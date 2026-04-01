import json
import os
import logging
import sys
import re
from datetime import datetime
from dotenv import load_dotenv

from typing import TypedDict, List
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END
from opentelemetry import trace

from service.webhook.k8s_client import get_pod_logs, get_pods_with_labels, execute_remediation, execute_remediation_sandboxed
from service.webhook.tracing import get_tracer
from service.webhook.cache import get_llm_cache
from service.webhook.llm_router import get_llm_router
from service.webhook.knowledge_base import get_knowledge_base, format_rag_context
from service.webhook.embeddings import get_embedding_generator, create_query_text

load_dotenv()

tracer = get_tracer()

# Configure logging
logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "30"))
logger.info(f"[CONFIG] Ollama model: {OLLAMA_MODEL} at {OLLAMA_BASE_URL} (timeout: {OLLAMA_TIMEOUT}s)")

# Programmatic deny-list for dangerous commands (checked before LLM)
DENY_PATTERNS = [
    r"rm\s+-rf\s+/",  # rm -rf / or variations
    r"rm\s+-rf\s+\*",  # rm -rf *
    r"kubectl\s+delete\s+namespace",  # Delete entire namespace
    r"kubectl\s+delete\s+.*--all-namespaces",  # Delete across all namespaces
    r"kubectl\s+delete\s+pod\s+--all",  # Delete all pods
    r"\bhalt\b",  # System halt
    r"\breboot\b",  # System reboot
    r"\bshutdown\b",  # System shutdown
    r"dd\s+if=.*of=/dev/",  # Direct disk writes
    r":(){.*};:",  # Fork bomb
    r"mkfs",  # Format filesystem
    r"fdisk",  # Disk partitioning
    r"\>/dev/sd",  # Write to disk device
]
logger.info(f"[CONFIG] Safety validation using {len(DENY_PATTERNS)} deny patterns")

# Pydantic Schemas for Structured Output
class RemediationPlan(BaseModel):
    analysis: str = Field(description="Analysis of the issue based on logs and metrics")
    script: str = Field(description="The proposed bash or kubectl remediation script")
    is_safe: bool = Field(description="Initial self-assessment of safety")

class SafetyValidation(BaseModel):
    approved: bool = Field(description="Whether the script is approved for execution")
    reasoning: str = Field(description="Reasoning for approval or denial")

# Helper function to fix LLM field name mismatches
def _fix_remediation_plan_fields(data: dict) -> dict:
    """Map common LLM field name variations to correct schema"""
    fixed = {}
    
    # Map 'analysis' field (common mistakes: analysis_script, analyze, description)
    fixed['analysis'] = (
        data.get('analysis') or 
        data.get('analysis_script') or 
        data.get('analyze') or 
        data.get('description') or 
        "No analysis provided"
    )
    
    # Map 'script' field (common mistakes: remediation_script, command, cmd)
    fixed['script'] = (
        data.get('script') or 
        data.get('remediation_script') or 
        data.get('command') or 
        data.get('cmd') or 
        "echo 'No script generated'"
    )
    
    # Map 'is_safe' field (common mistakes: safe, is_safe_to_execute, safety)
    fixed['is_safe'] = data.get('is_safe', data.get('safe', data.get('is_safe_to_execute', False)))
    
    return fixed

def _fix_safety_validation_fields(data: dict) -> dict:
    """Map common LLM field name variations to correct schema"""
    fixed = {}
    
    # Map 'approved' field (common mistakes: approved_status, is_approved, safe)
    fixed['approved'] = data.get('approved', data.get('approved_status', data.get('is_approved', data.get('safe', False))))
    
    # Map 'reasoning' field (common mistakes: reason, explanation, justification)
    fixed['reasoning'] = (
        data.get('reasoning') or 
        data.get('reason') or 
        data.get('explanation') or 
        data.get('justification') or 
        "No reasoning provided"
    )
    
    return fixed

# LangGraph State
class GraphState(TypedDict):
    alert_payload: dict
    logs: str
    remediation_plan: RemediationPlan
    safety_validation: SafetyValidation
    execution_result: str

# Retry Logic for LLM Calls
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((TimeoutError, ConnectionError, Exception)),
    reraise=True
)
def _invoke_llm_with_retry(llm, prompt: str, node_name: str):
    """Invoke LLM with exponential backoff retry logic."""
    logger.info(f"[{node_name.upper()}] Attempting LLM invocation (timeout={OLLAMA_TIMEOUT}s)...")
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
        logger.info(f"[{node_name.upper()}] LLM invocation successful")
        return response
    except TimeoutError as e:
        logger.error(f"[{node_name.upper()}] LLM timeout after {OLLAMA_TIMEOUT}s")
        raise
    except Exception as e:
        logger.error(f"[{node_name.upper()}] LLM invocation failed: {str(e)}")
        raise

# Node Functions
def parse_and_fetch_logs(state: GraphState) -> GraphState:
    """Fetch context from Kubernetes based on the alert."""
    node_start = datetime.now()
    logger.info(f"[NODE] Log Parser Started at {node_start}")

    with tracer.start_as_current_span("parser") as span:
        try:
            alert = state.get("alert_payload", {})
            logger.info(f"[ALERT] Processing alert with {len(alert)} top-level keys")

            if "alerts" in alert and len(alert["alerts"]) > 0:
                labels = alert["alerts"][0].get("labels", {})
                logger.info(f"[LABELS] Found {len(labels)} labels in first alert")
            else:
                labels = {}
                logger.warning("[WARNING] No alerts found in payload, using empty labels")

            namespace = labels.get("namespace", "default")
            alertname = labels.get("alertname", "unknown")
            span.set_attribute("alert.namespace", namespace)
            span.set_attribute("alert.name", alertname)
            logger.info(f"[NAMESPACE] Target namespace: {namespace}")

            logs = ""
            pods = get_pods_with_labels(namespace, "app=auto-remediation-service")
            span.set_attribute("k8s.pods_found", len(pods))

            if pods:
                pod_name = pods[0]
                span.set_attribute("k8s.pod_name", pod_name)
                logger.info(f"[PODS] Found {len(pods)} matching pods, using: {pod_name}")
                logs = get_pod_logs(namespace, pod_name, tail_lines=100)
                span.set_attribute("k8s.log_bytes", len(logs))
                logger.info(f"[LOGS] Fetched {len(logs)} bytes of logs from {pod_name}")
            else:
                logs = "No pods found to fetch logs from."
                span.set_attribute("k8s.pods_found", 0)
                logger.error("[ERROR] No pods found matching the label selector")

            node_duration = (datetime.now() - node_start).total_seconds()
            span.set_attribute("node.duration_seconds", node_duration)
            logger.info(f"[SUCCESS] [NODE] Log Parser completed in {node_duration:.2f} seconds")
            return {"logs": logs}

        except Exception as e:
            span.record_exception(e)
            span.set_status(trace.StatusCode.ERROR, str(e))
            logger.error(f"[ERROR] [NODE] Log Parser failed: {str(e)}")
            logger.exception("Log Parser full error traceback:")
            return {"logs": f"Error fetching logs: {str(e)}"}

def solver_node(state: GraphState) -> GraphState:
    """LLM Analyzes logs and suggests remediation."""
    node_start = datetime.now()
    logger.info(f"[NODE] Solver Engine Started at {node_start}")

    with tracer.start_as_current_span("solver") as span:
        try:
            alert = state.get("alert_payload", {})
            logs = state.get("logs", "")
            span.set_attribute("llm.model", OLLAMA_MODEL)
            span.set_attribute("input.log_chars", len(logs))

            # Extract alert context for caching
            alerts = alert.get("alerts", [])
            labels = alerts[0].get("labels", {}) if alerts else {}
            alert_type = labels.get("alertname", "unknown")
            namespace = labels.get("namespace", "default")
            
            cache_context = {
                "alert_type": alert_type,
                "namespace": namespace
            }

            # Check cache first
            llm_cache = get_llm_cache()
            cached_response = llm_cache.get_cached_response(
                alert_type=alert_type,
                logs=logs,
                context=cache_context
            )
            
            if cached_response:
                logger.info(f"[CACHE] Using cached LLM response for {alert_type}")
                span.set_attribute("cache.hit", True)
                
                # Reconstruct RemediationPlan from cached data
                plan = RemediationPlan(**cached_response)
                span.set_attribute("plan.is_safe", plan.is_safe)
                span.set_attribute("plan.script_chars", len(plan.script))
                
                node_duration = (datetime.now() - node_start).total_seconds()
                span.set_attribute("node.duration_seconds", node_duration)
                logger.info(f"[SUCCESS] [NODE] Solver Engine completed (cached) in {node_duration:.2f} seconds")
                return {"remediation_plan": plan}
            
            # Cache miss - call LLM with RAG enhancement
            span.set_attribute("cache.hit", False)
            logger.info(f"[AI] Initializing LLM Router with multi-model fallback...")
            
            # ─── RAG: Search Knowledge Base for Similar Cases ───────────────────
            rag_context = ""
            try:
                logger.info("[RAG] Searching knowledge base for similar historical cases...")
                kb = get_knowledge_base()
                embedding_gen = get_embedding_generator()
                
                # Create query text from current alert
                query_text = create_query_text(alert_type, logs)
                
                # Generate embedding for query
                query_embedding = embedding_gen.generate_embedding(query_text)
                
                # Search for similar cases
                similar_cases = kb.search_similar(
                    query_embedding=query_embedding,
                    alert_type=alert_type,
                    top_k=3,
                    success_only=True
                )
                
                if similar_cases:
                    rag_context = format_rag_context(similar_cases, max_cases=3)
                    span.set_attribute("rag.cases_found", len(similar_cases))
                    logger.info(f"[RAG] ✅ Found {len(similar_cases)} similar cases for context enhancement")
                else:
                    rag_context = "No similar historical cases found in knowledge base."
                    span.set_attribute("rag.cases_found", 0)
                    logger.info("[RAG] No similar cases found")
                    
            except Exception as e:
                logger.warning(f"[RAG] Failed to search knowledge base: {e}")
                rag_context = "Knowledge base unavailable."
                span.set_attribute("rag.error", str(e))
            
            # ─── LLM Invocation with RAG Context ─────────────────────────────────
            llm = get_llm_router()

            prompt = f"""
        You are an expert Kubernetes SRE. An alert has fired:
        {json.dumps(alert, indent=2)}

        Here are the recent logs from the affected pod:
        {logs[:2000]}

        {rag_context}

        Analyze the issue and propose a safe remediation script.
        Do NOT delete namespaces or entire deployments unless absolutely necessary.

        Return your response as JSON with EXACTLY these field names:
        {{
            "analysis": "your detailed analysis here",
            "script": "kubectl or bash command here",
            "is_safe": true or false
        }}

        CRITICAL: Use "analysis" NOT "analysis_script". Use "script" NOT "remediation_script".
        """

            logger.info("[AI] Sending prompt to Ollama LLM...")
            response = _invoke_llm_with_retry(llm, prompt, "solver")
            
            # Parse response and fix field names
            import json as json_module
            try:
                # Extract JSON from response
                response_text = response.content if hasattr(response, 'content') else str(response)
                logger.info(f"[DEBUG] LLM raw response: {response_text[:500]}")  # DEBUG
                response_data = json_module.loads(response_text)
                logger.info(f"[DEBUG] Parsed JSON keys: {list(response_data.keys())}")  # DEBUG
                
                # Fix common field name mistakes
                fixed_data = _fix_remediation_plan_fields(response_data)
                logger.info(f"[DEBUG] Fixed data keys: {list(fixed_data.keys())}")  # DEBUG
                
                # Now create Pydantic object with fixed fields
                plan = RemediationPlan(**fixed_data)
                logger.info(f"[SUCCESS] LLM response parsed and validated (fields fixed if needed)")
            except json_module.JSONDecodeError as json_error:
                logger.error(f"[FALLBACK] JSON parsing failed: {json_error}")
                logger.error(f"[FALLBACK] Raw response was: {response_text[:200]}")
                # If JSON parsing fails, create a safe default
                plan = RemediationPlan(
                    analysis=f"Unable to parse LLM JSON: {str(json_error)}",
                    script="echo 'LLM response parsing failed'",
                    is_safe=False
                )
            except Exception as parse_error:
                logger.error(f"[FALLBACK] Unexpected error parsing LLM response: {parse_error}")
                logger.exception("Full traceback:")
                # If JSON parsing fails, create a safe default
                plan = RemediationPlan(
                    analysis=f"Unable to parse LLM response: {str(parse_error)}",
                    script="echo 'LLM response parsing failed'",
                    is_safe=False
                )

            span.set_attribute("plan.is_safe", plan.is_safe)
            span.set_attribute("plan.script_chars", len(plan.script))
            logger.info(f"[ANALYSIS] {plan.analysis[:200]}{'...' if len(plan.analysis) > 200 else ''}")
            logger.info(f"[SAFETY] Initial self-assessment: {plan.is_safe}")
            
            # Cache the LLM response for future use
            cache_data = {
                "analysis": plan.analysis,
                "script": plan.script,
                "is_safe": plan.is_safe
            }
            llm_cache.set_cached_response(
                alert_type=alert_type,
                logs=logs,
                response=cache_data,
                context=cache_context
            )

            node_duration = (datetime.now() - node_start).total_seconds()
            span.set_attribute("node.duration_seconds", node_duration)
            logger.info(f"[SUCCESS] [NODE] Solver Engine completed in {node_duration:.2f} seconds")
            return {"remediation_plan": plan}

        except Exception as e:
            span.record_exception(e)
            span.set_status(trace.StatusCode.ERROR, str(e))
            logger.error(f"[ERROR] [NODE] Solver Engine failed: {str(e)}")
            logger.exception("Solver Engine full error traceback:")
            default_plan = RemediationPlan(
                analysis=f"Error during analysis: {str(e)}",
                script="echo 'Analysis failed, manual intervention required'",
                is_safe=False,
            )
            return {"remediation_plan": default_plan}

def safety_validation_node(state: GraphState) -> GraphState:
    """Safety LLM checks script against Deny-List."""
    node_start = datetime.now()
    logger.info(f"[NODE] Safety Validator (RBAC Checker) Started at {node_start}")

    with tracer.start_as_current_span("validator") as span:
        try:
            plan = state["remediation_plan"]
            span.set_attribute("script.chars", len(plan.script))
            
            # Pre-validation: Check script against regex deny patterns BEFORE LLM call
            logger.info("[SECURITY] Running programmatic pre-validation...")
            for pattern in DENY_PATTERNS:
                if re.search(pattern, plan.script, re.IGNORECASE):
                    matched_pattern = pattern
                    logger.error(f"[DENIED] Script matched deny pattern: {pattern}")
                    span.set_attribute("validation.approved", False)
                    span.set_attribute("validation.deny_pattern_matched", pattern)
                    validation = SafetyValidation(
                        approved=False,
                        reasoning=f"Script contains prohibited command pattern: {pattern}"
                    )
                    node_duration = (datetime.now() - node_start).total_seconds()
                    span.set_attribute("node.duration_seconds", node_duration)
                    logger.info(f"[DENIED] Pre-validation rejected script in {node_duration:.2f}s")
                    return {"safety_validation": validation}
            
            logger.info("[SECURITY] Pre-validation passed, proceeding to LLM validation...")
            span.set_attribute("validation.pre_check_passed", True)

            # Use LLMRouter with fallback chain: qwen2.5:3b → llama3.1:8b → GPT-4
            llm = get_llm_router()
            deny_list = ["rm -rf", "kubectl delete namespace", "kubectl delete pod --all", "halt", "reboot"]

            prompt = f"""
        You are an Automated DevSecOps Gatekeeper.
        Review the proposed remediation script:

        ```bash
        {plan.script}
        ```

        DENY the script if it contains highly destructive commands such as:
        {deny_list}
        or if it seems overly dangerous. APPROVE otherwise.

        Return your response as JSON with EXACTLY these field names:
        {{
            "approved": true or false,
            "reasoning": "your explanation here"
        }}

        CRITICAL: Use "approved" NOT "approved_status". Use "reasoning" NOT "reason".
        """

            logger.info("[AI] Sending script to safety validation LLM...")
            response = _invoke_llm_with_retry(llm, prompt, "validator")
            
            # Parse response and fix field names
            import json as json_module
            try:
                # Extract JSON from response
                response_text = response.content if hasattr(response, 'content') else str(response)
                response_data = json_module.loads(response_text)
                
                # Fix common field name mistakes
                fixed_data = _fix_safety_validation_fields(response_data)
                
                # Now create Pydantic object with fixed fields
                validation = SafetyValidation(**fixed_data)
                logger.info(f"[SUCCESS] LLM response parsed and validated (fields fixed if needed)")
            except Exception as parse_error:
                logger.warning(f"[FALLBACK] Failed to parse safety validation JSON: {parse_error}, denying by default")
                # If JSON parsing fails, deny for safety
                validation = SafetyValidation(
                    approved=False,
                    reasoning=f"Unable to parse safety validation response: {str(parse_error)}"
                )

            span.set_attribute("validation.approved", validation.approved)
            status_icon = "[APPROVED]" if validation.approved else "[DENIED]"
            logger.info(f"{status_icon} [VALIDATION] Approved: {validation.approved}")

            node_duration = (datetime.now() - node_start).total_seconds()
            span.set_attribute("node.duration_seconds", node_duration)
            logger.info(f"[SUCCESS] [NODE] Safety Validator completed in {node_duration:.2f} seconds")
            return {"safety_validation": validation}

        except Exception as e:
            span.record_exception(e)
            span.set_status(trace.StatusCode.ERROR, str(e))
            logger.error(f"[ERROR] [NODE] Safety Validator failed: {str(e)}")
            logger.exception("Safety Validator full error traceback:")
            default_validation = SafetyValidation(
                approved=False,
                reasoning=f"Safety validation failed with error: {str(e)}",
            )
            return {"safety_validation": default_validation}

def execute_remediation_node(state: GraphState) -> GraphState:
    """Execute the script if approved, else escalate."""
    node_start = datetime.now()
    logger.info(f"[NODE] Execution/Escalation Started at {node_start}")

    with tracer.start_as_current_span("execution") as span:
        try:
            validation = state["safety_validation"]
            plan = state["remediation_plan"]
            alert_payload = state.get("alert_payload", {})
            span.set_attribute("approved", validation.approved)

            # Extract context for sandboxed execution
            alerts = alert_payload.get("alerts", [])
            labels = alerts[0].get("labels", {}) if alerts else {}
            namespace = labels.get("namespace", "default")
            pod_name = labels.get("pod", "unknown")
            alert_type = labels.get("alertname", "custom")
            workflow_id = f"WF-{int(datetime.now().timestamp())}"

            if validation.approved:
                logger.info("[APPROVED] Script approved, proceeding with sandboxed execution...")
                # Use sandboxed execution in Kubernetes Job
                res = execute_remediation_sandboxed(
                    script=plan.script,
                    workflow_id=workflow_id,
                    namespace=namespace,
                    alert_type=alert_type,
                    pod_name=pod_name
                )
                span.set_attribute("execution.outcome", "executed")
                logger.info(f"[SUCCESS] [EXECUTION] Result: {res}")
                result = res
            else:
                msg = f"Escalated to human engineer. Script was denied for: {validation.reasoning}"
                span.set_attribute("execution.outcome", "escalated")
                logger.warning(f"[ESCALATION] {msg}")
                result = msg

            node_duration = (datetime.now() - node_start).total_seconds()
            span.set_attribute("node.duration_seconds", node_duration)
            logger.info(f"[SUCCESS] [NODE] Execution/Escalation completed in {node_duration:.2f} seconds")
            return {"execution_result": result}

        except Exception as e:
            span.record_exception(e)
            span.set_status(trace.StatusCode.ERROR, str(e))
            logger.error(f"[ERROR] [NODE] Execution/Escalation failed: {str(e)}")
            logger.exception("Execution/Escalation full error traceback:")
            return {"execution_result": f"Execution failed with error: {str(e)}"}

def build_graph():
    logger.info("[GRAPH] Building LangGraph state machine...")
    workflow = StateGraph(GraphState)
    
    # Add nodes
    logger.info("[GRAPH] Adding workflow nodes...")
    workflow.add_node("parser", parse_and_fetch_logs)
    workflow.add_node("solver", solver_node)
    workflow.add_node("validator", safety_validation_node)
    workflow.add_node("execution", execute_remediation_node)
    logger.info("[SUCCESS] 4 nodes added: parser, solver, validator, execution")
    
    # Edges
    logger.info("[GRAPH] Adding workflow edges...")
    workflow.add_edge(START, "parser")
    workflow.add_edge("parser", "solver")
    workflow.add_edge("solver", "validator")
    workflow.add_edge("validator", "execution")
    workflow.add_edge("execution", END)
    logger.info("[SUCCESS] Workflow edges configured: START -> parser -> solver -> validator -> execution -> END")
    
    compiled_graph = workflow.compile()
    logger.info("[SUCCESS] LangGraph workflow compiled successfully")
    return compiled_graph

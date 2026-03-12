import json
import os
import logging
import sys
from datetime import datetime
from dotenv import load_dotenv

from typing import TypedDict, List
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END

from k8s_client import get_pod_logs, get_pods_with_labels, execute_remediation

load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
logger.info(f"[CONFIG] GEMINI_API_KEY configured: {'Yes' if GEMINI_API_KEY else 'No'}")

# Pydantic Schemas for Structured Output
class RemediationPlan(BaseModel):
    analysis: str = Field(description="Analysis of the issue based on logs and metrics")
    script: str = Field(description="The proposed bash or kubectl remediation script")
    is_safe: bool = Field(description="Initial self-assessment of safety")

class SafetyValidation(BaseModel):
    approved: bool = Field(description="Whether the script is approved for execution")
    reasoning: str = Field(description="Reasoning for approval or denial")

# LangGraph State
class GraphState(TypedDict):
    alert_payload: dict
    logs: str
    remediation_plan: RemediationPlan
    safety_validation: SafetyValidation
    execution_result: str

# Node Functions
def parse_and_fetch_logs(state: GraphState) -> GraphState:
    """Fetch context from Kubernetes based on the alert."""
    node_start = datetime.now()
    logger.info(f"[NODE] Log Parser Started at {node_start}")
    
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
        logger.info(f"[NAMESPACE] Target namespace: {namespace}")
        
        logs = ""
        logger.info(f"[SEARCH] Searching for pods with label: app=auto-remediation-service")
        pods = get_pods_with_labels(namespace, "app=auto-remediation-service")
        
        if pods:
            pod_name = pods[0]
            logger.info(f"[PODS] Found {len(pods)} matching pods, using: {pod_name}")
            logs = get_pod_logs(namespace, pod_name, tail_lines=100)
            logger.info(f"[LOGS] Fetched {len(logs)} bytes of logs from {pod_name}")
            if len(logs) > 500:
                logger.debug(f"[LOGS] Last 500 chars of logs: ...{logs[-500:]}")
        else:
            logs = "No pods found to fetch logs from."
            logger.error("[ERROR] No pods found matching the label selector")
            
        node_duration = (datetime.now() - node_start).total_seconds()
        logger.info(f"[SUCCESS] [NODE] Log Parser completed in {node_duration:.2f} seconds")
        return {"logs": logs}
        
    except Exception as e:
        logger.error(f"[ERROR] [NODE] Log Parser failed: {str(e)}")
        logger.exception("Log Parser full error traceback:")
        return {"logs": f"Error fetching logs: {str(e)}"}
        pod_name = pods[0]
        logs = get_pod_logs(namespace, pod_name, tail_lines=100)
    else:
        logs = "No pods found to fetch logs from."
        
    print(f"Fetched {len(logs)} bytes of logs.")
    return {"logs": logs}

def solver_node(state: GraphState) -> GraphState:
    """LLM Analyzes logs and suggests remediation."""
    node_start = datetime.now()
    logger.info(f"[NODE] Solver Engine Started at {node_start}")
    
    try:
        alert = state.get("alert_payload", {})
        logs = state.get("logs", "")
        
        logger.info(f"[DATA] Processing alert with {len(str(alert))} chars")
        logger.info(f"[DATA] Processing logs with {len(logs)} chars")
        
        # We use Flash 2.5 for fast structured outputs
        logger.info("[AI] Initializing Gemini Flash 2.5 model...")
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0, google_api_key=GEMINI_API_KEY).with_structured_output(RemediationPlan)
        
        prompt = f"""
        You are an expert Kubernetes SRE. An alert has fired:
        {json.dumps(alert, indent=2)}
        
        Here are the recent logs from the affected pod:
        {logs[:2000]}
        
        Based on this, propose a safe remediation script (e.g. kubectl restart, config patch).
        Do NOT delete namespaces or entire deployments unless absolutely necessary.
        """
        
        logger.info("[AI] Sending prompt to Gemini Flash LLM...")
        plan = llm.invoke([HumanMessage(content=prompt)])
        
        logger.info(f"[ANALYSIS] {plan.analysis[:200]}{'...' if len(plan.analysis) > 200 else ''}")
        logger.info(f"[SCRIPT] {plan.script[:200]}{'...' if len(plan.script) > 200 else ''}")
        logger.info(f"[SAFETY] Initial self-assessment: {plan.is_safe}")
        
        node_duration = (datetime.now() - node_start).total_seconds()
        logger.info(f"[SUCCESS] [NODE] Solver Engine completed in {node_duration:.2f} seconds")
        return {"remediation_plan": plan}
        
    except Exception as e:
        logger.error(f"[ERROR] [NODE] Solver Engine failed: {str(e)}")
        logger.exception("Solver Engine full error traceback:")
        # Return a default safe plan
        default_plan = RemediationPlan(
            analysis=f"Error during analysis: {str(e)}",
            script="echo 'Analysis failed, manual intervention required'",
            is_safe=False
        )
        return {"remediation_plan": default_plan}

def safety_validation_node(state: GraphState) -> GraphState:
    """Safety LLM checks script against Deny-List."""
    node_start = datetime.now()
    logger.info(f"[NODE] Safety Validator (RBAC Checker) Started at {node_start}")
    
    try:
        plan = state["remediation_plan"]
        logger.info(f"[SCRIPT] Validating script with {len(plan.script)} characters")
        
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0, google_api_key=GEMINI_API_KEY).with_structured_output(SafetyValidation)
        
        deny_list = ["rm -rf", "kubectl delete namespace", "kubectl delete pod --all", "halt", "reboot"]
        logger.info(f"[DENY_LIST] Checking against {len(deny_list)} deny-list patterns: {deny_list}")
        
        prompt = f"""
        You are an Automated DevSecOps Gatekeeper. 
        Review the proposed remediation script:
        
        ```bash
        {plan.script}
        ```
        
        DENY the script if it contains highly destructive commands such as:
        {deny_list}
        or if it seems overly dangerous. APPROVE otherwise.
        """
        
        logger.info("[AI] Sending script to safety validation LLM...")
        validation = llm.invoke([HumanMessage(content=prompt)])
        
        status_icon = "[APPROVED]" if validation.approved else "[DENIED]"
        logger.info(f"{status_icon} [VALIDATION] Approved: {validation.approved}")
        logger.info(f"[REASONING] {validation.reasoning}")
        
        node_duration = (datetime.now() - node_start).total_seconds()
        logger.info(f"[SUCCESS] [NODE] Safety Validator completed in {node_duration:.2f} seconds")
        return {"safety_validation": validation}
        
    except Exception as e:
        logger.error(f"[ERROR] [NODE] Safety Validator failed: {str(e)}")
        logger.exception("Safety Validator full error traceback:")
        # Default to deny for safety
        default_validation = SafetyValidation(
            approved=False,
            reasoning=f"Safety validation failed with error: {str(e)}"
        )
        return {"safety_validation": default_validation}

def execute_remediation_node(state: GraphState) -> GraphState:
    """Execute the script if approved, else escalate."""
    node_start = datetime.now()
    logger.info(f"[NODE] Execution/Escalation Started at {node_start}")
    
    try:
        validation = state["safety_validation"]
        plan = state["remediation_plan"]
        
        logger.info(f"[STATUS] Script approval status: {validation.approved}")
        
        if validation.approved:
            logger.info("[APPROVED] Script approved, proceeding with execution...")
            logger.info(f"[EXECUTING] Executing: {plan.script[:100]}{'...' if len(plan.script) > 100 else ''}")
            res = execute_remediation(plan.script)
            logger.info(f"[SUCCESS] [EXECUTION] Result: {res}")
            result = res
        else:
            msg = f"Escalated to human engineer. Script was denied for: {validation.reasoning}"
            logger.warning(f"[ESCALATION] {msg}")
            result = msg
        
        node_duration = (datetime.now() - node_start).total_seconds()
        logger.info(f"[SUCCESS] [NODE] Execution/Escalation completed in {node_duration:.2f} seconds")
        return {"execution_result": result}
        
    except Exception as e:
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

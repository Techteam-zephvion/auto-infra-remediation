import json
import os
from dotenv import load_dotenv

from typing import TypedDict
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END

from k8s_client import get_pod_logs, get_pods_with_labels, execute_remediation
from prom_client import get_prometheus_metrics
from logger import get_logger

load_dotenv()

logger = get_logger("graph")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Pydantic Schemas for Structured Output
class RemediationPlan(BaseModel):
    analysis: str = Field(description="Analysis of the issue based on logs and metrics")
    script: str = Field(description="The proposed bash or kubectl remediation script")
    is_safe: bool = Field(description="Initial self-assessment of safety")

class SafetyValidation(BaseModel):
    approved: bool = Field(description="Whether the script is approved for execution")
    reasoning: str = Field(description="Reasoning for approval or denial")

# LangGraph State
class GraphState(TypedDict, total=False):
    alert_payload: dict
    logs: str
    metrics: str
    remediation_plan: RemediationPlan
    safety_validation: SafetyValidation
    execution_result: str


# Node Functions
def log_parser_node(state: GraphState) -> GraphState:
    """Fetch context from Kubernetes based on the alert."""
    alert = state.get("alert_payload", {})

    if "alerts" in alert and len(alert["alerts"]) > 0:
        labels = alert["alerts"][0].get("labels", {})
    else:
        labels = {}

    namespace = labels.get("namespace", "default")
    alertname = labels.get("alertname", "Unknown")

    logger.info(f"[PARSER] Fetching K8s context | alertname={alertname} | namespace={namespace}")

    logs = ""
    metrics = "No metrics fetched."
    pods = get_pods_with_labels(namespace, "app=auto-remediation-service")

    if pods:
        pod_name = pods[0]
        logger.info(f"[PARSER] Target pod identified: {pod_name}")
        logs = get_pod_logs(namespace, pod_name, tail_lines=100)
        metrics = get_prometheus_metrics(alertname, pod_name)
        logger.info(f"[PARSER] Fetched {len(logs)} bytes of logs. PromQL metrics fetched.")
    else:
        logger.warning(f"[PARSER] No pods found in namespace='{namespace}' with label 'app=auto-remediation-service'")
        logs = "No pods found to fetch logs from."

    return {"logs": logs, "metrics": metrics}


def solver_node(state: GraphState) -> GraphState:
    """LLM Analyzes logs and suggests remediation."""
    alert = state.get("alert_payload", {})
    logs = state.get("logs", "")
    metrics = state.get("metrics", "No metrics available.")

    logger.info(f"[SOLVER] Invoking LLM | model=gemini-2.0-flash | logs_len={len(logs)} | metrics_len={len(metrics)}")

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        temperature=0,
        google_api_key=GEMINI_API_KEY
    ).with_structured_output(RemediationPlan)

    prompt = f"""
    You are an expert Kubernetes SRE. An alert has fired:
    {json.dumps(alert, indent=2)}

    Here are the recent logs from the affected pod:
    {logs[:2000]}

    Here is the mathematical context from Prometheus metrics:
    {metrics}

    Based on this, propose a safe remediation script (e.g. kubectl restart, config patch).
    Do NOT delete namespaces or entire deployments unless absolutely necessary.
    """

    try:
        plan = llm.invoke([HumanMessage(content=prompt)])
        logger.info(f"[SOLVER] LLM responded | is_safe={plan.is_safe} | analysis_len={len(plan.analysis)}")
        logger.debug(f"[SOLVER] Proposed script:\n{plan.script}")
        return {"remediation_plan": plan}
    except Exception as e:
        logger.error(f"[SOLVER] ❌ LLM invocation failed: {e}", exc_info=True)
        raise


def safety_validation_node(state: GraphState) -> GraphState:
    """Safety LLM checks the remediation script against the deny-list."""
    plan = state["remediation_plan"]

    logger.info(f"[VALIDATOR] Running safety check | model=gemini-2.0-flash | script_len={len(plan.script)}")

    llm = ChatGoogleGenerativeAI(
        model="gemini-2.0-flash",
        temperature=0,
        google_api_key=GEMINI_API_KEY
    ).with_structured_output(SafetyValidation)

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
    """

    try:
        validation = llm.invoke([HumanMessage(content=prompt)])
        outcome = "APPROVED ✅" if validation.approved else "DENIED ❌"
        logger.info(f"[VALIDATOR] Validation result: {outcome} | reason={validation.reasoning}")
        return {"safety_validation": validation}
    except Exception as e:
        logger.error(f"[VALIDATOR] ❌ Safety LLM invocation failed: {e}", exc_info=True)
        raise


def execute_remediation_node(state: GraphState) -> GraphState:
    """Execute the script if approved, else escalate."""
    validation = state["safety_validation"]
    plan = state["remediation_plan"]

    if validation.approved:
        logger.info("[EXECUTION] ▶️  Executing approved remediation script...")
        res = execute_remediation(plan.script)
        logger.info(f"[EXECUTION] Script execution result: {res}")
        return {"execution_result": res}
    else:
        msg = f"Escalated to human engineer. Script denied: {validation.reasoning}"
        logger.warning(f"[EXECUTION] 🚫 Remediation DENIED — {msg}")
        return {"execution_result": msg}


def build_graph(memory_saver):
    logger.info("Compiling LangGraph state machine with nodes: parser → solver → validator → execution")
    workflow = StateGraph(GraphState)

    workflow.add_node("parser", log_parser_node)
    workflow.add_node("solver", solver_node)
    workflow.add_node("validator", safety_validation_node)
    workflow.add_node("execution", execute_remediation_node)

    workflow.add_edge(START, "parser")
    workflow.add_edge("parser", "solver")
    workflow.add_edge("solver", "validator")
    workflow.add_edge("validator", "execution")
    workflow.add_edge("execution", END)

    compiled = workflow.compile(checkpointer=memory_saver, interrupt_before=["execution"])
    logger.info("LangGraph compiled with HITL interrupt_before=['execution']")
    return compiled

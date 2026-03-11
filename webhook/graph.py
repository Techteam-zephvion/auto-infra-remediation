import json
import os
from dotenv import load_dotenv

from typing import TypedDict, List
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, START, END

from k8s_client import get_pod_logs, get_pods_with_labels, execute_remediation
from prom_client import get_prometheus_metrics

load_dotenv()

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
    print("\n--- [NODE] Log Parser ---")
    alert = state.get("alert_payload", {})
    
    if "alerts" in alert and len(alert["alerts"]) > 0:
        labels = alert["alerts"][0].get("labels", {})
    else:
        labels = {}
        
    namespace = labels.get("namespace", "default")
    alertname = labels.get("alertname", "Unknown")
    
    logs = ""
    metrics = "No metrics fetched."
    pods = get_pods_with_labels(namespace, "app=auto-remediation-service")
    if pods:
        pod_name = pods[0]
        logs = get_pod_logs(namespace, pod_name, tail_lines=100)
        metrics = get_prometheus_metrics(alertname, pod_name)
    else:
        logs = "No pods found to fetch logs from."
        
    print(f"Fetched {len(logs)} bytes of logs. Fetched PromQL metrics.")
    return {"logs": logs, "metrics": metrics}

def solver_node(state: GraphState) -> GraphState:
    """LLM Analyzes logs and suggests remediation."""
    print("\n--- [NODE] Solver Engine ---")
    alert = state.get("alert_payload", {})
    logs = state.get("logs", "")
    
    # We use Flash 2.5 for fast structured outputs
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0, google_api_key=GEMINI_API_KEY).with_structured_output(RemediationPlan)
    
    prompt = f"""
    You are an expert Kubernetes SRE. An alert has fired:
    {json.dumps(alert, indent=2)}
    
    Here are the recent logs from the affected pod:
    {logs[:2000]}
    
    Here is the mathematical context from Prometheus metrics:
    {state.get('metrics', 'No metrics available.')}
    
    Based on this, propose a safe remediation script (e.g. kubectl restart, config patch).
    Do NOT delete namespaces or entire deployments unless absolutely necessary.
    """
    
    plan = llm.invoke([HumanMessage(content=prompt)])
    print(f"Proposed Fix Analysis: {plan.analysis}")
    print(f"Proposed Script:\n{plan.script}")
    return {"remediation_plan": plan}

def safety_validation_node(state: GraphState) -> GraphState:
    """Safety LLM checks script against Deny-List."""
    print("\n--- [NODE] Safety Validator (RBAC Checker) ---")
    plan = state["remediation_plan"]
    
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0, google_api_key=GEMINI_API_KEY).with_structured_output(SafetyValidation)
    
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
    
    validation = llm.invoke([HumanMessage(content=prompt)])
    print(f"Validation Result - Approved: {validation.approved} | Reason: {validation.reasoning}")
    return {"safety_validation": validation}

def execute_remediation_node(state: GraphState) -> GraphState:
    """Execute the script if approved, else escalate."""
    print("\n--- [NODE] Execution/Escalation ---")
    validation = state["safety_validation"]
    plan = state["remediation_plan"]
    
    if validation.approved:
        res = execute_remediation(plan.script)
        return {"execution_result": res}
    else:
        msg = f"Escalated to human engineer. Script was denied for: {validation.reasoning}"
        print(msg)
        return {"execution_result": msg}

def build_graph(memory_saver):
    workflow = StateGraph(GraphState)
    
    # Add nodes
    workflow.add_node("parser", log_parser_node)
    workflow.add_node("solver", solver_node)
    workflow.add_node("validator", safety_validation_node)
    workflow.add_node("execution", execute_remediation_node)
    
    # Edges
    workflow.add_edge(START, "parser")
    workflow.add_edge("parser", "solver")
    workflow.add_edge("solver", "validator")
    workflow.add_edge("validator", "execution")
    workflow.add_edge("execution", END)
    
    return workflow.compile(checkpointer=memory_saver, interrupt_before=["execution"])

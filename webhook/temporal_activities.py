"""
Temporal Activities for Auto Remediation Pipeline
Each activity represents a unit of work that can be retried independently
"""

import os
import asyncio
import logging
from typing import Dict, Any
from datetime import datetime

from temporalio import activity
from dotenv import load_dotenv

# Import existing modules
import k8s_client
import database
from graph import (
    parse_and_fetch_logs as graph_parse_logs,
    solver_node as graph_solver,
    safety_validation_node as graph_safety_validator,
    execute_remediation_node as graph_executor,
    GraphState,
)

load_dotenv()
logger = logging.getLogger(__name__)


@activity.defn
async def parse_alert_and_fetch_logs(alert_payload: Dict[str, Any]) -> Dict[str, str]:
    """
    Activity 1: Parse alert and fetch Kubernetes pod logs
    
    Args:
        alert_payload: AlertManager webhook payload
    
    Returns:
        {"logs": str, "namespace": str, "pod": str}
    """
    activity.logger.info("[ACTIVITY] parse_alert_and_fetch_logs started")
    
    try:
        # Use existing graph function
        state: GraphState = {"alert_payload": alert_payload}
        result_state = graph_parse_logs(state)
        
        logs = result_state.get("logs", "")
        activity.logger.info(f"[ACTIVITY] Fetched {len(logs)} chars of logs")
        
        return {
            "logs": logs,
            "namespace": alert_payload.get("namespace", "default"),
            "pod": alert_payload.get("pod", "unknown"),
        }
    except Exception as e:
        activity.logger.error(f"[ACTIVITY] parse_alert_and_fetch_logs failed: {e}")
        raise


@activity.defn
async def analyze_issue_with_llm(input_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Activity 2: Analyze logs and generate remediation plan using LLM
    
    Args:
        input_data: {"alert_payload": dict, "logs": str}
    
    Returns:
        {"analysis": str, "script": str, "is_safe": bool}
    """
    activity.logger.info("[ACTIVITY] analyze_issue_with_llm started")
    
    try:
        # Use existing graph solver node
        state: GraphState = {
            "alert_payload": input_data["alert_payload"],
            "logs": input_data["logs"],
        }
        result_state = graph_solver(state)
        
        plan = result_state.get("remediation_plan")
        activity.logger.info(f"[ACTIVITY] LLM analysis complete: {plan.analysis[:100]}...")
        
        return {
            "analysis": plan.analysis,
            "script": plan.script,
            "is_safe": plan.is_safe,
        }
    except Exception as e:
        activity.logger.error(f"[ACTIVITY] analyze_issue_with_llm failed: {e}")
        # Return error state instead of raising
        return {
            "analysis": f"Error during analysis: {str(e)}",
            "script": "echo 'Analysis failed, manual intervention required'",
            "is_safe": False,
        }


@activity.defn
async def validate_script_safety(remediation_plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    Activity 3: Validate script safety using dual-layer validation
    
    Args:
        remediation_plan: {"analysis": str, "script": str, "is_safe": bool}
    
    Returns:
        {"approved": bool, "reasoning": str}
    """
    activity.logger.info("[ACTIVITY] validate_script_safety started")
    
    try:
        # Create a RemediationPlan-like object for the validator
        from graph import RemediationPlan
        plan = RemediationPlan(
            analysis=remediation_plan["analysis"],
            script=remediation_plan["script"],
            is_safe=remediation_plan["is_safe"],
        )
        
        # Use existing graph safety validator
        state: GraphState = {"remediation_plan": plan}
        result_state = graph_safety_validator(state)
        
        validation = result_state.get("safety_validation")
        activity.logger.info(
            f"[ACTIVITY] Safety validation: {validation.approved} - {validation.reasoning[:50]}..."
        )
        
        return {
            "approved": validation.approved,
            "reasoning": validation.reasoning,
        }
    except Exception as e:
        activity.logger.error(f"[ACTIVITY] validate_script_safety failed: {e}")
        return {
            "approved": False,
            "reasoning": f"Safety validation failed with error: {str(e)}",
        }


@activity.defn
async def execute_remediation(exec_input: Dict[str, Any]) -> Dict[str, str]:
    """
    Activity 4: Execute the approved remediation script
    
    Args:
        exec_input: {"script": str, "workflow_id": str}
    
    Returns:
        {"result": str, "success": bool}
    """
    activity.logger.info("[ACTIVITY] execute_remediation started")
    
    try:
        script = exec_input["script"]
        workflow_id = exec_input["workflow_id"]
        
        # Use existing graph executor
        from graph import RemediationPlan, SafetyValidation
        plan = RemediationPlan(analysis="", script=script, is_safe=True)
        validation = SafetyValidation(approved=True, reasoning="Approved by workflow")
        
        state: GraphState = {
            "remediation_plan": plan,
            "safety_validation": validation,
        }
        result_state = graph_executor(state)
        
        result = result_state.get("execution_result", "")
        activity.logger.info(f"[ACTIVITY] Execution result: {result[:100]}...")
        
        return {
            "result": result,
            "success": "successfully" in result.lower(),
        }
    except Exception as e:
        activity.logger.error(f"[ACTIVITY] execute_remediation failed: {e}")
        return {
            "result": f"Execution failed: {str(e)}",
            "success": False,
        }


@activity.defn
async def record_audit_event(audit_data: Dict[str, Any]) -> Dict[str, bool]:
    """
    Activity 5: Record workflow event in PostgreSQL audit trail
    
    Args:
        audit_data: {
            "workflow_id": str,
            "alert_type": str (optional),
            "status": str,
            "analysis": str (optional),
            "script": str (optional),
            "safety_approved": bool (optional),
            "safety_reasoning": str (optional),
            "execution_result": str (optional),
        }
    
    Returns:
        {"success": bool}
    """
    activity.logger.info(f"[ACTIVITY] record_audit_event: {audit_data.get('workflow_id')}")
    
    try:
        workflow_id = audit_data["workflow_id"]
        status = audit_data.get("status", "running")
        
        if status == "running":
            # Initial record
            await database.insert_audit_event(
                workflow_id=workflow_id,
                alert_type=audit_data.get("alert_type", "unknown"),
            )
        else:
            # Update existing record
            await database.update_audit_event(
                workflow_id=workflow_id,
                status=status,
                analysis=audit_data.get("analysis", ""),
                script=audit_data.get("script", ""),
                safety_approved=audit_data.get("safety_approved", False),
                safety_reasoning=audit_data.get("safety_reasoning", ""),
                execution_result=audit_data.get("execution_result", ""),
            )
        
        activity.logger.info(f"[ACTIVITY] Audit event recorded: {workflow_id}")
        return {"success": True}
        
    except Exception as e:
        activity.logger.error(f"[ACTIVITY] record_audit_event failed: {e}")
        # Don't raise - audit failures shouldn't stop the workflow
        return {"success": False}

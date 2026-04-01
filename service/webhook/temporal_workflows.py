"""
Temporal Workflows for Auto Remediation Pipeline
Provides exactly-once execution semantics and crash recovery
"""

import asyncio
from datetime import timedelta
from typing import Dict, Any
import logging

from temporalio import workflow
from temporalio.common import RetryPolicy

# Import activities (will be defined in temporal_activities.py)
with workflow.unsafe.imports_passed_through():
    from service.webhook.temporal_activities import (
        parse_alert_and_fetch_logs,
        analyze_issue_with_llm,
        validate_script_safety,
        execute_remediation,
        record_audit_event,
    )

logger = logging.getLogger(__name__)


@workflow.defn
class RemediationWorkflow:
    """
    Main remediation workflow with crash recovery and retry policies.
    Orchestrates the entire remediation pipeline using Temporal activities.
    """

    @workflow.run
    async def run(self, workflow_input: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the remediation workflow with the following steps:
        1. Parse alert and fetch Kubernetes logs
        2. Analyze issue with LLM (solver)
        3. Validate script safety (dual validation)
        4. Execute remediation (if approved)
        5. Record audit trail
        
        Args:
            workflow_input: {
                "workflow_id": str,
                "alert_payload": dict,
                "alert_type": str
            }
        
        Returns:
            {
                "status": "completed" | "failed",
                "workflow_id": str,
                "safety_approved": bool,
                "execution_result": str,
                "error": str (optional)
            }
        """
        workflow_id = workflow_input["workflow_id"]
        alert_payload = workflow_input["alert_payload"]
        alert_type = workflow_input["alert_type"]
        
        workflow.logger.info(f"[TEMPORAL] Starting remediation workflow: {workflow_id}")
        
        # Retry policy for all activities
        retry_policy = RetryPolicy(
            initial_interval=timedelta(seconds=1),
            backoff_coefficient=2.0,
            maximum_interval=timedelta(seconds=10),
            maximum_attempts=3,
        )
        
        result = {
            "status": "failed",
            "workflow_id": workflow_id,
            "safety_approved": False,
            "execution_result": "",
            "error": None,
        }
        
        try:
            # Activity 1: Record workflow start in audit trail
            await workflow.execute_activity(
                record_audit_event,
                args=[{
                    "workflow_id": workflow_id,
                    "alert_type": alert_type,
                    "status": "running",
                }],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_policy,
            )
            workflow.logger.info(f"[TEMPORAL] Audit event recorded: {workflow_id}")
            
            # Activity 2: Parse alert and fetch logs from Kubernetes
            logs_data = await workflow.execute_activity(
                parse_alert_and_fetch_logs,
                args=[alert_payload],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_policy,
            )
            workflow.logger.info(f"[TEMPORAL] Logs fetched: {len(logs_data.get('logs', ''))} chars")
            
            # Activity 3: Analyze issue with LLM (solver node)
            remediation_plan = await workflow.execute_activity(
                analyze_issue_with_llm,
                args=[{
                    "alert_payload": alert_payload,
                    "logs": logs_data["logs"],
                }],
                start_to_close_timeout=timedelta(seconds=60),  # LLM can take longer
                retry_policy=retry_policy,
            )
            workflow.logger.info(
                f"[TEMPORAL] Analysis complete: {remediation_plan['analysis'][:100]}..."
            )
            
            # Activity 4: Validate script safety
            safety_validation = await workflow.execute_activity(
                validate_script_safety,
                args=[remediation_plan],
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=retry_policy,
            )
            result["safety_approved"] = safety_validation["approved"]
            workflow.logger.info(
                f"[TEMPORAL] Safety validation: {safety_validation['approved']}"
            )
            
            # Activity 5: Execute remediation (only if approved)
            if safety_validation["approved"]:
                execution_result = await workflow.execute_activity(
                    execute_remediation,
                    args=[{
                        "script": remediation_plan["script"],
                        "workflow_id": workflow_id,
                    }],
                    start_to_close_timeout=timedelta(seconds=120),
                    retry_policy=retry_policy,
                )
                result["execution_result"] = execution_result["result"]
                workflow.logger.info(f"[TEMPORAL] Execution complete: {result['execution_result']}")
            else:
                result["execution_result"] = (
                    f"Escalated to human engineer. Script was denied for: "
                    f"{safety_validation['reasoning']}"
                )
                workflow.logger.warning(f"[TEMPORAL] Script denied: {safety_validation['reasoning']}")
            
            # Activity 6: Update audit trail with final result
            await workflow.execute_activity(
                record_audit_event,
                args=[{
                    "workflow_id": workflow_id,
                    "status": "completed",
                    "analysis": remediation_plan["analysis"],
                    "script": remediation_plan["script"],
                    "safety_approved": safety_validation["approved"],
                    "safety_reasoning": safety_validation["reasoning"],
                    "execution_result": result["execution_result"],
                }],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_policy,
            )
            
            result["status"] = "completed"
            workflow.logger.info(f"[TEMPORAL] Workflow completed: {workflow_id}")
            return result
            
        except Exception as e:
            error_msg = str(e)
            result["error"] = error_msg
            workflow.logger.error(f"[TEMPORAL] Workflow failed: {workflow_id} - {error_msg}")
            
            # Record failure in audit trail
            try:
                await workflow.execute_activity(
                    record_audit_event,
                    args=[{
                        "workflow_id": workflow_id,
                        "status": "failed",
                        "execution_result": f"Workflow error: {error_msg}",
                    }],
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=1),  # Don't retry on failure recording
                )
            except Exception as audit_error:
                workflow.logger.error(f"[TEMPORAL] Failed to record audit: {audit_error}")
            
            return result


@workflow.defn
class HealthCheckWorkflow:
    """Simple health check workflow to verify Temporal is working"""
    
    @workflow.run
    async def run(self) -> Dict[str, Any]:
        workflow.logger.info("[TEMPORAL] Health check workflow executed")
        return {
            "status": "healthy",
            "message": "Temporal workflow engine is operational",
        }

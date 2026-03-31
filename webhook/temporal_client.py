"""
Temporal Client Helper for API Integration
Provides easy interface to trigger workflows from FastAPI
"""

import os
import logging
from typing import Dict, Any, Optional
from datetime import timedelta

from temporalio.client import Client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Configuration
TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "auto-remediation-queue")
TEMPORAL_ENABLED = os.getenv("TEMPORAL_ENABLED", "true").lower() == "true"

# Global client instance
_temporal_client: Optional[Client] = None


async def get_temporal_client() -> Optional[Client]:
    """
    Get or create Temporal client connection
    
    Returns:
        Client instance or None if Temporal is disabled or unavailable
    """
    global _temporal_client
    
    if not TEMPORAL_ENABLED:
        logger.info("[TEMPORAL] Disabled via TEMPORAL_ENABLED=false")
        return None
    
    if _temporal_client is None:
        try:
            logger.info(f"[TEMPORAL] Connecting to {TEMPORAL_HOST}...")
            _temporal_client = await Client.connect(
                TEMPORAL_HOST,
                namespace=TEMPORAL_NAMESPACE,
            )
            logger.info(f"[TEMPORAL] Connected successfully to namespace: {TEMPORAL_NAMESPACE}")
        except Exception as e:
            logger.error(f"[TEMPORAL] Failed to connect: {e}")
            logger.warning("[TEMPORAL] Will fall back to direct graph execution")
            return None
    
    return _temporal_client


async def start_remediation_workflow(
    workflow_id: str,
    alert_payload: Dict[str, Any],
    alert_type: str
) -> Dict[str, Any]:
    """
    Start a remediation workflow in Temporal
    
    Args:
        workflow_id: Unique workflow identifier
        alert_payload: AlertManager webhook payload
        alert_type: Type of alert (cpu_spike, memory_leak, etc.)
    
    Returns:
        {
            "status": "accepted" | "failed",
            "workflow_id": str,
            "temporal_enabled": bool,
            "run_id": str (if Temporal is used),
            "error": str (if failed)
        }
    """
    client = await get_temporal_client()
    
    if client is None:
        logger.warning(f"[TEMPORAL] Client unavailable for workflow {workflow_id}, using direct execution")
        return {
            "status": "accepted",
            "workflow_id": workflow_id,
            "temporal_enabled": False,
            "fallback_mode": "direct_graph_execution",
        }
    
    try:
        from temporal_workflows import RemediationWorkflow
        
        workflow_input = {
            "workflow_id": workflow_id,
            "alert_payload": alert_payload,
            "alert_type": alert_type,
        }
        
        # Start workflow (async, non-blocking)
        handle = await client.start_workflow(
            RemediationWorkflow.run,
            workflow_input,
            id=workflow_id,
            task_queue=TASK_QUEUE,
            execution_timeout=timedelta(minutes=10),  # Max workflow duration
        )
        
        logger.info(f"[TEMPORAL] Workflow started: {workflow_id} (run_id: {handle.first_execution_run_id})")
        
        return {
            "status": "accepted",
            "workflow_id": workflow_id,
            "temporal_enabled": True,
            "run_id": handle.first_execution_run_id,
            "task_queue": TASK_QUEUE,
        }
        
    except Exception as e:
        logger.error(f"[TEMPORAL] Failed to start workflow {workflow_id}: {e}")
        return {
            "status": "failed",
            "workflow_id": workflow_id,
            "temporal_enabled": True,
            "error": str(e),
        }


async def get_workflow_status(workflow_id: str) -> Optional[Dict[str, Any]]:
    """
    Get the status of a running workflow
    
    Args:
        workflow_id: Workflow identifier
    
    Returns:
        Workflow status dict or None if not found
    """
    client = await get_temporal_client()
    
    if client is None:
        return None
    
    try:
        from temporal_workflows import RemediationWorkflow
        
        handle = client.get_workflow_handle(workflow_id)
        result = await handle.result()
        
        return {
            "workflow_id": workflow_id,
            "status": result.get("status", "unknown"),
            "result": result,
        }
        
    except Exception as e:
        logger.error(f"[TEMPORAL] Failed to get workflow status for {workflow_id}: {e}")
        return None


async def check_temporal_health() -> Dict[str, Any]:
    """
    Check if Temporal is accessible and healthy
    
    Returns:
        {"status": "healthy" | "unhealthy", "connected": bool, "error": str}
    """
    try:
        client = await get_temporal_client()
        
        if client is None:
            return {
                "status": "unavailable",
                "connected": False,
                "error": "Temporal is disabled or unreachable",
            }
        
        # Try to execute a simple health check workflow
        from temporal_workflows import HealthCheckWorkflow
        
        handle = await client.start_workflow(
            HealthCheckWorkflow.run,
            id=f"health-check-{os.urandom(4).hex()}",
            task_queue=TASK_QUEUE,
            execution_timeout=timedelta(seconds=10),
        )
        
        result = await handle.result()
        
        return {
            "status": "healthy",
            "connected": True,
            "message": result.get("message", ""),
            "host": TEMPORAL_HOST,
            "namespace": TEMPORAL_NAMESPACE,
        }
        
    except Exception as e:
        logger.error(f"[TEMPORAL] Health check failed: {e}")
        return {
            "status": "unhealthy",
            "connected": False,
            "error": str(e),
        }


async def close_temporal_client():
    """
    Close the Temporal client connection
    
    Note: Temporal Python SDK Client doesn't require explicit close
    as connections are managed automatically. This is a no-op for compatibility.
    """
    global _temporal_client
    
    if _temporal_client is not None:
        logger.info("[TEMPORAL] Cleaning up client reference...")
        # Temporal Client doesn't have a close() method in SDK 1.6.0
        # Connections are managed automatically by the SDK
        _temporal_client = None
        logger.info("[TEMPORAL] Client reference cleared")

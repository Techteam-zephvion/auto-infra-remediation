"""
Temporal Worker Service for Auto Remediation
Runs workflows and activities in a persistent worker process
"""

import asyncio
import logging
import os
from dotenv import load_dotenv

from temporalio.client import Client
from temporalio.worker import Worker

# Import workflows and activities
from service.webhook.temporal_workflows import RemediationWorkflow, HealthCheckWorkflow
from service.webhook.temporal_activities import (
    parse_alert_and_fetch_logs,
    analyze_issue_with_llm,
    validate_script_safety,
    execute_remediation,
    record_audit_event,
)

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Configuration
TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "auto-remediation-queue")


async def main():
    """
    Start the Temporal worker to process workflows and activities
    """
    logger.info(f"[WORKER] Connecting to Temporal server at {TEMPORAL_HOST}...")
    
    # Connect to Temporal
    client = await Client.connect(
        TEMPORAL_HOST,
        namespace=TEMPORAL_NAMESPACE,
    )
    logger.info(f"[WORKER] Connected to Temporal namespace: {TEMPORAL_NAMESPACE}")
    
    # Initialize dependencies
    logger.info("[WORKER] Initializing Kubernetes client...")
    try:
        import service.webhook.k8s_client as k8s_client
        k8s_client.init_k8s()
    except Exception as e:
        logger.warning(f"[WORKER] Kubernetes client unavailable (no cluster config): {e}. Remediation will run in simulation mode.")
    
    logger.info("[WORKER] Initializing database connection...")
    import service.webhook.database as database
    await database.setup_audit_table()
    
    # Create worker with workflows and activities
    logger.info(f"[WORKER] Starting worker on task queue: {TASK_QUEUE}")
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[RemediationWorkflow, HealthCheckWorkflow],
        activities=[
            parse_alert_and_fetch_logs,
            analyze_issue_with_llm,
            validate_script_safety,
            execute_remediation,
            record_audit_event,
        ],
    )
    
    logger.info("[WORKER] Worker started successfully. Listening for workflows...")
    logger.info("[WORKER] Press Ctrl+C to stop")
    
    # Run worker until interrupted
    await worker.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n[WORKER] Shutting down gracefully...")
    except Exception as e:
        logger.error(f"[WORKER] Fatal error: {e}")
        logger.exception("Full traceback:")
        raise

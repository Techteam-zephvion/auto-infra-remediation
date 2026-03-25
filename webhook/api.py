from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import asyncio
import logging
import sys
from datetime import datetime
from graph import build_graph

# Configure comprehensive logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('auto_remediation.log')
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Auto-Remediation Webhook API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

graph_app = build_graph()

# In-memory remediation history (max 50 entries)
remediation_history = []

# Predefined test alert payloads
TEST_ALERTS = {
    "cpu_spike": {
        "receiver": "auto-remediation-webhook",
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {
                "alertname": "HighCPUUsage",
                "namespace": "default",
                "severity": "critical",
                "app": "auto-remediation-service"
            },
            "annotations": {
                "summary": "CPU usage is above 90%",
                "description": "Pod is experiencing sustained high CPU usage above threshold"
            }
        }]
    },
    "memory_leak": {
        "receiver": "auto-remediation-webhook",
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {
                "alertname": "HighMemoryUsage",
                "namespace": "default",
                "severity": "warning",
                "app": "auto-remediation-service"
            },
            "annotations": {
                "summary": "Memory usage exceeds 80%",
                "description": "Pod memory consumption has grown abnormally, possible memory leak"
            }
        }]
    },
    "error_rate": {
        "receiver": "auto-remediation-webhook",
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {
                "alertname": "HighErrorRate",
                "namespace": "default",
                "severity": "critical",
                "app": "auto-remediation-service"
            },
            "annotations": {
                "summary": "HTTP error rate above 5%",
                "description": "Service is returning elevated 5xx errors"
            }
        }]
    }
}

logger.info("[STARTUP] Auto-Remediation Webhook API Starting Up...")
logger.info(f"[STARTUP] Startup Time: {datetime.now()}")


class AlertPayload(BaseModel):
    pass


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "auto-infra-remediation", "timestamp": datetime.now().isoformat()}


@app.get("/remediations")
async def get_remediations():
    """Return remediation history, newest first."""
    return list(reversed(remediation_history))


@app.post("/test-alert")
async def trigger_test_alert(body: dict):
    """Trigger a predefined test alert by type: cpu_spike | memory_leak | error_rate"""
    alert_type = body.get("alert_type", "cpu_spike")
    payload = TEST_ALERTS.get(alert_type, TEST_ALERTS["cpu_spike"])
    logger.info(f"[TEST] Triggering test alert: {alert_type}")
    task = asyncio.create_task(run_remediation_workflow(payload, alert_type=alert_type))
    logger.info(f"[TEST] Workflow task created: {task}")
    return {"status": "accepted", "alert_type": alert_type, "message": f"Test alert '{alert_type}' workflow started."}


@app.post("/alert")
async def receive_alert(request: Request):
    start_time = datetime.now()
    logger.info(f"[ALERT] Alert Received from Alertmanager at {start_time}")

    try:
        payload = await request.json()
        logger.info(f"[PAYLOAD] Alert Payload Size: {len(str(payload))} characters")
        task = asyncio.create_task(run_remediation_workflow(payload))
        logger.info(f"[WORKFLOW] Remediation workflow task created: {task}")

        processing_time = (datetime.now() - start_time).total_seconds()
        logger.info(f"[TIMING] Alert processing time: {processing_time:.2f} seconds")

        return {"status": "accepted", "message": "Remediation workflow started.", "timestamp": start_time.isoformat()}
    except Exception as e:
        logger.error(f"[ERROR] Error processing alert: {str(e)}")
        logger.exception("Full error traceback:")
        return {"status": "error", "message": f"Failed to process alert: {str(e)}"}


async def run_remediation_workflow(alert_payload: dict, alert_type: str = "custom"):
    workflow_start = datetime.now()
    workflow_id = f"WF-{int(workflow_start.timestamp())}"
    logger.info(f"[WORKFLOW {workflow_id}] Starting LangGraph Workflow at {workflow_start}")

    record = {
        "id": workflow_id,
        "timestamp": workflow_start.isoformat(),
        "alert_type": alert_type,
        "status": "running",
        "analysis": "",
        "script": "",
        "safety_approved": None,
        "safety_reasoning": "",
        "execution_result": ""
    }
    remediation_history.append(record)
    if len(remediation_history) > 50:
        remediation_history.pop(0)

    try:
        initial_state = {"alert_payload": alert_payload}
        step_count = 0

        for event in graph_app.stream(initial_state):
            step_count += 1
            node_name = list(event.keys())[0]
            node_state = event[node_name]
            logger.info(f"[WORKFLOW {workflow_id}] Step {step_count} - Node: {node_name}")

            if node_name == "solver" and "remediation_plan" in node_state:
                plan = node_state["remediation_plan"]
                record["analysis"] = plan.analysis
                record["script"] = plan.script
            elif node_name == "validator" and "safety_validation" in node_state:
                val = node_state["safety_validation"]
                record["safety_approved"] = val.approved
                record["safety_reasoning"] = val.reasoning
            elif node_name == "execution" and "execution_result" in node_state:
                record["execution_result"] = node_state["execution_result"]

        workflow_duration = (datetime.now() - workflow_start).total_seconds()
        record["status"] = "completed"
        record["duration_seconds"] = round(workflow_duration, 2)
        logger.info(f"[SUCCESS] [WORKFLOW {workflow_id}] Complete in {workflow_duration:.2f}s ({step_count} steps)")

    except Exception as e:
        logger.error(f"[ERROR] [WORKFLOW {workflow_id}] Workflow failed: {str(e)}")
        logger.exception(f"[WORKFLOW {workflow_id}] Full workflow error traceback:")
        record["status"] = "failed"
        record["execution_result"] = f"Error: {str(e)}"


if __name__ == "__main__":
    logger.info("[SERVER] Starting FastAPI server on 0.0.0.0:8001")
    uvicorn.run("api:app", host="0.0.0.0", port=8001, reload=True, log_level="info")

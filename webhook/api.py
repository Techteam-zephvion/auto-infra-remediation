from fastapi import FastAPI, Request
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
graph_app = build_graph()

# Log application startup
logger.info("[STARTUP] Auto-Remediation Webhook API Starting Up...")
logger.info(f"[STARTUP] Startup Time: {datetime.now()}")

class AlertPayload(BaseModel):
    # Depending on Prometheus Alertmanager
    # We will just accept it as a loose dict to be tolerant
    pass

@app.post("/alert")
async def receive_alert(request: Request):
    start_time = datetime.now()
    logger.info(f"[ALERT] Alert Received from Alertmanager at {start_time}")
    
    try:
        payload = await request.json()
        logger.info(f"[PAYLOAD] Alert Payload Size: {len(str(payload))} characters")
        logger.debug(f"[PAYLOAD] Full Alert Payload: {payload}")
        
        # We kick off the LangGraph workflow in the background
        # Since we want to return a quick 200 OK to Alertmanager
        task = asyncio.create_task(run_remediation_workflow(payload))
        logger.info(f"[WORKFLOW] Remediation workflow task created: {task}")
        
        processing_time = (datetime.now() - start_time).total_seconds()
        logger.info(f"[TIMING] Alert processing time: {processing_time:.2f} seconds")
        
        return {"status": "accepted", "message": "Remediation workflow started.", "timestamp": start_time.isoformat()}
    except Exception as e:
        logger.error(f"[ERROR] Error processing alert: {str(e)}")
        logger.exception("Full error traceback:")
        return {"status": "error", "message": f"Failed to process alert: {str(e)}"}

async def run_remediation_workflow(alert_payload: dict):
    workflow_start = datetime.now()
    workflow_id = f"WF-{int(workflow_start.timestamp())}"
    logger.info(f"[WORKFLOW {workflow_id}] Starting LangGraph Workflow at {workflow_start}")
    
    try:
        # Alertmanager groupings might send grouped alerts. We process the payload.
        initial_state = {"alert_payload": alert_payload}
        logger.info(f"[WORKFLOW {workflow_id}] Initial state prepared")
        
        step_count = 0
        for event in graph_app.stream(initial_state):
            step_count += 1
            logger.info(f"[WORKFLOW {workflow_id}] Step {step_count} completed: {list(event.keys())}")
            logger.debug(f"[WORKFLOW {workflow_id}] Step {step_count} details: {event}")
        
        workflow_duration = (datetime.now() - workflow_start).total_seconds()
        logger.info(f"[SUCCESS] [WORKFLOW {workflow_id}] Workflow Complete in {workflow_duration:.2f} seconds ({step_count} steps)")
        
    except Exception as e:
        logger.error(f"[ERROR] [WORKFLOW {workflow_id}] Workflow failed: {str(e)}")
        logger.exception(f"[WORKFLOW {workflow_id}] Full workflow error traceback:")

if __name__ == "__main__":
    logger.info("[SERVER] Starting FastAPI server on 0.0.0.0:8000")
    logger.info("[SERVER] Reload enabled for development")
    logger.info("[SERVER] Webhook endpoint available at: http://0.0.0.0:8000/alert")
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True, log_level="info")

import os
import time
import uuid
import hashlib
import sqlite3
import asyncio

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

from graph import build_graph
from langgraph.checkpoint.sqlite import SqliteSaver
from logger import get_logger

logger = get_logger("api")

# Simple memory cache for deduping: { "fingerprint": timestamp }
active_alerts = {}
DEBOUNCE_WINDOW_SECONDS = 120

# Initialize Database for Audit Logging
logger.info("Connecting to SQLite audit log database: audit_logs.db")
conn = sqlite3.connect("audit_logs.db", check_same_thread=False)
memory_saver = SqliteSaver(conn)

app = FastAPI(title="Auto-Remediation Webhook API")

# Setup CORS for Frontend Dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict this to the frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger.info("Building LangGraph state machine...")
graph_app = build_graph(memory_saver)
logger.info("LangGraph state machine ready.")


class AlertPayload(BaseModel):
    pass


@app.post("/alert")
async def receive_alert(request: Request):
    start_time = time.time()
    payload = await request.json()
    status = payload.get("status", "unknown")
    alertname = "Unknown"

    if "alerts" in payload and payload["alerts"]:
        alertname = payload["alerts"][0].get("labels", {}).get("alertname", "Unknown")

    logger.info(f"📨 Webhook received | status={status} | alertname={alertname}")

    # API Optimization 1: Ignore Resolved Alerts
    if status == "resolved":
        logger.info(f"🟢 Ignoring 'resolved' webhook. alertname={alertname}, skipping LLM.")
        return {"status": "ignored", "message": "Resolved alerts do not trigger remediation."}

    # API Optimization 2: Debouncing / Deduplication
    group_key = payload.get("groupKey", str(payload))
    fingerprint = hashlib.md5(group_key.encode()).hexdigest()

    now = time.time()
    if fingerprint in active_alerts and (now - active_alerts[fingerprint] < DEBOUNCE_WINDOW_SECONDS):
        age = int(now - active_alerts[fingerprint])
        logger.warning(f"🔕 Debounced duplicate webhook. fingerprint={fingerprint} age={age}s < {DEBOUNCE_WINDOW_SECONDS}s window")
        return {"status": "ignored", "message": "Alert debounced."}

    active_alerts[fingerprint] = now

    thread_id = str(uuid.uuid4())
    logger.info(f"🔔 Alert ACCEPTED | alertname={alertname} | fingerprint={fingerprint} | thread_id={thread_id}")

    asyncio.create_task(run_remediation_workflow(payload, thread_id))

    elapsed_ms = int((time.time() - start_time) * 1000)
    logger.info(f"✅ Webhook accepted in {elapsed_ms}ms. thread_id={thread_id}")
    return {"status": "accepted", "message": "Remediation workflow started.", "thread_id": thread_id}


async def run_remediation_workflow(alert_payload: dict, thread_id: str):
    initial_state = {"alert_payload": alert_payload}
    config = {"configurable": {"thread_id": thread_id}}

    logger.info(f"🚀 [thread={thread_id}] Starting LangGraph workflow...")

    try:
        for event in graph_app.stream(initial_state, config):
            node_name = list(event.keys())[0] if event else "?"
            logger.debug(f"[thread={thread_id}] Completed node: {node_name}")
    except Exception as e:
        logger.error(f"[thread={thread_id}] ❌ LangGraph stream error: {e}", exc_info=True)
        return

    # Check if we hit a breakpoint
    try:
        snapshot = graph_app.get_state(config)
        if snapshot.next and "execution" in snapshot.next:
            logger.info(f"⏸️  [thread={thread_id}] WORKFLOW PAUSED — awaiting human approval")
            logger.info(f"    → Approve via: POST /approve/{thread_id}")
        else:
            logger.info(f"✅ [thread={thread_id}] Workflow complete, no pending approval.")
    except Exception as e:
        logger.error(f"[thread={thread_id}] ❌ Error fetching state snapshot: {e}", exc_info=True)


@app.post("/approve/{thread_id}")
async def approve_workflow(thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}

    snapshot = graph_app.get_state(config)
    if not snapshot.next or "execution" not in snapshot.next:
        logger.warning(f"[thread={thread_id}] Approval attempted but no pending execution found.")
        return {"error": "No pending execution found for this thread ID."}

    logger.info(f"▶️  [thread={thread_id}] HUMAN APPROVED — resuming workflow...")

    try:
        for event in graph_app.stream(None, config):
            node_name = list(event.keys())[0] if event else "?"
            logger.debug(f"[thread={thread_id}] Resumed node: {node_name}")
    except Exception as e:
        logger.error(f"[thread={thread_id}] ❌ Error during resumed execution: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

    logger.info(f"✅ [thread={thread_id}] Approved workflow complete.")
    return {"status": "success", "message": f"Thread {thread_id} execution approved and completed."}


# --- Frontend Dashboard API Endpoints ---

@app.get("/threads")
def list_threads():
    """Returns a list of all thread IDs currently stored in the SQLite Audit Log."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id DESC")
        threads = [row[0] for row in cur.fetchall()]
        logger.info(f"GET /threads → returning {len(threads)} thread(s)")
        return {"threads": threads}
    except Exception as e:
        logger.error(f"GET /threads failed: {e}", exc_info=True)
        return {"error": f"Failed to fetch threads: {str(e)}"}


@app.get("/threads/{thread_id}")
def get_thread_state(thread_id: str):
    """Returns the comprehensive AI state (logs, metrics, LLM plan) for a specific thread."""
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = graph_app.get_state(config)
        if hasattr(snapshot, 'values') and snapshot.values:
            values = snapshot.values

            rem_plan = values.get('remediation_plan')
            if rem_plan and hasattr(rem_plan, 'model_dump'):
                values['remediation_plan'] = rem_plan.model_dump()

            safety_val = values.get('safety_validation')
            if safety_val and hasattr(safety_val, 'model_dump'):
                values['safety_validation'] = safety_val.model_dump()

            status = "pending_approval" if snapshot.next and "execution" in snapshot.next else "completed"
            logger.info(f"GET /threads/{thread_id} → status={status}")
            return {"thread_id": thread_id, "status": status, "state": values}
        else:
            logger.warning(f"GET /threads/{thread_id} → not found or no state")
            return {"error": f"Thread {thread_id} not found or has no state."}
    except Exception as e:
        logger.error(f"GET /threads/{thread_id} failed: {e}", exc_info=True)
        return {"error": f"Failed to fetch state: {str(e)}"}


if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)

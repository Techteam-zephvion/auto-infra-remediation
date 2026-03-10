from fastapi import FastAPI, Request
from pydantic import BaseModel
import uvicorn
import asyncio
import uuid
import time
import sqlite3
from graph import build_graph
from langgraph.checkpoint.sqlite import SqliteSaver

# Simple memory cache for deduping: { "fingerprint": timestamp }
active_alerts = {}
DEBOUNCE_WINDOW_SECONDS = 120

# Initialize Database for Audit Logging
conn = sqlite3.connect("audit_logs.db", check_same_thread=False)
memory_saver = SqliteSaver(conn)

app = FastAPI(title="Auto-Remediation Webhook API")
graph_app = build_graph(memory_saver)

class AlertPayload(BaseModel):
    # Depending on Prometheus Alertmanager
    # We will just accept it as a loose dict to be tolerant
    pass

@app.post("/alert")
async def receive_alert(request: Request):
    payload = await request.json()
    status = payload.get("status", "unknown")
    
    # API Optimization 1: Ignore Resolved Alerts
    if status == "resolved":
        print("🟢 Ignored 'resolved' webhook from Alertmanager. Skipping LLM execution.")
        return {"status": "ignored", "message": "Resolved alerts do not trigger remediation."}
        
    # API Optimization 2: Debouncing / Deduplication
    group_key = payload.get("groupKey", str(payload))
    fingerprint = hashlib.md5(group_key.encode()).hexdigest()
    
    now = time.time()
    if fingerprint in active_alerts and (now - active_alerts[fingerprint] < DEBOUNCE_WINDOW_SECONDS):
        print(f"🔕 Debounced duplicate alert webhook (Fingerprint: {fingerprint}).")
        return {"status": "ignored", "message": "Alert debounced."}
            
    active_alerts[fingerprint] = now
    
    print(f"🔔 Alert Received from Alertmanager! (Fingerprint: {fingerprint})")
    
    # We kick off the LangGraph workflow in the background
    # Since we want to return a quick 200 OK to Alertmanager
    thread_id = str(uuid.uuid4())
    asyncio.create_task(run_remediation_workflow(payload, thread_id))
    
    return {"status": "accepted", "message": "Remediation workflow started.", "thread_id": thread_id}

async def run_remediation_workflow(alert_payload: dict, thread_id: str):
    # Alertmanager groupings might send grouped alerts. We process the payload.
    initial_state = {"alert_payload": alert_payload}
    config = {"configurable": {"thread_id": thread_id}}
    
    print(f"🚀 Triggering LangGraph Workflow [Thread: {thread_id}]...")
    
    for event in graph_app.stream(initial_state, config):
        pass # All printing is done within the nodes

    # Check if we hit a breakpoint
    snapshot = graph_app.get_state(config)
    if snapshot.next and "execution" in snapshot.next:
        print(f"\n⏸️ WORKFLOW PAUSED: Human approval required for thread '{thread_id}'.")
        print(f"To approve, send POST to /approve/{thread_id}")
    else:
        print(f"\n✅ Workflow Complete [Thread: {thread_id}].")

@app.post("/approve/{thread_id}")
async def approve_workflow(thread_id: str):
    config = {"configurable": {"thread_id": thread_id}}
    
    snapshot = graph_app.get_state(config)
    if not snapshot.next or "execution" not in snapshot.next:
        return {"error": "No pending execution found for this thread ID."}
        
    print(f"\n▶️ RESUMING WORKFLOW [Thread: {thread_id}]...")
    
    # Resume the graph with no new state injection, just execute the continued nodes
    for event in graph_app.stream(None, config):
        pass
        
    print(f"✅ Workflow Complete [Thread: {thread_id}].")
    return {"status": "success", "message": f"Thread {thread_id} execution approved and completed."}

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)

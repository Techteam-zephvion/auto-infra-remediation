from fastapi import FastAPI, Request
from pydantic import BaseModel
import uvicorn
import asyncio
from graph import build_graph

app = FastAPI(title="Auto-Remediation Webhook API")
graph_app = build_graph()

class AlertPayload(BaseModel):
    # Depending on Prometheus Alertmanager
    # We will just accept it as a loose dict to be tolerant
    pass

@app.post("/alert")
async def receive_alert(request: Request):
    payload = await request.json()
    print("🔔 Alert Received from Alertmanager!")
    
    # We kick off the LangGraph workflow in the background
    # Since we want to return a quick 200 OK to Alertmanager
    asyncio.create_task(run_remediation_workflow(payload))
    
    return {"status": "accepted", "message": "Remediation workflow started."}

async def run_remediation_workflow(alert_payload: dict):
    # Alertmanager groupings might send grouped alerts. We process the payload.
    initial_state = {"alert_payload": alert_payload}
    print("🚀 Triggering LangGraph Workflow...")
    
    for event in graph_app.stream(initial_state):
        pass # All printing is done within the nodes

    print("✅ Workflow Complete.")

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)

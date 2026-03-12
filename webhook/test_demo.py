# Demo Test Script for Auto-Remediation Pipeline
import requests
import json
import time

# Simulate an alert from AlertManager
test_alert = {
    "version": "4",
    "groupKey": "{}:{alertname=\"HighCPUUsage\"}",
    "status": "firing",
    "receiver": "webhook-solver",
    "groupLabels": {
        "alertname": "HighCPUUsage"
    },
    "commonLabels": {
        "alertname": "HighCPUUsage",
        "namespace": "default",
        "severity": "critical"
    },
    "commonAnnotations": {
        "description": "CPU usage is above 80% for more than 5 minutes",
        "summary": "High CPU usage detected"
    },
    "externalURL": "http://localhost:9093",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "HighCPUUsage",
                "namespace": "default",
                "pod": "auto-remediation-service-123",
                "severity": "critical"
            },
            "annotations": {
                "description": "CPU usage is above 80% for more than 5 minutes",
                "summary": "High CPU usage detected on pod auto-remediation-service-123"
            },
            "startsAt": "2026-03-11T19:00:00.000Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "http://localhost:9090"
        }
    ]
}

def test_webhook():
    webhook_url = "http://localhost:8000/alert"
    
    print("[DEMO] Testing Auto-Remediation Webhook...")
    print(f"[DEMO] Sending alert to: {webhook_url}")
    print(f"[DEMO] Alert payload size: {len(json.dumps(test_alert))} chars")
    
    try:
        response = requests.post(
            webhook_url,
            json=test_alert,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        print(f"[DEMO] Response Status: {response.status_code}")
        print(f"[DEMO] Response: {response.json()}")
        
        if response.status_code == 200:
            print("[SUCCESS] Webhook accepted the alert!")
            print("[DEMO] Check the webhook logs for workflow execution details.")
        else:
            print(f"[ERROR] Webhook returned error: {response.status_code}")
            
    except requests.exceptions.ConnectionError:
        print("[ERROR] Could not connect to webhook. Is the server running on port 8000?")
    except Exception as e:
        print(f"[ERROR] Test failed: {str(e)}")

if __name__ == "__main__":
    test_webhook()
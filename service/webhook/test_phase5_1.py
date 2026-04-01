"""
Quick test script to verify Phase 5.1 alert tuning and notifications integration
Run this after starting the services to test the full flow
"""
import asyncio
import httpx
import json
from datetime import datetime


async def test_alert_tuning():
    """Test alert tuning with different severity levels"""
    print("\n[TEST] Testing Alert Tuning Integration")
    print("=" * 60)
    
    # Test alerts with different severities
    test_cases = [
        {
            "name": "High CPU - Critical Severity (should auto-remediate + notify)",
            "alert_type": "cpu_spike",
            "severity": "critical",
            "payload": {
                "receiver": "auto-remediation-webhook",
                "status": "firing",
                "alerts": [{
                    "status": "firing",
                    "labels": {
                        "alertname": "HighCPUUsage",
                        "namespace": "production",
                        "severity": "critical",
                        "app": "web-server",
                        "pod": "web-server-abc123"
                    },
                    "annotations": {
                        "summary": "CPU usage is above 90%",
                        "description": "Pod is experiencing sustained high CPU usage",
                    },
                }],
            }
        },
        {
            "name": "Disk Space Low - Medium Severity (notify only, no auto-remediate)",
            "alert_type": "disk_space_low",
            "severity": "medium",
            "payload": {
                "receiver": "auto-remediation-webhook",
                "status": "firing",
                "alerts": [{
                    "status": "firing",
                    "labels": {
                        "alertname": "DiskSpaceLow",
                        "namespace": "production",
                        "severity": "medium",
                        "app": "database",
                        "pod": "postgres-xyz789"
                    },
                    "annotations": {
                        "summary": "Disk usage is above 90%",
                        "description": "Volume is running out of space",
                    },
                }],
            }
        },
        {
            "name": "Memory Leak - Critical (PagerDuty escalation)",
            "alert_type": "memory_leak",
            "severity": "critical",
            "payload": {
                "receiver": "auto-remediation-webhook",
                "status": "firing",
                "alerts": [{
                    "status": "firing",
                    "labels": {
                        "alertname": "HighMemoryUsage",
                        "namespace": "production",
                        "severity": "critical",
                        "app": "cache-service",
                        "pod": "redis-master-999"
                    },
                    "annotations": {
                        "summary": "Memory usage exceeds 85%",
                        "description": "Pod memory consumption growing abnormally",
                    },
                }],
            }
        }
    ]
    
    base_url = "http://localhost:8001"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Check if API is running
        try:
            health = await client.get(f"{base_url}/health")
            print(f"\n✓ API Health Check: {health.status_code}")
        except Exception as e:
            print(f"\n✗ API not running: {e}")
            print("Please start the API first: cd webhook && python api.py")
            return
        
        # Run test cases
        for i, test_case in enumerate(test_cases, 1):
            print(f"\n\n[TEST {i}] {test_case['name']}")
            print("-" * 60)
            print(f"Alert Type: {test_case['alert_type']}")
            print(f"Severity: {test_case['severity']}")
            
            try:
                response = await client.post(
                    f"{base_url}/alert",
                    json=test_case['payload'],
                    headers={"Content-Type": "application/json"}
                )
                
                result = response.json()
                print(f"\nResponse Status: {response.status_code}")
                print(f"Response: {json.dumps(result, indent=2)}")
                
                if response.status_code == 200:
                    print("✓ Alert accepted")
                else:
                    print("✗ Alert failed")
                
                # Wait a bit between tests
                await asyncio.sleep(2)
                
            except Exception as e:
                print(f"✗ Test failed: {e}")
    
    print("\n\n[RESULTS] Test completed!")
    print("=" * 60)
    print("\nCheck the following:")
    print("1. API logs (auto_remediation.log) for alert tuning decisions")
    print("2. Notification outcomes (Slack webhooks, PagerDuty calls)")
    print("3. Escalation actions taken for each severity level")
    print("\nExpected behavior:")
    print("- CPU spike (critical): Auto-remediate + Slack notification")
    print("- Disk space (medium): Slack notification only")
    print("- Memory leak (critical): Auto-remediate + Slack + PagerDuty")


if __name__ == "__main__":
    asyncio.run(test_alert_tuning())

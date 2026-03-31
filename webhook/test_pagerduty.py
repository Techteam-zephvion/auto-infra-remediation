"""
PagerDuty Attack Test - Test critical alerts that create PagerDuty incidents
"""
import asyncio
import httpx
import json


async def test_pagerduty_incident():
    """Test PagerDuty incident creation for critical alerts"""
    print("\n" + "="*70)
    print("  🚨 PAGERDUTY ATTACK TEST 🚨")
    print("="*70)
    
    # Critical alerts that should create PagerDuty incidents
    test_cases = [
        {
            "name": "Memory Leak - Critical (PagerDuty + Slack)",
            "alert": {
                "receiver": "auto-remediation-webhook",
                "status": "firing",
                "alerts": [{
                    "status": "firing",
                    "labels": {
                        "alertname": "HighMemoryUsage",
                        "namespace": "production",
                        "severity": "critical",
                        "app": "payment-service",
                        "pod": "payment-service-xyz789"
                    },
                    "annotations": {
                        "summary": "Memory usage exceeds 90%",
                        "description": "Critical memory leak detected in payment service",
                    },
                }],
            }
        },
        {
            "name": "Database Connection Pool Exhausted - Critical (PagerDuty + Slack)",
            "alert": {
                "receiver": "auto-remediation-webhook",
                "status": "firing",
                "alerts": [{
                    "status": "firing",
                    "labels": {
                        "alertname": "DatabaseConnectionPoolExhausted",
                        "namespace": "production",
                        "severity": "critical",
                        "app": "api-gateway",
                        "pod": "api-gateway-abc123"
                    },
                    "annotations": {
                        "summary": "Database connection pool at 98%",
                        "description": "Database connections exhausted, service degraded",
                    },
                }],
            }
        },
        {
            "name": "Service Unavailable - Critical (All Escalations)",
            "alert": {
                "receiver": "auto-remediation-webhook",
                "status": "firing",
                "alerts": [{
                    "status": "firing",
                    "labels": {
                        "alertname": "ServiceUnavailable",
                        "namespace": "production",
                        "severity": "critical",
                        "app": "checkout-service",
                        "pod": "checkout-service-999"
                    },
                    "annotations": {
                        "summary": "Service completely unavailable",
                        "description": "Checkout service returning 503 errors",
                    },
                }],
            }
        }
    ]
    
    base_url = "http://localhost:8001"
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        # Check API health
        try:
            health = await client.get(f"{base_url}/health")
            print(f"\n✓ API Health Check: {health.status_code}")
        except Exception as e:
            print(f"\n✗ API not running: {e}")
            print("Please start the API first!")
            return
        
        # Run test cases
        for i, test_case in enumerate(test_cases, 1):
            print(f"\n\n{'='*70}")
            print(f"[TEST {i}] {test_case['name']}")
            print("="*70)
            
            try:
                response = await client.post(
                    f"{base_url}/alert",
                    json=test_case['alert'],
                    headers={"Content-Type": "application/json"}
                )
                
                result = response.json()
                print(f"\nResponse Status: {response.status_code}")
                print(f"Response: {json.dumps(result, indent=2)}")
                
                if response.status_code == 200:
                    print("✓ Alert accepted")
                else:
                    print("✗ Alert failed")
                
                # Wait for workflow to complete
                print("\n⏳ Waiting for workflow to complete (30s)...")
                await asyncio.sleep(30)
                
            except Exception as e:
                print(f"✗ Test failed: {e}")
    
    print("\n\n" + "="*70)
    print("  TEST COMPLETED!")
    print("="*70)
    print("\n📊 Check the following:")
    print("1. 📱 Slack: Check for colored notification messages")
    print("2. 🔔 PagerDuty: Check your dashboard for NEW INCIDENTS")
    print("3. 📝 Logs: auto_remediation.log for notification results")
    print("\n💡 Expected PagerDuty Incidents:")
    print("   - Memory Leak (payment-service)")
    print("   - Database Pool Exhausted (api-gateway)")
    print("   - Service Unavailable (checkout-service)")
    print("\n🎯 PagerDuty Integration Key:", end=" ")
    import os
    from dotenv import load_dotenv
    load_dotenv()
    key = os.getenv("PAGERDUTY_INTEGRATION_KEY", "NOT_SET")
    if key and key != "YOUR_KEY":
        print(f"{key[:20]}...")
    else:
        print("❌ NOT CONFIGURED!")
    print()


if __name__ == "__main__":
    asyncio.run(test_pagerduty_incident())

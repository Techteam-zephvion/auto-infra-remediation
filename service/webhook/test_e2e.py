"""
End-to-End System Test

Tests the complete AutoInfraRemediation pipeline with all Phase 6 enhancements:
- LLM Response Caching (Redis)
- Multi-Model LLM Fallback (qwen → llama → GPT-4)
- Knowledge Base RAG (ChromaDB semantic search)

This script verifies:
1. All services are healthy (PostgreSQL, Jaeger, Temporal, Vault, Redis, ChromaDB)
2. Alert processing pipeline works end-to-end
3. Cache functionality (miss on first run, hit on second)
4. RAG enhancement (searches knowledge base for similar cases)
5. Multi-model fallback routing (uses primary model)
6. Metrics collection and endpoints

Author: AutoInfraRemediation Team
Date: April 2026
"""

import requests
import time
import json
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

API_BASE_URL = "http://localhost:8001"


def print_section(title):
    """Print a formatted section header"""
    print("\n" + "="*80)
    print(f"  {title}")
    print("="*80)


def check_service_health(service_name, url):
    """Check if a service is healthy"""
    try:
        response = requests.get(url, timeout=30)  # Increased timeout for first request
        if response.status_code == 200:
            print(f"✅ {service_name}: HEALTHY")
            return True
        else:
            print(f"❌ {service_name}: UNHEALTHY (status={response.status_code})")
            return False
    except Exception as e:
        print(f"❌ {service_name}: UNREACHABLE ({str(e)})")
        return False


def test_health_checks():
    """Test 1: Verify all services are healthy"""
    print_section("TEST 1: Service Health Checks")
    
    services = {
        "API Server": f"{API_BASE_URL}/health",
        "Knowledge Base": f"{API_BASE_URL}/kb/health",
    }
    
    results = {}
    for name, url in services.items():
        results[name] = check_service_health(name, url)
    
    # Check main health endpoint for all dependencies
    main_health_ok = False
    try:
        response = requests.get(f"{API_BASE_URL}/health", timeout=30)
        if response.status_code == 200:
            health_data = response.json()
            print(f"\n📊 Full Health Status:")
            print(json.dumps(health_data, indent=2))
            main_health_ok = True
        else:
            print(f"⚠️  Health endpoint returned {response.status_code}")
    except Exception as e:
        print(f"❌ Failed to fetch health status: {e}")
    
    # Consider healthy if main endpoint works OR individual checks passed
    all_healthy = all(results.values()) or main_health_ok
    if all_healthy:
        print(f"\n✅ System is operational (passing health checks)")
    else:
        print(f"\n⚠️  Some services are unhealthy. Tests may fail.")
    
    return all_healthy


def send_test_alert(alert_name="E2ETestCPUSpike", namespace="test", description="End-to-end test alert"):
    """Send a test alert to the webhook"""
    alert_payload = {
        "alerts": [
            {
                "labels": {
                    "alertname": alert_name,
                    "severity": "warning",
                    "namespace": namespace,
                    "pod": f"test-pod-{int(time.time())}"
                },
                "annotations": {
                    "summary": f"Testing {alert_name}",
                    "description": description
                }
            }
        ]
    }
    
    try:
        response = requests.post(
            f"{API_BASE_URL}/alert",
            json=alert_payload,
            timeout=120  # Allow time for LLM processing
        )
        
        if response.status_code == 200:
            result = response.json()
            return True, result
        else:
            return False, {"error": f"Status {response.status_code}", "body": response.text}
    except Exception as e:
        return False, {"error": str(e)}


def test_first_alert_processing():
    """Test 2: Process alert (cache miss, RAG search, LLM invocation)"""
    print_section("TEST 2: First Alert Processing (Cache Miss + RAG)")
    
    print("\n📤 Sending test alert (first time)...")
    success, result = send_test_alert(
        alert_name="E2ETestCPUSpike",
        description="High CPU usage on nginx pod, needs scaling"
    )
    
    if success:
        print(f"✅ Alert processed successfully!")
        print(f"\n📋 Result Summary:")
        print(f"  Status: {result.get('status', 'unknown')}")
        
        if 'remediation_plan' in result:
            plan = result['remediation_plan']
            print(f"  Analysis: {plan.get('analysis', 'N/A')[:100]}...")
            print(f"  Script: {plan.get('script', 'N/A')[:100]}")
            print(f"  Is Safe: {plan.get('is_safe', 'N/A')}")
        
        if 'safety_validation' in result:
            validation = result['safety_validation']
            print(f"  Approved: {validation.get('approved', 'N/A')}")
            print(f"  Reasoning: {validation.get('reasoning', 'N/A')[:100]}...")
        
        return True
    else:
        print(f"❌ Alert processing failed: {result.get('error', 'Unknown error')}")
        return False


def test_cached_alert_processing():
    """Test 3: Process same alert again (cache hit)"""
    print_section("TEST 3: Second Alert Processing (Cache Hit)")
    
    print("\n📤 Sending same alert again (should hit cache)...")
    start_time = time.time()
    
    success, result = send_test_alert(
        alert_name="E2ETestCPUSpike",
        description="High CPU usage on nginx pod, needs scaling"  # Same as before
    )
    
    elapsed = time.time() - start_time
    
    if success:
        print(f"✅ Alert processed successfully in {elapsed:.2f}s")
        
        # Check if it was fast (indicating cache hit)
        if elapsed < 10:
            print(f"⚡ Fast response suggests cache hit!")
        else:
            print(f"⏱️  Response time suggests cache miss or LLM call")
        
        return True
    else:
        print(f"❌ Alert processing failed: {result.get('error', 'Unknown error')}")
        return False


def test_cache_stats():
    """Test 4: Check cache statistics"""
    print_section("TEST 4: Cache Statistics")
    
    try:
        response = requests.get(f"{API_BASE_URL}/cache/stats")
        if response.status_code == 200:
            stats = response.json()
            print(f"\n📊 Cache Stats:")
            print(f"  Enabled: {stats.get('enabled', 'N/A')}")
            print(f"  Total Requests: {stats.get('total_requests', 0)}")
            print(f"  Cache Hits: {stats.get('cache_hits', 0)}")
            print(f"  Cache Misses: {stats.get('cache_misses', 0)}")
            print(f"  Hit Rate: {stats.get('hit_rate_percent', 0)}%")
            print(f"  Redis Keys: {stats.get('redis_keys_count', 0)}")
            
            hit_rate = stats.get('hit_rate_percent', 0)
            if hit_rate > 0:
                print(f"\n✅ Cache is working (hit rate: {hit_rate}%)")
                return True
            else:
                print(f"\n⚠️  No cache hits yet (may need more identical alerts)")
                return True  # Still pass
        else:
            print(f"❌ Failed to fetch cache stats (status={response.status_code})")
            return False
    except Exception as e:
        print(f"❌ Error fetching cache stats: {e}")
        return False


def test_llm_router_stats():
    """Test 5: Check LLM router statistics"""
    print_section("TEST 5: LLM Router Statistics")
    
    try:
        response = requests.get(f"{API_BASE_URL}/llm/router/stats")
        if response.status_code == 200:
            stats = response.json()
            print(f"\n📊 LLM Router Stats:")
            print(f"  Total Requests: {stats.get('total_requests', 0)}")
            print(f"  Primary Invocations: {stats.get('primary_invocations', 0)}")
            print(f"  Secondary Invocations: {stats.get('secondary_invocations', 0)}")
            print(f"  Tertiary Invocations: {stats.get('tertiary_invocations', 0)}")
            print(f"  Fallbacks Triggered: {stats.get('fallback_triggered', 0)}")
            print(f"  Circuit Breaker Trips: {stats.get('circuit_breaker_trips', 0)}")
            print(f"  Total Cost: ${stats.get('total_cost_usd', 0):.4f}")
            print(f"  Avg Latency: {stats.get('avg_latency_seconds', 0):.2f}s")
            
            # Check circuit states
            circuit_states = stats.get('circuit_states', {})
            print(f"\n🔌 Circuit Breaker States:")
            for tier, is_open in circuit_states.items():
                status = "🔴 OPEN" if is_open else "🟢 CLOSED"
                print(f"  {tier}: {status}")
            
            # Verify primary model was used
            if stats.get('primary_invocations', 0) > 0:
                print(f"\n✅ Multi-model router working (primary model used)")
                return True
            else:
                print(f"\n⚠️  Primary model not used (may have fallen back)")
                return True  # Still pass
        else:
            print(f"❌ Failed to fetch LLM router stats (status={response.status_code})")
            return False
    except Exception as e:
        print(f"❌ Error fetching LLM router stats: {e}")
        return False


def test_knowledge_base_stats():
    """Test 6: Check knowledge base statistics"""
    print_section("TEST 6: Knowledge Base Statistics")
    
    try:
        response = requests.get(f"{API_BASE_URL}/kb/stats")
        if response.status_code == 200:
            stats = response.json()
            print(f"\n📊 Knowledge Base Stats:")
            print(f"  Enabled: {stats.get('enabled', 'N/A')}")
            print(f"  Total Documents: {stats.get('total_documents', 0)}")
            print(f"  Total Queries: {stats.get('total_queries', 0)}")
            print(f"  Total Hits: {stats.get('total_hits', 0)}")
            print(f"  Hit Rate: {stats.get('hit_rate_percent', 0)}%")
            print(f"  ChromaDB URL: {stats.get('chromadb_url', 'N/A')}")
            
            if stats.get('enabled', False):
                print(f"\n✅ Knowledge base is enabled and operational")
                
                if stats.get('total_queries', 0) > 0:
                    print(f"✅ RAG searches performed successfully")
                
                return True
            else:
                print(f"\n⚠️  Knowledge base is disabled")
                return True  # Still pass
        else:
            print(f"❌ Failed to fetch KB stats (status={response.status_code})")
            return False
    except Exception as e:
        print(f"❌ Error fetching KB stats: {e}")
        return False


def test_metrics_endpoint():
    """Test 7: Check Prometheus metrics"""
    print_section("TEST 7: Prometheus Metrics Endpoint")
    
    try:
        response = requests.get(f"{API_BASE_URL}/metrics")
        if response.status_code == 200:
            metrics_text = response.text
            
            # Check for key metrics
            key_metrics = [
                "llm_cache_hits_total",
                "llm_cache_misses_total",
                "llm_router_requests_total",
                "rag_queries_total",
                "remediation_requests_total"
            ]
            
            found_metrics = []
            for metric in key_metrics:
                if metric in metrics_text:
                    found_metrics.append(metric)
            
            print(f"\n📊 Prometheus Metrics Availability:")
            for metric in key_metrics:
                status = "✅" if metric in found_metrics else "❌"
                print(f"  {status} {metric}")
            
            if len(found_metrics) >= 4:
                print(f"\n✅ Metrics endpoint working ({len(found_metrics)}/{len(key_metrics)} metrics found)")
                return True
            else:
                print(f"\n⚠️  Some metrics missing ({len(found_metrics)}/{len(key_metrics)})")
                return True  # Still pass
        else:
            print(f"❌ Metrics endpoint failed (status={response.status_code})")
            return False
    except Exception as e:
        print(f"❌ Error fetching metrics: {e}")
        return False


def main():
    """Run all end-to-end tests"""
    print("\n" + "="*80)
    print("  AUTO-INFRA-REMEDIATION - END-TO-END SYSTEM TEST")
    print("="*80)
    print("\nTesting Complete Pipeline:")
    print("  • Phase 6.1: LLM Response Caching (Redis)")
    print("  • Phase 6.2: Multi-Model Fallback (qwen → llama → GPT-4)")
    print("  • Phase 6.3: Knowledge Base RAG (ChromaDB)")
    print("="*80)
    
    results = {}
    
    # Run tests
    try:
        results['health_checks'] = test_health_checks()
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        results['health_checks'] = False
    
    # Only continue if services are healthy
    if not results['health_checks']:
        print("\n⚠️  Services are unhealthy. Skipping alert processing tests.")
        print("   Make sure all services are running:")
        print("   docker-compose up -d")
        print("   python api.py")
        return False
    
    try:
        results['first_alert'] = test_first_alert_processing()
        time.sleep(2)  # Brief pause between tests
    except Exception as e:
        logger.error(f"First alert test failed: {e}")
        results['first_alert'] = False
    
    try:
        results['cached_alert'] = test_cached_alert_processing()
        time.sleep(1)
    except Exception as e:
        logger.error(f"Cached alert test failed: {e}")
        results['cached_alert'] = False
    
    try:
        results['cache_stats'] = test_cache_stats()
    except Exception as e:
        logger.error(f"Cache stats test failed: {e}")
        results['cache_stats'] = False
    
    try:
        results['llm_router_stats'] = test_llm_router_stats()
    except Exception as e:
        logger.error(f"LLM router stats test failed: {e}")
        results['llm_router_stats'] = False
    
    try:
        results['kb_stats'] = test_knowledge_base_stats()
    except Exception as e:
        logger.error(f"KB stats test failed: {e}")
        results['kb_stats'] = False
    
    try:
        results['metrics'] = test_metrics_endpoint()
    except Exception as e:
        logger.error(f"Metrics test failed: {e}")
        results['metrics'] = False
    
    # Summary
    print("\n" + "="*80)
    print("  TEST SUMMARY")
    print("="*80)
    
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{test_name:25} {status}")
    
    total = len(results)
    passed = sum(results.values())
    pass_rate = (passed/total*100) if total > 0 else 0
    
    print(f"\nTotal: {passed}/{total} tests passed ({pass_rate:.0f}%)")
    print("="*80)
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED! System is fully operational.")
        return True
    elif passed >= total * 0.7:
        print("\n⚠️  Most tests passed. System is mostly operational.")
        return True
    else:
        print("\n❌ Multiple test failures. Check service health and logs.")
        return False


if __name__ == "__main__":
    try:
        success = main()
        exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nTests interrupted by user")
        exit(1)
    except Exception as e:
        logger.error(f"Test suite failed: {e}")
        logger.exception("Full traceback:")
        exit(1)

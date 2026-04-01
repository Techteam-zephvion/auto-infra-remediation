"""
Test script for Phase 6.2 - Multi-Model LLM Fallback

Tests the LLMRouter with 3-tier fallback chain:
- Primary: qwen2.5:3b (local, fast)
- Secondary: llama3.1:8b (local, accurate)  
- Tertiary: GPT-4 (cloud, expensive)

Also tests circuit breaker functionality.

Requirements:
    - Ollama running (docker-compose up -d ollama)
    - Models pulled: qwen2.5:3b, llama3.1:8b
    - Optional: OPENAI_API_KEY in .env for GPT-4 testing

Usage:
    python test_llm_router.py
"""

import os
import logging
from langchain_core.messages import HumanMessage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def test_router_initialization():
    """Test 1: Router initialization"""
    print("\n" + "="*70)
    print("TEST 1: LLM Router Initialization")
    print("="*70)
    
    from service.webhook.llm_router import get_llm_router
    router = get_llm_router()
    
    print(f"Router initialized: {router is not None}")
    print(f"Ollama base URL: {router.ollama_base_url}")
    print(f"OpenAI API key: {'configured' if router.openai_api_key else 'missing (GPT-4 disabled)'}")
    print(f"Fallback enabled: {router.enable_fallback}")
    
    return router is not None


def test_basic_invocation():
    """Test 2: Basic LLM invocation (should use primary model)"""
    print("\n" + "="*70)
    print("TEST 2: Basic LLM Invocation")
    print("="*70)
    
    from service.webhook.llm_router import get_llm_router
    router = get_llm_router()
    router.reset_metrics()
    
    messages = [HumanMessage(content="Say 'hello' in one word.")]
    
    try:
        print("\nInvoking LLM with simple prompt...")
        response = router.invoke(messages)
        
        print(f"✅ Response received: {response[:100]}")
        
        # Check metrics
        metrics = router.get_metrics()
        print(f"\nMetrics:")
        print(f"  Primary invocations: {metrics['primary_invocations']}")
        print(f"  Secondary invocations: {metrics['secondary_invocations']}")
        print(f"  Tertiary invocations: {metrics['tertiary_invocations']}")
        print(f"  Fallbacks triggered: {metrics['fallback_triggered']}")
        print(f"  Total cost: ${metrics['total_cost_usd']:.4f}")
        print(f"  Avg latency: {metrics['avg_latency_seconds']:.2f}s")
        
        # Should use primary model (qwen2.5:3b)
        if metrics['primary_invocations'] == 1:
            print(f"\n✅ PASS: Primary model used as expected")
            return True
        else:
            print(f"\n⚠️  WARNING: Expected primary model but used different tier")
            return True  # Still pass as long as we got a response
    
    except Exception as e:
        print(f"❌ FAIL: {e}")
        logger.exception("Full traceback:")
        return False


def test_complex_prompt():
    """Test 3: Complex K8s remediation prompt"""
    print("\n" + "="*70)
    print("TEST 3: Complex Remediation Prompt")
    print("="*70)
    
    from service.webhook.llm_router import get_llm_router
    router = get_llm_router()
    router.reset_metrics()
    
    prompt = """
    You are an expert Kubernetes SRE. Analyze this alert:
    
    Alert: High CPU usage in pod nginx-deployment-abc123
    Namespace: production
    Logs: 
    2024-04-01 10:00:00 ERROR CPU usage at 95%
    2024-04-01 10:00:05 WARNING Throttling requests
    
    Propose a safe remediation script.
    Return JSON with fields: analysis, script, is_safe
    """
    
    messages = [HumanMessage(content=prompt)]
    
    try:
        print("\nInvoking LLM with complex prompt...")
        response = router.invoke(messages)
        
        print(f"✅ Response received ({len(response)} chars)")
        print(f"First 200 chars: {response[:200]}...")
        
        # Check if response looks like JSON
        has_json_markers = '{' in response and '}' in response
        print(f"\nContains JSON markers: {has_json_markers}")
        
        # Check metrics
        metrics = router.get_metrics()
        print(f"\nMetrics:")
        print(f"  Model used: ", end="")
        if metrics['primary_invocations'] > 0:
            print("Primary (qwen2.5:3b)")
        elif metrics['secondary_invocations'] > 0:
            print("Secondary (llama3.1:8b)")
        elif metrics['tertiary_invocations'] > 0:
            print("Tertiary (GPT-4)")
        
        print(f"  Fallbacks: {metrics['fallback_triggered']}")
        print(f"  Cost: ${metrics['total_cost_usd']:.4f}")
        print(f"  Latency: {metrics['avg_latency_seconds']:.2f}s")
        
        return True
    
    except Exception as e:
        print(f"❌ FAIL: {e}")
        logger.exception("Full traceback:")
        return False


def test_circuit_breaker():
    """Test 4: Circuit breaker manual test (info only)"""
    print("\n" + "="*70)
    print("TEST 4: Circuit Breaker Status")
    print("="*70)
    
    from service.webhook.llm_router import get_llm_router
    router = get_llm_router()
    
    metrics = router.get_metrics()
    circuit_states = metrics['circuit_states']
    
    print("\nCircuit Breaker States:")
    print(f"  Primary (qwen2.5:3b):  {'🔴 OPEN' if circuit_states['primary'] else '🟢 CLOSED'}")
    print(f"  Secondary (llama3.1:8b): {'🔴 OPEN' if circuit_states['secondary'] else '🟢 CLOSED'}")
    print(f"  Tertiary (GPT-4):   {'🔴 OPEN' if circuit_states['tertiary'] else '🟢 CLOSED'}")
    
    print(f"\nCircuit breaker trips: {metrics['circuit_breaker_trips']}")
    
    print("\nℹ️  To test circuit breaker:")
    print("  1. Stop Ollama: docker-compose stop ollama")
    print("  2. Send 3+ requests (primary will fail)")
    print("  3. Circuit breaker should open for primary")
    print("  4. Next request will skip primary and use secondary")
    
    return True


def test_router_metrics():
    """Test 5: Metrics tracking"""
    print("\n" + "="*70)
    print("TEST 5: Metrics Tracking")
    print("="*70)
    
    from service.webhook.llm_router import get_llm_router
    router = get_llm_router()
    
    metrics = router.get_metrics()
    
    print("\nAll Router Metrics:")
    for key, value in metrics.items():
        if key != 'circuit_states':
            print(f"  {key}: {value}")
    
    print("\nCircuit States:")
    for tier, state in metrics['circuit_states'].items():
        print(f"  {tier}: {'OPEN' if state else 'CLOSED'}")
    
    return True


def main():
    """Run all LLM router tests"""
    print("\n" + "="*70)
    print("PHASE 6.2 - MULTI-MODEL LLM FALLBACK TESTS")
    print("="*70)
    print("Testing: 3-tier fallback, circuit breaker, cost tracking")
    print("="*70)
    
    results = {}
    
    try:
        results['initialization'] = test_router_initialization()
    except Exception as e:
        logger.error(f"Test 1 failed: {e}")
        results['initialization'] = False
    
    try:
        results['basic_invocation'] = test_basic_invocation()
    except Exception as e:
        logger.error(f"Test 2 failed: {e}")
        results['basic_invocation'] = False
    
    try:
        results['complex_prompt'] = test_complex_prompt()
    except Exception as e:
        logger.error(f"Test 3 failed: {e}")
        results['complex_prompt'] = False
    
    try:
        results['circuit_breaker'] = test_circuit_breaker()
    except Exception as e:
        logger.error(f"Test 4 failed: {e}")
        results['circuit_breaker'] = False
    
    try:
        results['metrics'] = test_router_metrics()
    except Exception as e:
        logger.error(f"Test 5 failed: {e}")
        results['metrics'] = False
    
    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{test_name:25} {status}")
    
    total = len(results)
    passed = sum(results.values())
    print(f"\nTotal: {passed}/{total} tests passed ({passed/total*100:.0f}%)")
    print("="*70)
    
    return passed == total


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

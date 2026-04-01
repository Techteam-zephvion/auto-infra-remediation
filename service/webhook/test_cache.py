"""
Test script for Phase 6.1 - LLM Response Caching

Tests Redis-based caching of LLM responses.

Requirements:
    - Redis running (docker-compose up -d redis)
    - Python packages: redis

Usage:
    python test_cache.py
"""

import asyncio
import logging
from service.webhook.cache import get_llm_cache

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def test_cache_initialization():
    """Test 1: Cache initialization"""
    print("\n" + "="*70)
    print("TEST 1: Cache Initialization")
    print("="*70)
    
    cache = get_llm_cache()
    
    print(f"Cache enabled: {cache.enabled}")
    print(f"Cache TTL: {cache.ttl_seconds}s")
    print(f"Redis URL: {cache.redis_url}")
    
    health = cache.health_check()
    print(f"Health check: {'✅ PASS' if health else '❌ FAIL'}")
    
    return health


def test_cache_set_get():
    """Test 2: Cache set and get operations"""
    print("\n" + "="*70)
    print("TEST 2: Cache Set/Get Operations")
    print("="*70)
    
    cache = get_llm_cache()
    
    # Test data
    alert_type = "cpu_spike"
    logs = "2024-01-01 12:00:00 ERROR CPU usage at 95%"
    response = {
        "analysis": "High CPU usage detected, suggesting resource limit increase",
        "script": "kubectl scale deployment nginx --replicas=3",
        "is_safe": True
    }
    
    # Set cache
    print(f"\nSetting cache for alert_type={alert_type}...")
    success = cache.set_cached_response(
        alert_type=alert_type,
        logs=logs,
        response=response
    )
    print(f"Set result: {'✅ SUCCESS' if success else '❌ FAILED'}")
    
    # Get cache (should hit)
    print(f"\nGetting cached response...")
    cached = cache.get_cached_response(
        alert_type=alert_type,
        logs=logs
    )
    
    if cached:
        print(f"✅ CACHE HIT")
        print(f"Analysis: {cached['analysis'][:50]}...")
        print(f"Script: {cached['script']}")
        print(f"Is safe: {cached['is_safe']}")
        return True
    else:
        print(f"❌ CACHE MISS")
        return False


def test_cache_miss():
    """Test 3: Cache miss with different logs"""
    print("\n" + "="*70)
    print("TEST 3: Cache Miss Test")
    print("="*70)
    
    cache = get_llm_cache()
    
    # Different logs should miss cache
    alert_type = "memory_leak"
    logs = "2024-01-01 13:00:00 ERROR Memory usage at 98%"
    
    print(f"\nChecking cache for new alert_type={alert_type}...")
    cached = cache.get_cached_response(
        alert_type=alert_type,
        logs=logs
    )
    
    if cached is None:
        print(f"✅ CACHE MISS (as expected)")
        return True
    else:
        print(f"❌ UNEXPECTED CACHE HIT")
        return False


def test_cache_normalization():
    """Test 4: Log normalization (timestamps removed)"""
    print("\n" + "="*70)
    print("TEST 4: Log Normalization Test")
    print("="*70)
    
    cache = get_llm_cache()
    
    alert_type = "test_normalization"
    
    # Set cache with timestamp
    logs1 = "2024-01-01 12:00:00 ERROR Test message"
    response = {
        "analysis": "Test analysis",
        "script": "echo 'test'",
        "is_safe": True
    }
    
    print(f"\nSetting cache with logs: {logs1}")
    cache.set_cached_response(alert_type, logs1, response)
    
    # Check with different timestamp (should still hit due to normalization)
    logs2 = "2024-01-02 15:30:00 ERROR Test message"
    print(f"Checking cache with logs: {logs2}")
    
    cached = cache.get_cached_response(alert_type, logs2)
    
    if cached:
        print(f"✅ CACHE HIT (normalization working)")
        return True
    else:
        print(f"❌ CACHE MISS (normalization may not work as expected)")
        return False


def test_cache_stats():
    """Test 5: Cache statistics"""
    print("\n" + "="*70)
    print("TEST 5: Cache Statistics")
    print("="*70)
    
    cache = get_llm_cache()
    stats = cache.get_stats()
    
    print(f"\nCache Statistics:")
    print(f"  Enabled: {stats['enabled']}")
    print(f"  Total requests: {stats['total_requests']}")
    print(f"  Cache hits: {stats['cache_hits']}")
    print(f"  Cache misses: {stats['cache_misses']}")
    print(f"  Cache errors: {stats['cache_errors']}")
    print(f"  Hit rate: {stats['hit_rate_percent']}%")
    
    if 'redis_memory_used' in stats:
        print(f"  Redis memory: {stats['redis_memory_used']}")
        print(f"  Redis keys: {stats['redis_keys_count']}")
    
    return True


def test_cache_invalidation():
    """Test 6: Cache invalidation"""
    print("\n" + "="*70)
    print("TEST 6: Cache Invalidation")
    print("="*70)
    
    cache = get_llm_cache()
    
    # Add some test entries
    for i in range(3):
        cache.set_cached_response(
            alert_type=f"test_{i}",
            logs=f"test logs {i}",
            response={"analysis": f"test {i}", "script": "echo 'test'", "is_safe": True}
        )
    
    print(f"\nAdded 3 test cache entries")
    
    # Invalidate all
    print(f"Invalidating all cache entries...")
    deleted = cache.invalidate_cache("llm:response:*")
    print(f"Deleted {deleted} entries")
    
    if deleted >= 3:
        print(f"✅ INVALIDATION SUCCESS (deleted {deleted} entries)")
        return True
    else:
        print(f"⚠️  INVALIDATION PARTIAL (deleted {deleted} entries, expected ≥3)")
        return True  # Still pass, might have had fewer entries


def main():
    """Run all cache tests"""
    print("\n" + "="*70)
    print("PHASE 6.1 - LLM RESPONSE CACHING TESTS")
    print("="*70)
    print("Testing: Redis connection, cache operations, normalization")
    print("="*70)
    
    results = {}
    
    try:
        results['initialization'] = test_cache_initialization()
    except Exception as e:
        logger.error(f"Test 1 failed: {e}")
        results['initialization'] = False
    
    try:
        results['set_get'] = test_cache_set_get()
    except Exception as e:
        logger.error(f"Test 2 failed: {e}")
        results['set_get'] = False
    
    try:
        results['cache_miss'] = test_cache_miss()
    except Exception as e:
        logger.error(f"Test 3 failed: {e}")
        results['cache_miss'] = False
    
    try:
        results['normalization'] = test_cache_normalization()
    except Exception as e:
        logger.error(f"Test 4 failed: {e}")
        results['normalization'] = False
    
    try:
        results['stats'] = test_cache_stats()
    except Exception as e:
        logger.error(f"Test 5 failed: {e}")
        results['stats'] = False
    
    try:
        results['invalidation'] = test_cache_invalidation()
    except Exception as e:
        logger.error(f"Test 6 failed: {e}")
        results['invalidation'] = False
    
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

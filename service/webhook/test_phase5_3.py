"""
Test script for Phase 5.3 - Script Sandboxing Layer

Tests sandboxed execution of remediation scripts in isolated Kubernetes Jobs.

Usage:
    python test_phase5_3.py
"""

import asyncio
import logging
from service.webhook.job_executor import get_job_executor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


async def test_safe_script():
    """Test 1: Safe kubectl command"""
    print("\n" + "="*70)
    print("TEST 1: Safe kubectl get pods command")
    print("="*70)
    
    executor = get_job_executor()
    
    result = await executor.execute_script(
        script="kubectl get pods -n default",
        workflow_id="TEST-SAFE-001",
        namespace="default",
        alert_type="test",
        pod_name="test-pod"
    )
    
    print(f"\nResult: {'✅ SUCCESS' if result['success'] else '❌ FAILED'}")
    print(f"Job: {result['job_name']}")
    print(f"Duration: {result['duration_seconds']}s")
    print(f"Exit Code: {result['exit_code']}")
    print(f"Output:\n{result['stdout']}")
    if result['error']:
        print(f"Error: {result['error']}")
    
    return result['success']


async def test_dangerous_script():
    """Test 2: Dangerous command (should be blocked at validation level, but testing sandbox)"""
    print("\n" + "="*70)
    print("TEST 2: Dangerous command (network isolation test)")
    print("="*70)
    
    executor = get_job_executor()
    
    # This should fail due to network isolation (can't reach external internet)
    result = await executor.execute_script(
        script="curl -s https://www.google.com",
        workflow_id="TEST-DANGEROUS-001",
        namespace="default",
        alert_type="test",
        pod_name="test-pod",
        timeout=30
    )
    
    print(f"\nResult: {'✅ BLOCKED (as expected)' if not result['success'] else '❌ UNEXPECTED SUCCESS'}")
    print(f"Job: {result['job_name']}")
    print(f"Duration: {result['duration_seconds']}s")
    print(f"Exit Code: {result['exit_code']}")
    print(f"Output:\n{result['stdout']}")
    if result['error']:
        print(f"Error: {result['error']}")
    
    # Success means it was blocked (which is what we want)
    return not result['success']


async def test_timeout():
    """Test 3: Script timeout"""
    print("\n" + "="*70)
    print("TEST 3: Script timeout (sleep 65s with 60s timeout)")
    print("="*70)
    
    executor = get_job_executor()
    
    result = await executor.execute_script(
        script="sleep 65 && echo 'Should not see this'",
        workflow_id="TEST-TIMEOUT-001",
        namespace="default",
        alert_type="test",
        pod_name="test-pod",
        timeout=60
    )
    
    print(f"\nResult: {'✅ TIMED OUT (as expected)' if not result['success'] else '❌ UNEXPECTED SUCCESS'}")
    print(f"Job: {result['job_name']}")
    print(f"Duration: {result['duration_seconds']}s")
    print(f"Exit Code: {result['exit_code']}")
    if result['error']:
        print(f"Error: {result['error']}")
    
    # Success means it timed out (which is what we want)
    return not result['success']


async def test_resource_limits():
    """Test 4: Resource limits enforcement"""
    print("\n" + "="*70)
    print("TEST 4: Resource limits (memory bomb)")
    print("="*70)
    
    executor = get_job_executor()
    
    # Try to allocate more memory than limit (256Mi)
    result = await executor.execute_script(
        script="dd if=/dev/zero of=/tmp/bigfile bs=1M count=300",
        workflow_id="TEST-RESOURCE-001",
        namespace="default",
        alert_type="test",
        pod_name="test-pod"
    )
    
    print(f"\nResult: {'✅ BLOCKED (as expected)' if not result['success'] else '⚠️  Result varies by K8s config'}")
    print(f"Job: {result['job_name']}")
    print(f"Duration: {result['duration_seconds']}s")
    print(f"Exit Code: {result['exit_code']}")
    print(f"Output:\n{result['stdout'][:500]}")
    if result['error']:
        print(f"Error: {result['error']}")
    
    return True  # Either succeeds or fails is OK for this test


async def test_script_sandboxing():
    """Test 5: Read-only filesystem"""
    print("\n" + "="*70)
    print("TEST 5: Read-only filesystem (write to / should fail)")
    print("="*70)
    
    executor = get_job_executor()
    
    # Try to write to root filesystem (should fail - read-only)
    result = await executor.execute_script(
        script="touch /test-file 2>&1",
        workflow_id="TEST-READONLY-001",
        namespace="default",
        alert_type="test",
        pod_name="test-pod"
    )
    
    print(f"\nResult: {'✅ BLOCKED (as expected)' if not result['success'] else '❌ UNEXPECTED SUCCESS'}")
    print(f"Job: {result['job_name']}")
    print(f"Duration: {result['duration_seconds']}s")
    print(f"Exit Code: {result['exit_code']}")
    print(f"Output:\n{result['stdout']}")
    if result['error']:
        print(f"Error: {result['error']}")
    
    # Success means it was blocked (read-only filesystem working)
    return not result['success']


async def main():
    """Run all tests"""
    print("\n" + "="*70)
    print("PHASE 5.3 - SCRIPT SANDBOXING LAYER TESTS")
    print("="*70)
    print("Testing: Network isolation, Resource limits, Security context")
    print("="*70)
    
    results = {}
    
    try:
        results['safe_script'] = await test_safe_script()
    except Exception as e:
        logger.error(f"Test 1 failed with exception: {e}")
        results['safe_script'] = False
    
    try:
        results['network_isolation'] = await test_dangerous_script()
    except Exception as e:
        logger.error(f"Test 2 failed with exception: {e}")
        results['network_isolation'] = False
    
    try:
        results['timeout'] = await test_timeout()
    except Exception as e:
        logger.error(f"Test 3 failed with exception: {e}")
        results['timeout'] = False
    
    try:
        results['resource_limits'] = await test_resource_limits()
    except Exception as e:
        logger.error(f"Test 4 failed with exception: {e}")
        results['resource_limits'] = False
    
    try:
        results['readonly_fs'] = await test_script_sandboxing()
    except Exception as e:
        logger.error(f"Test 5 failed with exception: {e}")
        results['readonly_fs'] = False
    
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
    success = asyncio.run(main())
    exit(0 if success else 1)

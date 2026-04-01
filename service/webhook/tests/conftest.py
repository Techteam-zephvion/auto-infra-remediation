"""
Test fixtures and configuration for the test suite
"""
import pytest
import asyncio
from typing import Dict, Any


@pytest.fixture
def sample_alert_payload() -> Dict[str, Any]:
    """Sample AlertManager webhook payload for testing"""
    return {
        "receiver": "auto-remediation-webhook",
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {
                "alertname": "HighCPUUsage",
                "namespace": "default",
                "pod": "test-pod-123",
                "severity": "critical",
                "app": "test-app",
            },
            "annotations": {
                "summary": "CPU usage is above 90%",
                "description": "Pod test-pod-123 in namespace default has high CPU usage",
            },
        }],
    }


@pytest.fixture
def sample_pod_logs() -> str:
    """Sample Kubernetes pod logs for testing"""
    return """
2026-03-31 10:15:23 INFO Starting application
2026-03-31 10:15:24 INFO Connected to database
2026-03-31 10:20:15 ERROR Out of memory: heap space exhausted
2026-03-31 10:20:16 WARN Retrying failed operation
2026-03-31 10:20:17 ERROR Connection timeout after 30s
"""


@pytest.fixture
def sample_remediation_plan() -> Dict[str, Any]:
    """Sample LLM remediation plan for testing"""
    return {
        "analysis": "Pod is experiencing memory issues due to heap exhaustion. Need to restart pod to clear memory.",
        "script": "kubectl delete pod test-pod-123 -n default",
        "is_safe": True,
    }


@pytest.fixture
def dangerous_remediation_plan() -> Dict[str, Any]:
    """Sample dangerous remediation plan that should be blocked"""
    return {
        "analysis": "Need to clean up all pods",
        "script": "kubectl delete pod --all -n default",
        "is_safe": False,
    }


@pytest.fixture
def mock_llm_response():
    """Mock LLM response object"""
    class MockLLMResponse:
        def __init__(self, content: str):
            self.content = content
    
    return MockLLMResponse


@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

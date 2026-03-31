"""
End-to-end tests simulating full remediation workflows
Tests: Complete pipeline from webhook to execution
"""
import pytest
import json
from unittest.mock import Mock, patch, AsyncMock
from httpx import AsyncClient


class TestE2EWebhookFlow:
    """Test complete webhook flow from alert to remediation"""
    
    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_full_webhook_to_remediation_flow(self, sample_alert_payload):
        """Test complete flow: webhook → parse → analyze → validate → execute"""
        from api import app
        
        # Mock all external dependencies
        with patch('k8s_client.get_pods_with_labels') as mock_pods:
            with patch('k8s_client.get_pod_logs') as mock_logs:
                with patch('graph.ChatOllama') as mock_ollama:
                    with patch('database.insert_audit_event', return_value=1):
                        with patch('database.update_audit_event'):
                            # Setup mocks
                            mock_pod = Mock()
                            mock_pod.metadata.name = "test-pod-123"
                            mock_pods.return_value = [mock_pod]
                            mock_logs.return_value = "ERROR: High CPU usage detected"
                            
                            class MockLLMResponse:
                                def __init__(self, content):
                                    self.content = content
                            
                            mock_llm = Mock()
                            mock_llm.invoke.side_effect = [
                                MockLLMResponse(json.dumps({
                                    "analysis": "Pod needs restart due to CPU spike",
                                    "script": "kubectl get pods -n default",  # Safe command
                                    "is_safe": True
                                })),
                                MockLLMResponse(json.dumps({
                                    "approved": True,
                                    "reasoning": "Safe read-only command"
                                }))
                            ]
                            mock_ollama.return_value = mock_llm
                            
                            # Make webhook request
                            async with AsyncClient(app=app, base_url="http://test") as client:
                                response = await client.post("/webhook", json=sample_alert_payload)
                            
                            assert response.status_code == 200
                            data = response.json()
                            assert data["status"] == "accepted"
                            assert "workflow_id" in data
    
    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_dangerous_script_blocked_e2e(self, sample_alert_payload):
        """Test that dangerous scripts are blocked end-to-end"""
        from api import app
        
        with patch('k8s_client.get_pods_with_labels') as mock_pods:
            with patch('k8s_client.get_pod_logs') as mock_logs:
                with patch('graph.ChatOllama') as mock_ollama:
                    with patch('database.insert_audit_event', return_value=1):
                        with patch('database.update_audit_event'):
                            # LLM suggests dangerous command
                            mock_pod = Mock()
                            mock_pod.metadata.name = "test-pod"
                            mock_pods.return_value = [mock_pod]
                            mock_logs.return_value = "Logs"
                            
                            class MockLLMResponse:
                                def __init__(self, content):
                                    self.content = content
                            
                            mock_llm = Mock()
                            mock_llm.invoke.side_effect = [
                                MockLLMResponse(json.dumps({
                                    "analysis": "Delete all pods",
                                    "script": "kubectl delete pod --all -n default",
                                    "is_safe": True  # LLM wrongly says safe
                                })),
                                MockLLMResponse(json.dumps({
                                    "approved": False,
                                    "reasoning": "Dangerous command blocked"
                                }))
                            ]
                            mock_ollama.return_value = mock_llm
                            
                            async with AsyncClient(app=app, base_url="http://test") as client:
                                response = await client.post("/webhook", json=sample_alert_payload)
                            
                            # Should still accept but script will be blocked
                            assert response.status_code == 200


class TestE2ETemporalIntegration:
    """Test E2E flow with Temporal workflows"""
    
    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_temporal_workflow_execution(self, sample_alert_payload):
        """Test workflow execution through Temporal"""
        from api import app
        
        with patch('temporal_client.get_temporal_client') as mock_temporal:
            with patch('temporal_client.start_remediation_workflow') as mock_start:
                # Mock Temporal client
                mock_client = AsyncMock()
                mock_temporal.return_value = mock_client
                
                mock_start.return_value = {
                    "status": "accepted",
                    "workflow_id": "WF-TEST-123",
                    "temporal_enabled": True,
                    "run_id": "run-123"
                }
                
                async with AsyncClient(app=app, base_url="http://test") as client:
                    response = await client.post("/webhook", json=sample_alert_payload)
                
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "accepted"
    
    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_temporal_unavailable_fallback(self, sample_alert_payload):
        """Test fallback to direct execution when Temporal unavailable"""
        from api import app
        
        with patch('temporal_client.get_temporal_client', return_value=None):
            with patch('graph.build_graph') as mock_graph:
                with patch('database.insert_audit_event', return_value=1):
                    with patch('database.update_audit_event'):
                        # Mock graph execution
                        mock_graph_app = Mock()
                        mock_graph_app.invoke.return_value = {
                            "parsed_data": {},
                            "remediation_plan": {},
                            "safety_check": {"approved": False}
                        }
                        mock_graph.return_value = mock_graph_app
                        
                        async with AsyncClient(app=app, base_url="http://test") as client:
                            response = await client.post("/webhook", json=sample_alert_payload)
                        
                        # Should still work with fallback
                        assert response.status_code == 200


class TestE2EErrorRecovery:
    """Test error recovery in E2E scenarios"""
    
    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_llm_timeout_recovery(self, sample_alert_payload):
        """Test recovery from LLM timeout"""
        from api import app
        
        with patch('k8s_client.get_pods_with_labels', return_value=[]):
            with patch('graph.ChatOllama') as mock_ollama:
                with patch('database.insert_audit_event', return_value=1):
                    # LLM times out on first attempt, succeeds on retry
                    mock_llm = Mock()
                    mock_llm.invoke.side_effect = [
                        TimeoutError("LLM timeout"),
                        Mock(content=json.dumps({
                            "analysis": "Analysis",
                            "script": "kubectl get pods",
                            "is_safe": True
                        }))
                    ]
                    mock_ollama.return_value = mock_llm
                    
                    async with AsyncClient(app=app, base_url="http://test") as client:
                        response = await client.post("/webhook", json=sample_alert_payload)
                    
                    # Should recover and complete
                    assert response.status_code == 200
    
    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_database_unavailable_fallback(self, sample_alert_payload):
        """Test fallback when database is unavailable"""
        from api import app
        
        with patch('database.insert_audit_event', side_effect=Exception("DB down")):
            with patch('database.update_audit_event', side_effect=Exception("DB down")):
                with patch('k8s_client.get_pods_with_labels', return_value=[]):
                    with patch('graph.ChatOllama') as mock_ollama:
                        mock_llm = Mock()
                        mock_llm.invoke.return_value = Mock(content=json.dumps({
                            "analysis": "Test",
                            "script": "kubectl get pods",
                            "is_safe": True
                        }))
                        mock_ollama.return_value = mock_llm
                        
                        async with AsyncClient(app=app, base_url="http://test") as client:
                            response = await client.post("/webhook", json=sample_alert_payload)
                        
                        # Should still process alert despite DB failure
                        assert response.status_code == 200


class TestE2EMultipleAlerts:
    """Test handling multiple concurrent alerts"""
    
    @pytest.mark.e2e
    @pytest.mark.asyncio
    @pytest.mark.slow
    async def test_concurrent_alert_processing(self, sample_alert_payload):
        """Test processing multiple alerts concurrently"""
        import asyncio
        from api import app
        
        with patch('k8s_client.get_pods_with_labels', return_value=[]):
            with patch('graph.ChatOllama') as mock_ollama:
                with patch('database.insert_audit_event', return_value=1):
                    with patch('database.update_audit_event'):
                        mock_llm = Mock()
                        mock_llm.invoke.return_value = Mock(content=json.dumps({
                            "analysis": "Test",
                            "script": "kubectl get pods",
                            "is_safe": True
                        }))
                        mock_ollama.return_value = mock_llm
                        
                        async with AsyncClient(app=app, base_url="http://test") as client:
                            # Send 5 concurrent requests
                            tasks = [
                                client.post("/webhook", json=sample_alert_payload)
                                for _ in range(5)
                            ]
                            responses = await asyncio.gather(*tasks)
                        
                        # All should succeed
                        assert all(r.status_code == 200 for r in responses)
                        workflow_ids = [r.json()["workflow_id"] for r in responses]
                        # All should have unique workflow IDs
                        assert len(set(workflow_ids)) == 5


class TestE2EHealthAndMetrics:
    """Test E2E health checks and metrics"""
    
    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_health_endpoint_all_services(self):
        """Test health endpoint shows all service statuses"""
        from api import app
        
        with patch('httpx.AsyncClient.get') as mock_http:
            with patch('temporal_client.check_temporal_health') as mock_temporal:
                with patch('vault_client.check_vault_health') as mock_vault:
                    # Mock Ollama response
                    mock_response = Mock()
                    mock_response.status_code = 200
                    mock_response.json.return_value = {"models": ["qwen2.5:3b"]}
                    mock_http.return_value = mock_response
                    
                    mock_temporal.return_value = {"status": "healthy", "connected": True}
                    mock_vault.return_value = {"status": "healthy", "authenticated": True}
                    
                    async with AsyncClient(app=app, base_url="http://test") as client:
                        response = await client.get("/health")
                    
                    assert response.status_code == 200
                    data = response.json()
                    assert "dependencies" in data
                    assert "ollama" in data["dependencies"]
                    assert "temporal" in data["dependencies"]
                    assert "vault" in data["dependencies"]
    
    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_metrics_endpoint(self):
        """Test Prometheus metrics endpoint"""
        from api import app
        
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get("/metrics")
        
        assert response.status_code == 200
        # Should contain Prometheus metrics
        content = response.text
        assert "remediation_workflows_total" in content or "python_" in content


class TestE2ERealWorldScenarios:
    """Test real-world scenarios"""
    
    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_memory_leak_scenario(self):
        """Test memory leak detection and remediation"""
        from api import app
        
        alert = {
            "alerts": [{
                "status": "firing",
                "labels": {
                    "alertname": "MemoryLeak",
                    "namespace": "production",
                    "pod": "app-pod-xyz",
                    "severity": "warning"
                },
                "annotations": {
                    "summary": "Memory usage increasing continuously"
                }
            }]
        }
        
        with patch('k8s_client.get_pods_with_labels') as mock_pods:
            with patch('k8s_client.get_pod_logs') as mock_logs:
                with patch('graph.ChatOllama') as mock_ollama:
                    with patch('database.insert_audit_event', return_value=1):
                        mock_pod = Mock()
                        mock_pod.metadata.name = "app-pod-xyz"
                        mock_pods.return_value = [mock_pod]
                        mock_logs.return_value = "java.lang.OutOfMemoryError: Java heap space"
                        
                        mock_llm = Mock()
                        mock_llm.invoke.return_value = Mock(content=json.dumps({
                            "analysis": "Memory leak detected in Java application",
                            "script": "kubectl delete pod app-pod-xyz -n production",
                            "is_safe": True
                        }))
                        mock_ollama.return_value = mock_llm
                        
                        async with AsyncClient(app=app, base_url="http://test") as client:
                            response = await client.post("/webhook", json=alert)
                        
                        assert response.status_code == 200
    
    @pytest.mark.e2e
    @pytest.mark.asyncio
    async def test_cpu_spike_scenario(self):
        """Test CPU spike detection and remediation"""
        from api import app
        
        alert = {
            "alerts": [{
                "status": "firing",
                "labels": {
                    "alertname": "HighCPUUsage",
                    "namespace": "default",
                    "pod": "worker-pod-123",
                    "severity": "critical"
                },
                "annotations": {
                    "summary": "CPU usage above 90%"
                }
            }]
        }
        
        with patch('k8s_client.get_pods_with_labels', return_value=[]):
            with patch('graph.ChatOllama') as mock_ollama:
                with patch('database.insert_audit_event', return_value=1):
                    mock_llm = Mock()
                    mock_llm.invoke.return_value = Mock(content=json.dumps({
                        "analysis": "CPU spike due to infinite loop",
                        "script": "kubectl describe pod worker-pod-123 -n default",
                        "is_safe": True
                    }))
                    mock_ollama.return_value = mock_llm
                    
                    async with AsyncClient(app=app, base_url="http://test") as client:
                        response = await client.post("/webhook", json=alert)
                    
                    assert response.status_code == 200

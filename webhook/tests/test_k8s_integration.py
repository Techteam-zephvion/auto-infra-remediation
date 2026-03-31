"""
Integration tests with mock Kubernetes API
Tests: K8s client integration, pod log fetchiing, error handling
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from kubernetes.client.rest import ApiException


class TestK8sClientIntegration:
    """Test Kubernetes client integration"""
    
    @pytest.mark.integration
    def test_k8s_client_initialization(self):
        """Test that K8s client initializes correctly"""
        with patch('k8s_client.config.load_incluster_config', side_effect=Exception("Not in cluster")):
            with patch('k8s_client.config.load_kube_config'):
                from k8s_client import get_k8s_client
                
                client = get_k8s_client()
                assert client is not None
    
    @pytest.mark.integration
    def test_get_pods_with_labels_success(self):
        """Test successful pod listing with label selector"""
        from k8s_client import get_pods_with_labels
        
        mock_pod_list = Mock()
        mock_pod = Mock()
        mock_pod.metadata.name = "test-pod-123"
        mock_pod.metadata.namespace = "default"
        mock_pod_list.items = [mock_pod]
        
        with patch('k8s_client.client.CoreV1Api') as mock_api:
            mock_api.return_value.list_namespaced_pod.return_value = mock_pod_list
            
            pods = get_pods_with_labels(
                namespace="default",
                label_selector="app=test-app"
            )
            
            assert len(pods) == 1
            assert pods[0].metadata.name == "test-pod-123"
    
    @pytest.mark.integration
    def test_get_pods_handles_api_exception(self):
        """Test error handling when K8s API returns error"""
        from k8s_client import get_pods_with_labels
        
        with patch('k8s_client.client.CoreV1Api') as mock_api:
            mock_api.return_value.list_namespaced_pod.side_effect = ApiException(
                status=403,
                reason="Forbidden"
            )
            
            pods = get_pods_with_labels(
                namespace="default",
                label_selector="app=test-app"
            )
            
            # Should return empty list on error
            assert pods == []
    
    @pytest.mark.integration
    def test_get_pod_logs_success(self):
        """Test successful pod log retrieval"""
        from k8s_client import get_pod_logs
        
        mock_logs = "2026-03-31 10:15:23 INFO Application started\n2026-03-31 10:15:24 ERROR Connection failed"
        
        with patch('k8s_client.client.CoreV1Api') as mock_api:
            mock_api.return_value.read_namespaced_pod_log.return_value = mock_logs
            
            logs = get_pod_logs(
                pod_name="test-pod",
                namespace="default",
                tail_lines=100
            )
            
            assert "Application started" in logs
            assert "Connection failed" in logs
            assert len(logs) > 0
    
    @pytest.mark.integration
    def test_get_pod_logs_handles_not_found(self):
        """Test handling of non-existent pod"""
        from k8s_client import get_pod_logs
        
        with patch('k8s_client.client.CoreV1Api') as mock_api:
            mock_api.return_value.read_namespaced_pod_log.side_effect = ApiException(
                status=404,
                reason="Not Found"
            )
            
            logs = get_pod_logs(
                pod_name="non-existent-pod",
                namespace="default"
            )
            
            # Should return empty string or error message
            assert isinstance(logs, str)
    
    @pytest.mark.integration
    def test_get_pod_logs_with_tail_limit(self):
        """Test log retrieval with tail lines limit"""
        from k8s_client import get_pod_logs
        
        with patch('k8s_client.client.CoreV1Api') as mock_api:
            mock_api.return_value.read_namespaced_pod_log.return_value = "logs"
            
            get_pod_logs(
                pod_name="test-pod",
                namespace="default",
                tail_lines=50
            )
            
            # Should call with tail_lines parameter
            call_kwargs = mock_api.return_value.read_namespaced_pod_log.call_args[1]
            assert 'tail_lines' in call_kwargs
            assert call_kwargs['tail_lines'] == 50


class TestLogParserIntegration:
    """Test log parser with K8s integration"""
    
    @pytest.mark.integration
    def test_log_parser_fetches_from_k8s(self, sample_alert_payload):
        """Test that log parser integrates with K8s client"""
        from graph import graph_log_parser
        
        mock_logs = "Application error: Out of memory"
        
        with patch('k8s_client.get_pods_with_labels') as mock_get_pods:
            with patch('k8s_client.get_pod_logs', return_value=mock_logs):
                mock_pod = Mock()
                mock_pod.metadata.name = "test-pod-123"
                mock_get_pods.return_value = [mock_pod]
                
                result = graph_log_parser({"alert_payload": sample_alert_payload})
                
                assert "parsed_data" in result
                assert "logs" in result["parsed_data"]
                assert "Out of memory" in result["parsed_data"]["logs"]
    
    @pytest.mark.integration
    def test_log_parser_handles_no_pods_found(self, sample_alert_payload):
        """Test log parser when no pods match the selector"""
        from graph import graph_log_parser
        
        with patch('k8s_client.get_pods_with_labels', return_value=[]):
            result = graph_log_parser({"alert_payload": sample_alert_payload})
            
            assert "parsed_data" in result
            assert "logs" in result["parsed_data"]
            # Should have fallback message
            assert len(result["parsed_data"]["logs"]) > 0
    
    @pytest.mark.integration
    def test_log_parser_handles_k8s_connection_error(self, sample_alert_payload):
        """Test log parser when K8s connection fails"""
        from graph import graph_log_parser
        
        with patch('k8s_client.get_pods_with_labels', side_effect=Exception("Connection refused")):
            result = graph_log_parser({"alert_payload": sample_alert_payload})
            
            # Should handle error gracefully
            assert "parsed_data" in result
            assert "logs" in result["parsed_data"]


class TestEndToEndWithMockK8s:
    """End-to-end tests with mocked Kubernetes"""
    
    @pytest.mark.integration
    @patch('graph.ChatOllama')
    def test_full_pipeline_with_k8s_logs(self, mock_ollama, sample_alert_payload, sample_remediation_plan):
        """Test complete pipeline from alert to remediation with K8s integration"""
        import json
        from graph import build_graph
        
        # Mock K8s responses
        mock_pod = Mock()
        mock_pod.metadata.name = "test-pod-123"
        mock_logs = "ERROR: Application crashed due to OutOfMemoryError"
        
        with patch('k8s_client.get_pods_with_labels', return_value=[mock_pod]):
            with patch('k8s_client.get_pod_logs', return_value=mock_logs):
                # Mock LLM responses
                class MockResponse:
                    def __init__(self, content):
                        self.content = content
                
                mock_llm = Mock()
                mock_llm.invoke.side_effect = [
                    MockResponse(json.dumps(sample_remediation_plan)),  # Solver
                    MockResponse(json.dumps({"approved": False, "reasoning": "Too risky"}))  # Validator
                ]
                mock_ollama.return_value = mock_llm
                
                graph = build_graph()
                result = graph.invoke({"alert_payload": sample_alert_payload})
                
                # Should complete full pipeline
                assert "parsed_data" in result
                assert "OutOfMemoryError" in result["parsed_data"]["logs"]
                assert "remediation_plan" in result
                assert "safety_check" in result


class TestK8sErrorConditions:
    """Test various K8s error conditions"""
    
    @pytest.mark.integration
    def test_handle_pod_not_ready(self):
        """Test handling of pods that are not ready"""
        from k8s_client import get_pod_logs
        
        with patch('k8s_client.client.CoreV1Api') as mock_api:
            mock_api.return_value.read_namespaced_pod_log.side_effect = ApiException(
                status=400,
                reason="Bad Request - Pod not ready"
            )
            
            logs = get_pod_logs("not-ready-pod", "default")
            assert isinstance(logs, str)
    
    @pytest.mark.integration
    def test_handle_namespace_not_found(self):
        """Test handling of non-existent namespace"""
        from k8s_client import get_pods_with_labels
        
        with patch('k8s_client.client.CoreV1Api') as mock_api:
            mock_api.return_value.list_namespaced_pod.side_effect = ApiException(
                status=404,
                reason="Namespace not found"
            )
            
            pods = get_pods_with_labels("non-existent-ns", "app=test")
            assert pods == []
    
    @pytest.mark.integration
    def test_handle_permission_denied(self):
        """Test handling of insufficient permissions"""
        from k8s_client import get_pods_with_labels
        
        with patch('k8s_client.client.CoreV1Api') as mock_api:
            mock_api.return_value.list_namespaced_pod.side_effect = ApiException(
                status=403,
                reason="Forbidden - Insufficient permissions"
            )
            
            pods = get_pods_with_labels("production", "app=critical")
            assert pods == []
    
    @pytest.mark.integration
    def test_handle_timeout(self):
        """Test handling of K8s API timeout"""
        from k8s_client import get_pod_logs
        
        with patch('k8s_client.client.CoreV1Api') as mock_api:
            mock_api.return_value.read_namespaced_pod_log.side_effect = TimeoutError("Request timed out")
            
            logs = get_pod_logs("slow-pod", "default")
            assert isinstance(logs, str)


class TestMultiplePods:
    """Test scenarios with multiple pods"""
    
    @pytest.mark.integration
    def test_multiple_pods_matching_selector(self):
        """Test when multiple pods match the label selector"""
        from k8s_client import get_pods_with_labels
        
        mock_pod_list = Mock()
        pods = []
        for i in range(3):
            pod = Mock()
            pod.metadata.name = f"test-pod-{i}"
            pod.metadata.namespace = "default"
            pods.append(pod)
        mock_pod_list.items = pods
        
        with patch('k8s_client.client.CoreV1Api') as mock_api:
            mock_api.return_value.list_namespaced_pod.return_value = mock_pod_list
            
            result = get_pods_with_labels("default", "app=test")
            
            assert len(result) == 3
            assert all(pod.metadata.name.startswith("test-pod") for pod in result)
    
    @pytest.mark.integration
    def test_log_parser_with_multiple_pods(self, sample_alert_payload):
        """Test log parser selects correct pod from multiple matches"""
        from graph import graph_log_parser
        
        pods = []
        for i in range(3):
            pod = Mock()
            pod.metadata.name = f"test-pod-{i}"
            pods.append(pod)
        
        with patch('k8s_client.get_pods_with_labels', return_value=pods):
            with patch('k8s_client.get_pod_logs', return_value="test logs"):
                result = graph_log_parser({"alert_payload": sample_alert_payload})
                
                assert "parsed_data" in result
                assert "pod_name" in result["parsed_data"]

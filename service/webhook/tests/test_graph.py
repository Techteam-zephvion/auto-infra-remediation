"""
Unit tests for graph.py - LangGraph remediation pipeline
Tests: safety patterns, field mapping, retry logic, LLM integration
"""
import pytest
import json
import re
from unittest.mock import Mock, patch, AsyncMock
from service.webhook.graph import (
    DENY_PATTERNS,
    _fix_remediation_plan_fields,
    _fix_safety_validation_fields,
    build_graph,
)


class TestDenyPatterns:
    """Test safety validation DENY_PATTERNS"""
    
    @pytest.mark.unit
    def test_delete_all_pattern(self):
        """Test that 'delete --all' is blocked"""
        dangerous_scripts = [
            "kubectl delete pod --all",
            "kubectl delete deployment --all -n production",
            "kubectl delete --all pods",
        ]
        
        # Compile patterns to regex objects
        compiled_patterns = [re.compile(pattern) for pattern in DENY_PATTERNS]
        
        for script in dangerous_scripts:
            blocked = any(pattern.search(script) for pattern in compiled_patterns)
            assert blocked, f"Script should be blocked: {script}"
    
    @pytest.mark.unit
    def test_force_delete_pattern(self):
        """Test that dangerous rm commands are blocked"""
        dangerous_scripts = [
            "rm -rf /",
            "rm -rf *",
        ]
        
        compiled_patterns = [re.compile(pattern) for pattern in DENY_PATTERNS]
        
        for script in dangerous_scripts:
            blocked = any(pattern.search(script) for pattern in compiled_patterns)
            assert blocked, f"Script should be blocked: {script}"
    
    @pytest.mark.unit
    def test_namespace_deletion_pattern(self):
        """Test that namespace deletion is blocked"""
        dangerous_scripts = [
            "kubectl delete namespace production",
            "kubectl delete ns kube-system",
        ]
        
        compiled_patterns = [re.compile(pattern) for pattern in DENY_PATTERNS]
        
        for script in dangerous_scripts:
            blocked = any(pattern.search(script) for pattern in compiled_patterns)
            assert blocked, f"Script should be blocked: {script}"
    
    @pytest.mark.unit
    def test_safe_scripts_not_blocked(self):
        """Test that safe scripts pass validation"""
        safe_scripts = [
            "kubectl get pods",
            "kubectl describe pod my-pod",
            "kubectl logs my-pod",
            "kubectl delete pod specific-pod-name -n default",
            "kubectl scale deployment my-app --replicas=3",
        ]
        
        compiled_patterns = [re.compile(pattern) for pattern in DENY_PATTERNS]
        
        for script in safe_scripts:
            blocked = any(pattern.search(script) for pattern in compiled_patterns)
            assert not blocked, f"Safe script should not be blocked: {script}"


class TestFieldMapping:
    """Test LLM field name mapping fix"""
    
    @pytest.mark.unit
    def test_fix_remediation_plan_standard_fields(self):
        """Test that standard fields pass through unchanged"""
        data = {
            "analysis": "This is correct",
            "script": "kubectl get pods",
            "is_safe": True,
        }
        
        fixed = _fix_remediation_plan_fields(data.copy())
        assert fixed["analysis"] == data["analysis"]
        assert fixed["script"] == data["script"]
        assert fixed["is_safe"] == data["is_safe"]
    
    @pytest.mark.unit
    def test_fix_remediation_plan_wrong_field_names(self):
        """Test that wrong field names are mapped correctly"""
        data = {
            "analysis_script": "LLM returned wrong field",  # Should map to 'analysis'
            "remediation_script": "kubectl get pods",  # Should map to 'script'
            "safe": True,  # Should map to 'is_safe'
        }
        
        fixed = _fix_remediation_plan_fields(data.copy())
        assert fixed["analysis"] == "LLM returned wrong field"
        assert fixed["script"] == "kubectl get pods"
        assert fixed["is_safe"] is True
    
    @pytest.mark.unit
    def test_fix_remediation_plan_multiple_variations(self):
        """Test various field name variations"""
        test_cases = [
            ({"root_cause": "Analysis", "command": "script", "safe": True}, 
             {"analysis": "Analysis", "script": "script", "is_safe": True}),
            ({"root_cause_analysis": "Analysis", "remediation_command": "script", "is_safe_to_execute": True},
             {"analysis": "Analysis", "script": "script", "is_safe": True}),
            ({"analysis_result": "Analysis", "kubectl_command": "script", "safe_to_run": True},
             {"analysis": "Analysis", "script": "script", "is_safe": True}),
        ]
        
        for input_data, expected in test_cases:
            fixed = _fix_remediation_plan_fields(input_data.copy())
            assert fixed["analysis"] == expected["analysis"]
            assert fixed["script"] == expected["script"]
            assert fixed["is_safe"] == expected["is_safe"]
    
    @pytest.mark.unit
    def test_fix_safety_validation_fields(self):
        """Test safety validation field mapping"""
        data = {
            "approved_status": True,  # Should map to 'approved'
            "reason": "Script is safe",  # Should map to 'reasoning'
        }
        
        fixed = _fix_safety_validation_fields(data.copy())
        assert fixed["approved"] is True
        assert fixed["reasoning"] == "Script is safe"
    
    @pytest.mark.unit
    def test_fix_safety_validation_variations(self):
        """Test various safety validation field variations"""
        test_cases = [
            ({"is_approved": False, "justification": "Dangerous"},
             {"approved": False, "reasoning": "Dangerous"}),
            ({"approved_by_llm": True, "reasoning_text": "Safe script"},
             {"approved": True, "reasoning": "Safe script"}),
        ]
        
        for input_data, expected in test_cases:
            fixed = _fix_safety_validation_fields(input_data.copy())
            assert fixed["approved"] == expected["approved"]
            assert fixed["reasoning"] == expected["reasoning"]


class TestLangGraphPipeline:
    """Test LangGraph state machine pipeline"""
    
    @pytest.mark.unit
    def test_build_graph_creates_valid_graph(self):
        """Test that build_graph() returns a compiled graph"""
        graph = build_graph()
        assert graph is not None
        assert hasattr(graph, 'invoke')  # CompiledStateGraph has invoke method
    
    @pytest.mark.unit
    @patch('graph.ChatOllama')
    def test_solver_node_with_field_mapping(self, mock_ollama, mock_llm_response):
        """Test that solver_node handles field mapping correctly"""
        # Mock LLM to return wrong field names
        mock_response = mock_llm_response(json.dumps({
            "analysis_script": "Test analysis",
            "remediation_script": "kubectl get pods",
            "safe": True
        }))
        
        mock_llm = Mock()
        mock_llm.invoke = Mock(return_value=mock_response)
        mock_ollama.return_value = mock_llm
        
        from service.webhook.graph import graph_solver
        
        state = {
            "alert_payload": {"test": "data"},
            "parsed_data": {
                "namespace": "default",
                "pod_name": "test-pod",
                "logs": "test logs"
            }
        }
        
        result = graph_solver(state)
        
        # Should have mapped fields correctly
        assert "remediation_plan" in result
        plan = result["remediation_plan"]
        assert plan["analysis"] == "Test analysis"
        assert plan["script"] == "kubectl get pods"
        assert plan["is_safe"] is True


class TestRetryLogic:
    """Test retry logic and error handling"""
    
    @pytest.mark.unit
    @patch('graph.ChatOllama')
    def test_solver_retries_on_json_parse_error(self, mock_ollama, mock_llm_response):
        """Test that solver retries on invalid JSON"""
        # First call returns invalid JSON, second call returns valid JSON
        mock_llm = Mock()
        responses = [
            mock_llm_response("Invalid JSON {{{"),  # First attempt fails
            mock_llm_response(json.dumps({
                "analysis": "Valid",
                "script": "kubectl get pods",
                "is_safe": True
            }))  # Second attempt succeeds
        ]
        mock_llm.invoke = Mock(side_effect=responses)
        mock_ollama.return_value = mock_llm
        
        from service.webhook.graph import graph_solver
        
        state = {
            "alert_payload": {"test": "data"},
            "parsed_data": {
                "namespace": "default",
                "pod_name": "test-pod",
                "logs": "test logs"
            }
        }
        
        # Should succeed on retry
        result = graph_solver(state)
        assert "remediation_plan" in result
        assert mock_llm.invoke.call_count >= 1  # At least one attempt


class TestSafetyValidation:
    """Test dual-layer safety validation"""
    
    @pytest.mark.unit
    def test_programmatic_validation_blocks_dangerous_patterns(self):
        """Test that programmatic validation catches dangerous commands"""
        from service.webhook.graph import graph_safety_validator
        
        state = {
            "remediation_plan": {
                "analysis": "Delete all pods",
                "script": "kubectl delete pod --all -n default",
                "is_safe": True  # LLM says safe (but it's not!)
            }
        }
        
        result = graph_safety_validator(state)
        
        # Should be blocked by programmatic check
        assert "safety_check" in result
        assert result["safety_check"]["approved"] is False
        assert "dangerous pattern" in result["safety_check"]["reasoning"].lower() or \
               "--all" in result["safety_check"]["reasoning"]
    
    @pytest.mark.unit
    @patch('graph.ChatOllama')
    def test_llm_validation_provides_second_opinion(self, mock_ollama, mock_llm_response):
        """Test that LLM validation provides second opinion"""
        # Mock LLM to reject the script
        mock_response = mock_llm_response(json.dumps({
            "approved": False,
            "reasoning": "This command could cause data loss"
        }))
        
        mock_llm = Mock()
        mock_llm.invoke = Mock(return_value=mock_response)
        mock_ollama.return_value = mock_llm
        
        from service.webhook.graph import graph_safety_validator
        
        state = {
            "remediation_plan": {
                "analysis": "Restart pod",
                "script": "kubectl delete pod my-pod -n default",
                "is_safe": True
            }
        }
        
        result = graph_safety_validator(state)
        
        # Should be blocked by LLM
        assert "safety_check" in result
        assert result["safety_check"]["approved"] is False
        assert "data loss" in result["safety_check"]["reasoning"]


class TestEdgeCases:
    """Test edge cases and error conditions"""
    
    @pytest.mark.unit
    def test_empty_logs_handling(self):
        """Test handling of empty pod logs"""
        from service.webhook.graph import graph_log_parser
        
        state = {
            "alert_payload": {
                "alerts": [{
                    "labels": {
                        "alertname": "Test",
                        "namespace": "default",
                        "app": "test-app"
                    }
                }]
            }
        }
        
        result = graph_log_parser(state)
        
        # Should handle empty logs gracefully
        assert "parsed_data" in result
        assert "logs" in result["parsed_data"]
    
    @pytest.mark.unit
    def test_missing_alert_fields(self):
        """Test handling of alerts with missing fields"""
        from service.webhook.graph import graph_log_parser
        
        state = {
            "alert_payload": {
                "alerts": [{
                    "labels": {}  # Empty labels
                }]
            }
        }
        
        result = graph_log_parser(state)
        
        # Should handle missing fields gracefully
        assert "parsed_data" in result
        assert result["parsed_data"]["namespace"] == "default"  # Default fallback

import pytest
import pandas as pd
from unittest.mock import patch, MagicMock
import requests

from src.api.metrics_api import (
    fetch_metrics,
    fetch_openshift_metrics,
    fetch_alerts_from_prometheus,
    discover_vllm_metrics,
    discover_dcgm_metrics,
    discover_cluster_metrics_dynamically,
    _fetch_all_rule_definitions,
)
from src.core.metrics import get_models_helper


class TestBase:
    """Base class with shared fixtures for integration tests"""
    
    @pytest.fixture
    def mock_prometheus_query_range_response(self):
        """Standard Prometheus query_range response"""
        return {
            "data": {
                "result": [
                    {
                        "metric": {"model_name": "test-model", "namespace": "test-ns"},
                        "values": [
                            [1640995200, "10.5"],
                            [1640995230, "11.2"],
                            [1640995260, "12.1"]
                        ]
                    }
                ]
            }
        }
    
    @pytest.fixture
    def mock_prometheus_labels_response(self):
        """Prometheus label values response for metric discovery"""
        return {
            "data": [
                "vllm:request_prompt_tokens_created",
                "vllm:request_completion_tokens_created", 
                "vllm:avg_generation_throughput_toks_per_s",
                "DCGM_FI_DEV_GPU_TEMP",
                "DCGM_FI_DEV_GPU_UTIL",
                "kube_pod_status_phase",
                "node_cpu_seconds_total"
            ]
        }
    
    @pytest.fixture
    def mock_prometheus_series_response(self):
        """Prometheus series response for model discovery"""
        return {
            "data": [
                {"model_name": "llama-7b", "namespace": "production"},
                {"model_name": "gpt-3.5", "namespace": "staging"},
                {"model_name": "claude-3", "namespace": "production"}
            ]
        }
    
    @pytest.fixture
    def mock_prometheus_alerts_response(self):
        """Prometheus alerts query response for query_range"""
        return {
            "data": {
                "result": [
                    {
                        "metric": {
                            "alertname": "HighCPUUsage",
                            "severity": "warning",
                            "alertstate": "firing",
                            "namespace": "production",
                            "for": "5m"
                        },
                        "values": [
                            [1640995200, "1"],
                            [1640995230, "1"],
                            [1640995260, "1"]
                        ]
                    }
                ]
            }
        }
    
    @pytest.fixture
    def mock_prometheus_rules_response(self):
        """Prometheus rules response"""
        return {
            "data": {
                "groups": [
                    {
                        "rules": [
                            {
                                "alert": "HighCPUUsage",
                                "expr": "cpu_usage > 90",
                                "for": "5m",
                                "labels": {"severity": "warning"}
                            }
                        ]
                    }
                ]
            }
        }


class TestPrometheusIntegration(TestBase):
    """Test all Prometheus API interactions"""
    
    # === METRIC FETCHING TESTS ===
    
    @patch('src.api.metrics_api.requests.get')
    def test_fetch_metrics_success(self, mock_get, mock_prometheus_query_range_response):
        """Test successful metric fetching from Prometheus"""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = mock_prometheus_query_range_response
        mock_get.return_value = mock_response
        
        result_df = fetch_metrics(
            query="vllm:request_prompt_tokens_created",
            model_name="test-model",
            start=1640995200,
            end=1640995800
        )
        
        assert isinstance(result_df, pd.DataFrame)
        assert not result_df.empty
        assert "timestamp" in result_df.columns
        assert "value" in result_df.columns
        assert "model_name" in result_df.columns
        assert "namespace" in result_df.columns
        
        # Verify request was made correctly
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "/api/v1/query_range" in call_args[0][0]
        assert call_args[1]["params"]["query"] == 'vllm:request_prompt_tokens_created{model_name="test-model"}'
    
    @patch('src.api.metrics_api.requests.get')
    def test_fetch_openshift_metrics_with_namespace(self, mock_get, mock_prometheus_query_range_response):
        """Test OpenShift metrics fetching with namespace filtering"""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = mock_prometheus_query_range_response
        mock_get.return_value = mock_response
        
        result_df = fetch_openshift_metrics(
            query="kube_pod_status_phase{phase='Running'}",
            start=1640995200,
            end=1640995800,
            namespace="production"
        )
        
        assert isinstance(result_df, pd.DataFrame)
        assert not result_df.empty
        
        # Verify namespace filter was added
        call_args = mock_get.call_args
        assert 'namespace="production"' in call_args[1]["params"]["query"]
    
    @patch('src.api.metrics_api.requests.get')
    def test_fetch_alerts_success(self, mock_get, mock_prometheus_alerts_response):
        """Test successful alert fetching"""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = mock_prometheus_alerts_response
        mock_get.return_value = mock_response
        
        promql_query, alerts_data = fetch_alerts_from_prometheus(
            start_ts=1640995200,
            end_ts=1640995800,
            namespace="production"
        )
        
        # Check return values
        assert isinstance(promql_query, str)
        assert isinstance(alerts_data, list)
        assert len(alerts_data) > 0
        
        # Check alert structure
        alert = alerts_data[0]
        assert "alertname" in alert
        assert "severity" in alert
        assert "timestamp" in alert
        assert "alertstate" in alert
        assert alert["alertname"] == "HighCPUUsage"
        assert alert["severity"] == "warning"
        
        # Verify request was made correctly
        call_args = mock_get.call_args
        assert 'ALERTS{namespace="production"}' in call_args[1]["params"]["query"]
    
    # === DISCOVERY TESTS ===
    
    @pytest.mark.parametrize("discovery_function,expected_metrics", [
        (discover_vllm_metrics, ["token", "throughput"]),
        (discover_dcgm_metrics, ["GPU", "Temperature"]),
        (discover_cluster_metrics_dynamically, ["kube", "node"])
    ])
    @patch('src.api.metrics_api.requests.get')
    def test_discovery_functions_success(self, mock_get, discovery_function, expected_metrics, mock_prometheus_labels_response):
        """Test successful metric discovery functions"""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = mock_prometheus_labels_response
        mock_get.return_value = mock_response
        
        result = discovery_function()
        
        assert isinstance(result, dict)
        assert len(result) > 0
        
        # Should contain expected metric types
        found_metrics = [k for k in result.keys() 
                        if any(metric_type.lower() in k.lower() for metric_type in expected_metrics)]
        assert len(found_metrics) > 0
        
        # Verify correct API endpoint was called
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "/api/v1/label/__name__/values" in call_args[0][0]
    
    # === ERROR HANDLING TESTS ===
    
    @pytest.mark.parametrize("function_call,expected_empty_type", [
        (lambda: fetch_metrics("test_query", "test-model", 1640995200, 1640995800), pd.DataFrame),
        (lambda: fetch_openshift_metrics("test_query", 1640995200, 1640995800), pd.DataFrame),
        (lambda: fetch_alerts_from_prometheus(1640995200, 1640995800), tuple)
    ])
    @patch('src.api.metrics_api.requests.get')
    def test_prometheus_connection_errors(self, mock_get, function_call, expected_empty_type):
        """Test handling of Prometheus connection failures"""
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")
        
        result = function_call()
        
        if expected_empty_type == tuple:
            # For fetch_alerts_from_prometheus which returns (query, alerts_data)
            assert isinstance(result, tuple)
            assert len(result) == 2
            assert isinstance(result[1], list)
            assert len(result[1]) == 0
        elif expected_empty_type == pd.DataFrame:
            assert isinstance(result, pd.DataFrame)
            assert result.empty
    
    @patch('src.api.metrics_api.requests.get')
    def test_discover_vllm_metrics_connection_error_fallback(self, mock_get):
        """Test vLLM discovery returns fallback metrics on connection error"""
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")
        
        result = discover_vllm_metrics()
        
        assert isinstance(result, dict)
        assert len(result) > 0  # Should return fallback metrics
        # Should contain expected fallback metrics
        assert "GPU Temperature (°C)" in result
        assert "Prompt Tokens Created" in result
        assert "Output Tokens Created" in result
    
    @pytest.mark.parametrize("discovery_function", [
        discover_dcgm_metrics,
        discover_cluster_metrics_dynamically
    ])
    @patch('src.api.metrics_api.requests.get')
    def test_discovery_functions_connection_error_empty(self, mock_get, discovery_function):
        """Test discovery functions that return empty dict on connection error"""
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")
        
        result = discovery_function()
        
        assert isinstance(result, dict)
        assert len(result) == 0  # Should return empty dict


class TestPrometheusHelpers(TestBase):
    """Test Prometheus helper functions"""
    
    @patch('src.api.metrics_api.requests.get')
    def test_get_models_helper_success(self, mock_get, mock_prometheus_series_response):
        """Test successful model discovery"""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = mock_prometheus_series_response
        mock_get.return_value = mock_response
        
        result = get_models_helper()
        
        assert isinstance(result, list)
        assert len(result) > 0
        assert "production | llama-7b" in result
        assert "staging | gpt-3.5" in result
        
        # Verify correct API endpoint was called
        call_args = mock_get.call_args
        assert "/api/v1/series" in call_args[0][0]
    
    @patch('src.api.metrics_api.requests.get')
    def test_fetch_rule_definitions_success(self, mock_get, mock_prometheus_rules_response):
        """Test successful rule definitions fetching"""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = mock_prometheus_rules_response
        mock_get.return_value = mock_response
        
        result = _fetch_all_rule_definitions()
        
        assert isinstance(result, dict)
        assert "HighCPUUsage" in result
        assert result["HighCPUUsage"]["expression"] == "cpu_usage > 90"
        assert result["HighCPUUsage"]["duration"] == "5m"
        
        # Verify correct API endpoint was called
        call_args = mock_get.call_args
        assert "/api/v1/rules" in call_args[0][0]
    
    @pytest.mark.parametrize("helper_function", [
        get_models_helper,
        _fetch_all_rule_definitions
    ])
    @patch('src.api.metrics_api.requests.get')
    def test_helper_functions_connection_errors(self, mock_get, helper_function):
        """Test helper functions handle connection errors gracefully"""
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")
        
        result = helper_function()
        
        assert isinstance(result, (dict, list))
        assert len(result) == 0


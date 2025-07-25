import pytest
import pandas as pd
from datetime import datetime, timedelta

# Import functions still in API module
from metric_ui.api.mcp import (
    detect_anomalies,
    describe_trend,
    compute_health_score
)

# Import functions moved to core modules
from metric_ui.core.llm_client import (
    _clean_llm_summary_string,
    _validate_and_extract_response,
    extract_time_range_with_info,
    extract_time_range,
    add_namespace_filter,
    fix_promql_syntax,
    format_alerts_for_ui
)

from metric_ui.core.metrics import (
    calculate_metric_stats
)


class TestDetectAnomalies:
    """Test anomaly detection logic - core statistical analysis"""
    
    def test_empty_dataframe_returns_no_data(self):
        """Empty DataFrame should return 'No data'"""
        df = pd.DataFrame()
        result = detect_anomalies(df, "Test Metric")
        assert result == "No data"
    
    def test_spike_detection(self):
        """Detect metrics that spike above 90th percentile"""
        # Create data with clear spike at the end
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 50] 
        df = pd.DataFrame({"value": values})
        
        result = detect_anomalies(df, "CPU Usage")
        assert "⚠️ CPU Usage spike" in result
        assert "latest=50.00" in result
        assert ">90th pct" in result
    
    def test_low_value_detection(self):
        """Detect values below mean - standard deviation"""
        # Create data where latest value is unusually low
        values = [10, 10, 10, 10, 10, 10, 10, 10, 10, 2]
        df = pd.DataFrame({"value": values})
        
        result = detect_anomalies(df, "Request Rate")
        assert "⚠️ Request Rate unusually low" in result
        assert "latest=2.00" in result
        assert "mean=9.20" in result
    
    def test_stable_metric(self):
        """Stable metrics should return stable status"""
        values = [5, 5.1, 4.9, 5.2, 4.8, 5.0, 5.1, 4.9, 5.0, 5.0]
        df = pd.DataFrame({"value": values})
        
        result = detect_anomalies(df, "Memory Usage")
        assert "Memory Usage stable" in result
        assert "latest=5.00" in result


class TestDescribeTrend:
    """Test trend analysis using linear regression"""
    
    def test_empty_dataframe(self):
        """Empty DataFrame should return 'not enough data'"""
        df = pd.DataFrame()
        assert describe_trend(df) == "not enough data"
    
    def test_single_datapoint(self):
        """Single data point should return 'not enough data'"""
        df = pd.DataFrame({
            "timestamp": [datetime.now()],
            "value": [10]
        })
        assert describe_trend(df) == "not enough data"
    
    def test_increasing_trend(self):
        """Strong upward trend should be detected"""
        timestamps = [datetime.now() + timedelta(minutes=i) for i in range(10)]
        values = [i * 2 for i in range(10)]  # Clear increasing pattern
        df = pd.DataFrame({"timestamp": timestamps, "value": values})
        
        assert describe_trend(df) == "increasing"
    
    def test_decreasing_trend(self):
        """Strong downward trend should be detected"""
        timestamps = [datetime.now() + timedelta(minutes=i) for i in range(10)]
        values = [20 - i * 2 for i in range(10)]  # Clear decreasing pattern
        df = pd.DataFrame({"timestamp": timestamps, "value": values})
        
        assert describe_trend(df) == "decreasing"
    
    def test_stable_trend(self):
        """Small changes should be classified as stable"""
        timestamps = [datetime.now() + timedelta(minutes=i) for i in range(10)]
        values = [10 + 0.001 * i for i in range(10)]  # Very slight increase
        df = pd.DataFrame({"timestamp": timestamps, "value": values})
        
        assert describe_trend(df) == "stable"
    
    def test_flat_trend_identical_timestamps(self):
        """Identical timestamps should return 'flat'"""
        same_time = datetime.now()
        df = pd.DataFrame({
            "timestamp": [same_time, same_time, same_time],
            "value": [10, 11, 12]
        })
        assert describe_trend(df) == "flat"


class TestComputeHealthScore:
    """Test health scoring algorithm"""
    
    def test_healthy_system_zero_score(self):
        """Healthy metrics should result in score of 0"""
        metric_dfs = {
            "P95 Latency (s)": pd.DataFrame({"value": [0.5, 0.6, 0.7]}),  # Good latency
            "GPU Usage (%)": pd.DataFrame({"value": [80, 85, 90]}),        # Good GPU usage
            "Requests Running": pd.DataFrame({"value": [2, 3, 4]})         # Reasonable load
        }
        
        score, reasons = compute_health_score(metric_dfs)
        assert score == 0
        assert len(reasons) == 0
    
    def test_high_latency_penalty(self):
        """High latency should reduce health score by 2"""
        metric_dfs = {
            "P95 Latency (s)": pd.DataFrame({"value": [3.0, 3.5, 4.0]}),  # High latency
        }
        
        score, reasons = compute_health_score(metric_dfs)
        assert score == -2
        assert len(reasons) == 1
        assert "High Latency" in reasons[0]
        assert "avg=3.50" in reasons[0]
    
    def test_low_gpu_utilization_penalty(self):
        """Low GPU usage should reduce health score by 1"""
        metric_dfs = {
            "GPU Usage (%)": pd.DataFrame({"value": [5, 6, 7]}),  # Low GPU usage
        }
        
        score, reasons = compute_health_score(metric_dfs)
        assert score == -1
        assert len(reasons) == 1
        assert "Low GPU Utilization" in reasons[0]
        assert "avg=6.00" in reasons[0]
    
    def test_too_many_requests_penalty(self):
        """High request load should reduce health score by 1"""
        metric_dfs = {
            "Requests Running": pd.DataFrame({"value": [15, 20, 25]}),  # Too many requests
        }
        
        score, reasons = compute_health_score(metric_dfs)
        assert score == -1
        assert len(reasons) == 1
        assert "Too many requests" in reasons[0]
        assert "avg=20.00" in reasons[0]
    
    def test_multiple_issues_cumulative_penalty(self):
        """Multiple issues should result in cumulative penalties"""
        metric_dfs = {
            "P95 Latency (s)": pd.DataFrame({"value": [3.0, 3.5, 4.0]}),  # -2
            "GPU Usage (%)": pd.DataFrame({"value": [5, 6, 7]}),          # -1
            "Requests Running": pd.DataFrame({"value": [15, 20, 25]})     # -1
        }
        
        score, reasons = compute_health_score(metric_dfs)
        assert score == -4  # -2 + -1 + -1
        assert len(reasons) == 3
    
    def test_empty_dataframes_ignored(self):
        """Empty DataFrames should be ignored in health calculation"""
        metric_dfs = {
            "P95 Latency (s)": pd.DataFrame(),  # Empty - should be ignored
            "GPU Usage (%)": pd.DataFrame({"value": [80, 85, 90]}),  # Healthy
        }
        
        score, reasons = compute_health_score(metric_dfs)
        assert score == 0
        assert len(reasons) == 0


class TestCalculateMetricStats:
    """Test statistical calculations on metric data"""
    
    def test_empty_data_returns_none(self):
        """Empty data should return None values"""
        assert calculate_metric_stats([]) == (None, None)
        assert calculate_metric_stats(None) == (None, None)
    
    def test_valid_data_calculation(self):
        """Should correctly calculate average and max"""
        data = [
            {"value": 10, "timestamp": "2023-01-01T00:00:00Z"},
            {"value": 20, "timestamp": "2023-01-01T00:01:00Z"},
            {"value": 30, "timestamp": "2023-01-01T00:02:00Z"}
        ]
        
        avg, max_val = calculate_metric_stats(data)
        assert avg == 20.0  # (10 + 20 + 30) / 3
        assert max_val == 30.0
    
    def test_single_value_data(self):
        """Single data point should work correctly"""
        data = [{"value": 42, "timestamp": "2023-01-01T00:00:00Z"}]
        
        avg, max_val = calculate_metric_stats(data)
        assert avg == 42.0
        assert max_val == 42.0
    
    def test_malformed_data_returns_none(self):
        """Malformed data should return None safely"""
        malformed_data = [{"timestamp": "2023-01-01T00:00:00Z"}]  # Missing 'value' key
        
        avg, max_val = calculate_metric_stats(malformed_data)
        assert avg is None
        assert max_val is None


class TestValidateAndExtractResponse:
    """Test LLM response validation and extraction"""
    
    def test_external_openai_response(self):
        """Should extract content from OpenAI-style responses"""
        response_json = {
            "choices": [
                {
                    "message": {
                        "content": "This is the response content"
                    }
                }
            ]
        }
        
        result = _validate_and_extract_response(response_json, is_external=True, provider="openai")
        assert result == "This is the response content"
    
    def test_external_google_response(self):
        """Should extract content from Google Gemini responses"""
        response_json = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": "This is the Gemini response"
                            }
                        ]
                    }
                }
            ]
        }
        
        result = _validate_and_extract_response(response_json, is_external=True, provider="google")
        assert result == "This is the Gemini response"
    
    def test_local_model_response(self):
        """Should extract content from local model responses"""
        response_json = {
            "choices": [
                {
                    "text": "This is the local model response"
                }
            ]
        }
        
        result = _validate_and_extract_response(response_json, is_external=False)
        assert result == "This is the local model response"
    
    def test_invalid_response_raises_error(self):
        """Should raise ValueError for invalid responses"""
        invalid_response = {"invalid": "structure"}
        
        with pytest.raises(ValueError):
            _validate_and_extract_response(invalid_response, is_external=True)
    
    def test_missing_choices_raises_error(self):
        """Should raise error when choices are missing"""
        response_json = {"choices": []}
        
        with pytest.raises(ValueError):
            _validate_and_extract_response(response_json, is_external=True)


class TestCleanLlmSummaryString:
    """Test LLM response text cleaning"""
    
    @pytest.mark.parametrize("input_text,expected_output,test_description", [
        # Non-printable character removal
        ("Hello\x00\x01World🤖", "HelloWorld", "removes non-printable ASCII characters"),
        # Whitespace normalization
        ("Hello     World\n\n\nTest\t\t\tText", "Hello World Test Text", "normalizes multiple spaces/newlines/tabs to single spaces"),
        # Leading/trailing whitespace stripping
        ("   Hello World   ", "Hello World", "strips leading and trailing whitespace"),
        # Empty and whitespace-only strings
        ("", "", "handles empty string"),
        ("   \n\t   ", "", "handles whitespace-only string"),
        # Complex combination
        ("\x00  Hello\n\n\nWorld  \x01\t", "Hello World", "handles complex combination of issues"),
        # Normal text (no changes needed)
        ("Hello World", "Hello World", "preserves normal text unchanged")
    ])
    def test_clean_llm_summary_string(self, input_text, expected_output, test_description):
        """Test LLM response text cleaning functionality"""
        result = _clean_llm_summary_string(input_text)
        assert result == expected_output, f"Failed: {test_description}"


class TestTimeRangeParsing:
    """Test time range parsing and extraction functions"""
    
    def test_extract_time_range_with_info_basic_parsing(self):
        """Should parse common time expressions"""
        query = "cpu usage in the past 30 minutes"
        start_ts, end_ts, info = extract_time_range_with_info(query, None, None)

        assert isinstance(start_ts, int)
        assert isinstance(end_ts, int)
        assert end_ts > start_ts
        assert info["duration_str"] == "past 30 minutes"
        assert info["rate_syntax"] == "30m"

    def test_extract_time_range_with_info_with_provided_timestamps(self):
        """Should use provided timestamps when no time in query"""
        query = "cpu usage data"  # Simple query that won't trigger dateparser
        start_ts = 1640995200
        end_ts = 1640998800  # 1 hour later

        result_start, result_end, info = extract_time_range_with_info(query, start_ts, end_ts)

        assert result_start == start_ts
        assert result_end == end_ts
        assert info["duration_str"] == "past 1 hour"

    def test_extract_time_range_wrapper(self):
        """extract_time_range should work as a simple wrapper"""
        query = "metrics from past 2 hours"
        start_ts, end_ts = extract_time_range(query, None, None)

        assert isinstance(start_ts, int)
        assert isinstance(end_ts, int)
        assert end_ts > start_ts


class TestPromQLManipulation:
    """Test PromQL query manipulation functions"""

    @pytest.mark.parametrize("promql,namespace,expected", [
        # No existing labels - should add namespace
        ("cpu_usage", "production", 'cpu_usage{namespace="production"}'),
        # Existing labels - should insert namespace
        ('cpu_usage{job="app"}', "production", 'cpu_usage{namespace="production", job="app"}'),
        # Namespace already exists - should remain unchanged
        ('cpu_usage{namespace="production", job="app"}', "production", 'cpu_usage{namespace="production", job="app"}'),
        # Complex query with multiple labels
        ('http_requests{method="GET", status="200"}', "staging", 'http_requests{namespace="staging", method="GET", status="200"}'),
        # Different namespace values
        ("memory_usage", "test-env", 'memory_usage{namespace="test-env"}')
    ])
    def test_add_namespace_filter(self, promql, namespace, expected):
        """Should correctly add or preserve namespace filters in PromQL queries"""
        result = add_namespace_filter(promql, namespace)
        assert result == expected

    def test_fix_promql_syntax_trailing_commas(self):
        """Should remove trailing commas in label selectors"""
        promql = 'cpu_usage{job="app",}'
        result = fix_promql_syntax(promql)
        assert result == 'cpu_usage{job="app"}'

    def test_fix_promql_syntax_add_rate_time_range(self):
        """Should add time range to rate functions"""
        promql = 'rate(http_requests_total)'
        result = fix_promql_syntax(promql, "10m")
        assert result == 'rate(http_requests_total[10m])'


class TestAlertFormatting:
    """Test alert formatting for UI display"""

    def test_format_alerts_for_ui_empty_alerts(self):
        """Should handle empty alerts list gracefully"""
        result = format_alerts_for_ui("cpu_usage > 90", [])

        assert "PromQL Query for Alerts: `cpu_usage > 90`" in result
        assert "No relevant alerts were firing" in result

    def test_format_alerts_for_ui_with_basic_alerts(self):
        """Should format basic alerts correctly"""
        alerts_data = [
            {
                "alertname": "HighCPU",
                "severity": "warning",
                "timestamp": "2023-01-01T12:00:00",
                "labels": {"pod": "app-123", "namespace": "production"}
            }
        ]

        result = format_alerts_for_ui("cpu_usage > 90", alerts_data)

        assert "PromQL Query for Alerts: `cpu_usage > 90`" in result
        assert "**HighCPU**" in result
        assert "Severity: **warning**" in result
        assert "Pod: `app-123`" in result
        assert "Namespace: `production`" in result
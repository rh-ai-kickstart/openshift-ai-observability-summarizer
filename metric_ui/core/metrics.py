"""
Metrics collection and processing functions

Contains all business logic for interacting with Prometheus/Thanos,
collecting vLLM metrics, and processing observability data.
"""

import requests
from datetime import datetime
from typing import List

from .config import PROMETHEUS_URL, THANOS_TOKEN, VERIFY_SSL


def get_models_helper() -> List[str]:
    """
    Get list of available vLLM models from Prometheus metrics.
    
    Returns:
        List of model names in format "namespace | model_name"
    """
    try:
        headers = {"Authorization": f"Bearer {THANOS_TOKEN}"}

        # Try multiple vLLM metrics with longer time windows
        vllm_metrics_to_check = [
            "vllm:request_prompt_tokens_created",
            "vllm:request_prompt_tokens_total",
            "vllm:avg_generation_throughput_toks_per_s",
            "vllm:num_requests_running",
            "vllm:gpu_cache_usage_perc",
        ]

        model_set = set()

        # Try different time windows: 7 days, 24 hours, 1 hour
        time_windows = [7 * 24 * 3600, 24 * 3600, 3600]  # 7 days, 24 hours, 1 hour

        for time_window in time_windows:
            for metric_name in vllm_metrics_to_check:
                try:
                    response = requests.get(
                        f"{PROMETHEUS_URL}/api/v1/series",
                        headers=headers,
                        params={
                            "match[]": metric_name,
                            "start": int((datetime.now().timestamp()) - time_window),
                            "end": int(datetime.now().timestamp()),
                        },
                        verify=VERIFY_SSL,
                    )
                    response.raise_for_status()
                    series = response.json()["data"]

                    for entry in series:
                        model = entry.get("model_name", "").strip()
                        namespace = entry.get("namespace", "").strip()
                        if model and namespace:
                            model_set.add(f"{namespace} | {model}")

                    # If we found models, return them
                    if model_set:
                        return sorted(list(model_set))

                except Exception as e:
                    print(
                        f"Error checking {metric_name} with {time_window}s window: {e}"
                    )
                    continue

        return sorted(list(model_set))
    except Exception as e:
        print("Error getting models:", e)
        return []


def calculate_metric_stats(data):
    """
    Calculate basic statistics (average and max) from metric data.
    
    Args:
        data: List of dictionaries with 'value' and 'timestamp' keys
        
    Returns:
        Tuple of (average, max) or (None, None) for invalid data
    """
    if not data or data is None:
        return (None, None)
    
    try:
        values = [item.get("value") for item in data if "value" in item]
        if not values:
            return (None, None)
            
        avg = sum(values) / len(values)
        max_val = max(values)
        return (float(avg), float(max_val))
    except (TypeError, ValueError, KeyError):
        return (None, None) 
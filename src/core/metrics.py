"""
Metrics collection and processing functions

Contains all business logic for interacting with Prometheus/Thanos,
collecting vLLM metrics, and processing observability data.
"""

import requests
import pandas as pd
import os
import json
import re
from datetime import datetime
from typing import List, Dict, Any, Optional

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


def get_namespaces_helper() -> List[str]:
    """
    Get list of namespaces that have vLLM metrics available.

    Mirrors the logic used in the FastAPI /namespaces endpoint to ensure
    consistent behavior across API and MCP tools.

    Returns:
        Sorted list of namespace names
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

        namespace_set = set()

        # Try different time windows: 7 days, 24 hours, 1 hour
        time_windows = [7 * 24 * 3600, 24 * 3600, 3600]

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
                        namespace = entry.get("namespace", "").strip()
                        model = entry.get("model_name", "").strip()
                        if namespace and model:
                            namespace_set.add(namespace)

                    # If we found namespaces, return them immediately
                    if namespace_set:
                        return sorted(list(namespace_set))

                except Exception as e:
                    print(
                        f"Error checking {metric_name} with {time_window}s window: {e}"
                    )
                    continue

        return sorted(list(namespace_set))
    except Exception as e:
        print("Error getting namespaces:", e)
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


# --- Metric Discovery Functions ---

def discover_vllm_metrics():
    """Dynamically discover available vLLM metrics from Prometheus, including GPU metrics"""
    try:
        headers = {"Authorization": f"Bearer {THANOS_TOKEN}"}
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/label/__name__/values",
            headers=headers,
            verify=VERIFY_SSL,
            timeout=30,  # Add timeout
        )
        response.raise_for_status()
        all_metrics = response.json()["data"]

        # Create friendly names for metrics
        metric_mapping = {}

        # First, add GPU metrics (DCGM) that are relevant for vLLM monitoring
        gpu_metrics = {
            "GPU Temperature (°C)": "DCGM_FI_DEV_GPU_TEMP",
            "GPU Power Usage (Watts)": "DCGM_FI_DEV_POWER_USAGE",
            "GPU Memory Usage (GB)": "DCGM_FI_DEV_FB_USED / (1024*1024*1024)",
            "GPU Energy Consumption (Joules)": "DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION",
            "GPU Memory Temperature (°C)": "DCGM_FI_DEV_MEMORY_TEMP",
            "GPU Utilization (%)": "DCGM_FI_DEV_GPU_UTIL",
        }

        for friendly_name, metric_name in gpu_metrics.items():
            if metric_name in all_metrics:
                metric_mapping[friendly_name] = f"avg({metric_name})"

        # Filter for vLLM metrics
        vllm_metrics = [metric for metric in all_metrics if metric.startswith("vllm:")]

        # Add vLLM-specific metrics
        for metric in vllm_metrics:
            # Convert metric name to friendly display name
            friendly_name = metric.replace("vllm:", "").replace("_", " ").title()

            # Special handling for common metrics
            if "token" in metric.lower() and "prompt" in metric.lower():
                friendly_name = "Prompt Tokens Created"
            elif "token" in metric.lower() and (
                "generation" in metric.lower() or "output" in metric.lower()
            ):
                friendly_name = "Output Tokens Created"
            elif "latency" in metric.lower() and "e2e" in metric.lower():
                friendly_name = "P95 Latency (s)"
            elif (
                "gpu" in metric.lower()
                and "usage" in metric.lower()
                and "perc" in metric.lower()
            ):
                friendly_name = "GPU Usage (%)"
            elif "request" in metric.lower() and "running" in metric.lower():
                friendly_name = "Requests Running"
            elif "inference" in metric.lower() and "time" in metric.lower():
                friendly_name = "Inference Time (s)"
            else:
                # Keep original friendly conversion
                friendly_name = metric.replace("vllm:", "").replace("_", " ").title()

            metric_mapping[friendly_name] = metric

        return metric_mapping
    except Exception as e:
        print(f"Error discovering vLLM metrics: {e}")
        # Enhanced fallback with comprehensive GPU metrics and vLLM metrics
        return {
            "GPU Temperature (°C)": "avg(DCGM_FI_DEV_GPU_TEMP)",
            "GPU Power Usage (Watts)": "avg(DCGM_FI_DEV_POWER_USAGE)",
            "GPU Memory Usage (GB)": "avg(DCGM_FI_DEV_FB_USED) / (1024*1024*1024)",
            "GPU Energy Consumption (Joules)": "avg(DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION)",
            "GPU Memory Temperature (°C)": "avg(DCGM_FI_DEV_MEMORY_TEMP)",
            "GPU Utilization (%)": "avg(DCGM_FI_DEV_GPU_UTIL)",
            "Prompt Tokens Created": "vllm:request_prompt_tokens_created",
            "Output Tokens Created": "vllm:request_generation_tokens_created",
            "Requests Running": "vllm:num_requests_running",
            "P95 Latency (s)": "vllm:e2e_request_latency_seconds_count",
            "Inference Time (s)": "vllm:request_inference_time_seconds_count",
        }


def discover_dcgm_metrics():
    """Dynamically discover available GPU metrics (DCGM, nvidia_smi, or alternatives)"""
    try:
        headers = {"Authorization": f"Bearer {THANOS_TOKEN}"}
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/label/__name__/values",
            headers=headers,
            verify=VERIFY_SSL,
            timeout=30,  # Add timeout
        )
        response.raise_for_status()
        all_metrics = response.json()["data"]

        # Filter for different types of GPU metrics
        dcgm_metrics = [metric for metric in all_metrics if metric.startswith("DCGM_")]
        nvidia_metrics = [metric for metric in all_metrics if "nvidia" in metric.lower()]
        gpu_metrics = [metric for metric in all_metrics if "gpu" in metric.lower() and not metric.startswith("vllm:")]

        print(f"🔍 Found {len(dcgm_metrics)} DCGM metrics, {len(nvidia_metrics)} NVIDIA metrics, {len(gpu_metrics)} GPU metrics")

        # Create a mapping of useful GPU metrics for fleet monitoring
        gpu_mapping = {}
        fb_used_metric = None

        # Priority 1: DCGM metrics (most comprehensive)
        for metric in dcgm_metrics:
            if "GPU_TEMP" in metric:
                gpu_mapping["GPU Temperature (°C)"] = f"avg({metric})"
            elif "POWER_USAGE" in metric:
                gpu_mapping["GPU Power Usage (Watts)"] = f"avg({metric})"
            elif "GPU_UTIL" in metric:
                gpu_mapping["GPU Utilization (%)"] = f"avg({metric})"
            elif "MEMORY_TEMP" in metric:
                gpu_mapping["GPU Memory Temperature (°C)"] = f"avg({metric})"
            elif "TOTAL_ENERGY_CONSUMPTION" in metric:
                gpu_mapping["GPU Energy Consumption (Joules)"] = f"avg({metric})"
            elif "FB_USED" in metric:
                fb_used_metric = metric
                gpu_mapping["GPU Memory Used (bytes)"] = f"avg({metric})"
            elif "FB_TOTAL" in metric:
                gpu_mapping["GPU Memory Total (bytes)"] = f"avg({metric})"
            elif "SM_CLOCK" in metric:
                gpu_mapping["GPU SM Clock (MHz)"] = f"avg({metric})"
            elif "MEM_CLOCK" in metric:
                gpu_mapping["GPU Memory Clock (MHz)"] = f"avg({metric})"

        # Add GPU Memory Usage in GB if we found the FB_USED metric
        if fb_used_metric:
            gpu_mapping["GPU Memory Usage (GB)"] = (
                f"avg({fb_used_metric}) / (1024*1024*1024)"
            )

        # Priority 2: nvidia-smi or alternative metrics if DCGM not available
        if not gpu_mapping:
            print("🔍 No DCGM metrics found, checking for alternative GPU metrics...")
            
            # Look for common GPU metric patterns
            gpu_patterns = {
                "GPU Temperature (°C)": ["nvidia_smi_temperature", "gpu_temperature", "gpu_temp"],
                "GPU Utilization (%)": ["nvidia_smi_utilization", "gpu_utilization", "gpu_usage_percent"],
                "GPU Power Usage (Watts)": ["nvidia_smi_power", "gpu_power", "gpu_power_usage"],
                "GPU Memory Usage (%)": ["nvidia_smi_memory_used", "gpu_memory_usage", "gpu_mem_used"],
                "GPU Memory Free (bytes)": ["nvidia_smi_memory_free", "gpu_memory_free"],
                "GPU Fan Speed (%)": ["nvidia_smi_fan_speed", "gpu_fan"],
            }
            
            for friendly_name, pattern_list in gpu_patterns.items():
                for pattern in pattern_list:
                    matching_metrics = [m for m in all_metrics if pattern in m.lower()]
                    if matching_metrics:
                        # Use the first matching metric
                        gpu_mapping[friendly_name] = f"avg({matching_metrics[0]})"
                        print(f"✅ Found alternative GPU metric: {friendly_name} -> {matching_metrics[0]}")
                        break

        # Priority 3: Generic GPU metrics
        if not gpu_mapping:
            print("🔍 No specific GPU metrics found, checking for generic patterns...")
            for metric in gpu_metrics:
                metric_lower = metric.lower()
                if "temperature" in metric_lower or "temp" in metric_lower:
                    gpu_mapping["GPU Temperature"] = f"avg({metric})"
                elif "utilization" in metric_lower or "usage" in metric_lower:
                    gpu_mapping["GPU Utilization"] = f"avg({metric})"
                elif "power" in metric_lower:
                    gpu_mapping["GPU Power"] = f"avg({metric})"
                elif "memory" in metric_lower and "used" in metric_lower:
                    gpu_mapping["GPU Memory Used"] = f"avg({metric})"

        if gpu_mapping:
            print(f"✅ Successfully discovered {len(gpu_mapping)} GPU metrics")
        else:
            print("⚠️ No GPU metrics found - cluster may not have GPUs or GPU monitoring")

        return gpu_mapping
    except Exception as e:
        print(f"Error discovering GPU metrics: {e}")
        return {}


def discover_openshift_metrics():
    """Return comprehensive OpenShift/Kubernetes metrics organized by category"""
    return {
        "Fleet Overview": {
            # Core cluster-wide metrics
            "Total Pods Running": "sum(kube_pod_status_phase{phase='Running'})",
            "Total Pods Failed": "sum(kube_pod_status_phase{phase='Failed'})",
            "Total Deployments": "sum(kube_deployment_status_replicas_ready)",
            "Cluster CPU Usage (%)": "100 - (avg(rate(node_cpu_seconds_total{mode='idle'}[5m])) * 100)",
            "Cluster Memory Usage (%)": "100 - (sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes) * 100)",
            "Container Images": "count(count by (image)(container_spec_image))",
            "Total Services": "sum(kube_service_info)",
            "Total Nodes": "sum(kube_node_info)",
            # Key GPU metrics for fleet overview
            "GPU Utilization (%)": "avg(DCGM_FI_DEV_GPU_UTIL)",
            "GPU Temperature (°C)": "avg(DCGM_FI_DEV_GPU_TEMP)",
        },
        "Services & Networking": {
            # Services, ingress, and networking metrics
            "Services Running": "sum(kube_service_info)",
            "Service Endpoints": "sum(kube_endpoint_address_available)",
            "Ingress Rules": "sum(kube_ingress_info)",
            "Network Policies": "sum(kube_networkpolicy_labels)",
            "Load Balancer Services": "sum(kube_service_spec_type{type='LoadBalancer'})",
            "ClusterIP Services": "sum(kube_service_spec_type{type='ClusterIP'})",
        },
        "Jobs & Workloads": {
            # Jobs, cronjobs, and other workload types
            "Jobs Running": "sum(kube_job_status_active)",
            "Jobs Completed": "sum(kube_job_status_succeeded)",
            "Jobs Failed": "sum(kube_job_status_failed)", 
            "CronJobs": "sum(kube_cronjob_info)",
            "DaemonSets": "sum(kube_daemonset_status_number_ready)",
            "StatefulSets": "sum(kube_statefulset_status_replicas_ready)",
        },
        "Storage & Config": {
            # Storage and configuration resources
            "Persistent Volumes": "sum(kube_persistentvolume_info)",
            "PV Claims": "sum(kube_persistentvolumeclaim_info)",
            "ConfigMaps": "sum(kube_configmap_info)",
            "Secrets": "sum(kube_secret_info)",
            "Storage Classes": "sum(kube_storageclass_info)",
            "Volume Snapshots": "sum(kube_volumesnapshot_info)",
        },
        "Workloads & Pods": {
            # 6 most important pod/container metrics
            "Pods Running": "sum(kube_pod_status_phase{phase='Running'})",
            "Pods Pending": "sum(kube_pod_status_phase{phase='Pending'})",
            "Pods Failed": "sum(kube_pod_status_phase{phase='Failed'})",
            "Pod Restarts (Rate)": "sum(rate(kube_pod_container_status_restarts_total[5m]))",
            "Container CPU Usage": "sum(rate(container_cpu_usage_seconds_total[5m]))",
            "Container Memory Usage": "sum(container_memory_usage_bytes)",
        },
        "GPU & Accelerators": {
            # 🚀 Comprehensive GPU fleet monitoring with DCGM metrics
            "GPU Temperature (°C)": "avg(DCGM_FI_DEV_GPU_TEMP)",
            "GPU Power Usage (Watts)": "avg(DCGM_FI_DEV_POWER_USAGE)",
            "GPU Utilization (%)": "avg(DCGM_FI_DEV_GPU_UTIL)",
            "GPU Memory Usage (GB)": "avg(DCGM_FI_DEV_FB_USED) / (1024*1024*1024)",
            "GPU Energy Consumption (Joules)": "avg(DCGM_FI_DEV_TOTAL_ENERGY_CONSUMPTION)",
            "GPU Memory Temperature (°C)": "avg(DCGM_FI_DEV_MEMORY_TEMP)",
        },
        "Storage & Networking": {
            # 6 storage and network metrics
            "PV Available Space": "sum(kube_persistentvolume_capacity_bytes)",
            "PVC Bound": "sum(kube_persistentvolumeclaim_status_phase{phase='Bound'})",
            "Storage I/O Rate": "sum(rate(container_fs_reads_total[5m]) + rate(container_fs_writes_total[5m]))",
            "Network Receive Rate": "sum(rate(container_network_receive_bytes_total[5m]))",
            "Network Transmit Rate": "sum(rate(container_network_transmit_bytes_total[5m]))",
            "Network Errors": "sum(rate(container_network_receive_errors_total[5m]) + rate(container_network_transmit_errors_total[5m]))",
        },
        "Application Services": {
            # 6 application-level metrics
            "HTTP Request Rate": "sum(rate(http_requests_total[5m]))",
            "HTTP Error Rate (%)": "sum(rate(http_requests_total{status=~'5..'}[5m])) / sum(rate(http_requests_total[5m])) * 100",
            "HTTP P95 Latency": "histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))",
            "Services Available": "sum(up)",
            "Ingress Request Rate": "sum(rate(nginx_ingress_controller_requests[5m]))",
            "Load Balancer Backends": "sum(haproxy_server_up)",
        },
    } 


# Cache discovered metrics to avoid repeated API calls
_vllm_metrics_cache = None
_openshift_metrics_cache = None
_cache_timestamp = None
CACHE_TTL = 300  # 5 minutes


def get_vllm_metrics():
    """Get vLLM metrics with caching"""
    global _vllm_metrics_cache, _cache_timestamp

    current_time = datetime.now().timestamp()
    if (
        _vllm_metrics_cache is None
        or _cache_timestamp is None
        or (current_time - _cache_timestamp) > CACHE_TTL
    ):
        _vllm_metrics_cache = discover_vllm_metrics()
        _cache_timestamp = current_time

    return _vllm_metrics_cache


def get_openshift_metrics():
    """Get OpenShift metrics with caching"""
    global _openshift_metrics_cache, _cache_timestamp

    current_time = datetime.now().timestamp()
    if (
        _openshift_metrics_cache is None
        or _cache_timestamp is None
        or (current_time - _cache_timestamp) > CACHE_TTL
    ):
        _openshift_metrics_cache = discover_openshift_metrics()
        _cache_timestamp = current_time

    return _openshift_metrics_cache


def discover_cluster_metrics_dynamically():
    """Dynamically discover cluster metrics from Prometheus"""
    try:
        headers = {"Authorization": f"Bearer {THANOS_TOKEN}"}
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/label/__name__/values",
            headers=headers,
            verify=VERIFY_SSL,
            timeout=30,
        )
        response.raise_for_status()
        all_metrics = response.json()["data"]

        # Filter for Kubernetes/OpenShift metrics
        cluster_metrics = {}
        kube_prefixes = ["kube_", "node_", "container_", "apiserver_", "etcd_", "scheduler_", "kubelet_"]
        
        for metric in all_metrics:
            if any(metric.startswith(prefix) for prefix in kube_prefixes):
                # Create a friendly name
                friendly_name = metric.replace("_", " ").title()
                cluster_metrics[friendly_name] = f"sum({metric})"

        # Limit to first 50 metrics to avoid overwhelming UI
        limited_metrics = dict(list(cluster_metrics.items())[:50])
        return limited_metrics
    except Exception as e:
        print(f"Error discovering cluster metrics: {e}")
        return {}


def get_all_metrics():
    """Get all available metrics (vLLM, OpenShift, GPU) combined"""
    all_metrics = {}
    
    # Add vLLM metrics
    vllm_metrics = get_vllm_metrics()
    for label, query in vllm_metrics.items():
        all_metrics[f"vLLM: {label}"] = query
    
    # Add GPU/DCGM metrics
    dcgm_metrics = discover_dcgm_metrics()
    for label, query in dcgm_metrics.items():
        all_metrics[f"GPU: {label}"] = query
    
    # Add OpenShift metrics (flattened from categories)
    openshift_metrics = get_openshift_metrics()
    for category, metrics in openshift_metrics.items():
        for label, query in metrics.items():
            all_metrics[f"OpenShift {category}: {label}"] = query
    
    return all_metrics


def get_namespace_specific_metrics(category):
    """Get metrics that actually have namespace labels for namespace-specific analysis"""

    namespace_aware_metrics = {
        "Fleet Overview": {
            # Metrics that work with namespace filtering
            "Deployment Replicas Ready": "sum(kube_deployment_status_replicas_ready)",
            "Pods Running": "sum(kube_pod_status_phase{phase='Running'})",
            "Pods Failed": "sum(kube_pod_status_phase{phase='Failed'})",
            "Container CPU Usage": "sum(rate(container_cpu_usage_seconds_total[5m]))",
            "Container Memory Usage": "sum(container_memory_usage_bytes)",
            "Pod Restart Rate": "sum(rate(kube_pod_container_status_restarts_total[5m]))",
        },
        "Workloads & Pods": {
            # Pod and container metrics naturally have namespace labels
            "Pods Running": "sum(kube_pod_status_phase{phase='Running'})",
            "Pods Pending": "sum(kube_pod_status_phase{phase='Pending'})",
            "Pods Failed": "sum(kube_pod_status_phase{phase='Failed'})",
            "Pod Restarts (Rate)": "sum(rate(kube_pod_container_status_restarts_total[5m]))",
            "Container CPU Usage": "sum(rate(container_cpu_usage_seconds_total[5m]))",
            "Container Memory Usage": "sum(container_memory_usage_bytes)",
        },
        "Compute & Resources": {
            # Container-level compute and resource metrics
            "Container CPU Throttling": "sum(container_cpu_cfs_throttled_seconds_total)",
            "Container Memory Failures": "sum(container_memory_failcnt)",
            "OOM Events": "sum(container_oom_events_total)",
            "Container Processes": "sum(container_processes)",
            "Container Threads": "sum(container_threads)",
            "Container File Descriptors": "sum(container_file_descriptors)",
        },
        "Storage & Networking": {
            # Storage and network metrics that have namespace context
            "PV Claims Bound": "sum(kube_persistentvolumeclaim_status_phase{phase='Bound'})",
            "PV Claims Pending": "sum(kube_persistentvolumeclaim_status_phase{phase='Pending'})",
            "Container Network Receive": "sum(rate(container_network_receive_bytes_total[5m]))",
            "Container Network Transmit": "sum(rate(container_network_transmit_bytes_total[5m]))",
            "Network Errors": "sum(rate(container_network_receive_errors_total[5m]) + rate(container_network_transmit_errors_total[5m]))",
            "Filesystem Usage": "sum(container_fs_usage_bytes)",
        },
        "Application Services": {
            # Application metrics that work at namespace level
            "HTTP Request Rate": "sum(rate(http_requests_total[5m]))",
            "HTTP Error Rate (%)": "sum(rate(http_requests_total{status=~'5..'}[5m])) / sum(rate(http_requests_total[5m])) * 100",
            "Available Endpoints": "sum(kube_endpoint_address_available)",
            "Container Processes": "sum(container_processes)",
            "Container File Descriptors": "sum(container_file_descriptors)",
            "Container Threads": "sum(container_threads)",
        },
    }

    return namespace_aware_metrics.get(category, {})


# --- Metric Fetching Functions ---

def fetch_metrics(query, model_name, start, end, namespace=None):
    """Fetch metrics from Prometheus for vLLM models"""
    # Handle GPU metrics that don't have model_name labels (they're global/node-level metrics)
    if query.startswith("avg(DCGM_") or "DCGM_" in query:
        # GPU metrics are node-level, not model-specific
        promql_query = query
    else:
        # Handle vLLM metrics that have model_name and namespace labels
        if namespace:
            namespace = namespace.strip()
            if "|" in model_name:
                model_namespace, actual_model_name = map(
                    str.strip, model_name.split("|", 1)
                )
                promql_query = f'{query}{{model_name="{actual_model_name}", namespace="{namespace}"}}'
            else:
                promql_query = (
                    f'{query}{{model_name="{model_name}", namespace="{namespace}"}}'
                )
        else:
            # Original logic if no namespace is explicitly provided (for backward compatibility or other endpoints)
            if "|" in model_name:
                namespace, model_name = map(str.strip, model_name.split("|", 1))
                promql_query = (
                    f'{query}{{model_name="{model_name}", namespace="{namespace}"}}'
                )
            else:
                model_name = model_name.strip()
                promql_query = f'{query}{{model_name="{model_name}"}}'

    headers = {"Authorization": f"Bearer {THANOS_TOKEN}"}
    try:
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query_range",
            headers=headers,
            params={"query": promql_query, "start": start, "end": end, "step": "30s"},
            verify=VERIFY_SSL,
            timeout=30,  # Add timeout
        )
        response.raise_for_status()
        result = response.json()["data"]["result"]
    except requests.exceptions.ConnectionError as e:
        print(f"⚠️ Prometheus connection error for query '{promql_query}': {e}")
        return pd.DataFrame()  # Return empty DataFrame on connection error
    except requests.exceptions.Timeout as e:
        print(f"⚠️ Prometheus timeout for query '{promql_query}': {e}")
        return pd.DataFrame()  # Return empty DataFrame on timeout
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Prometheus request error for query '{promql_query}': {e}")
        return pd.DataFrame()  # Return empty DataFrame on other request errors

    rows = []
    for series in result:
        for val in series["values"]:
            ts = datetime.fromtimestamp(float(val[0]))
            value = float(val[1])

            # Handle NaN values that can't be JSON serialized
            if pd.isna(value) or value != value:  # Check for NaN
                value = 0.0  # Convert NaN to 0 for JSON compatibility

            row = dict(series["metric"])
            row["timestamp"] = ts
            row["value"] = value
            rows.append(row)

    return pd.DataFrame(rows)


def fetch_openshift_metrics(query, start, end, namespace=None):
    """Fetch OpenShift metrics with optional namespace filtering"""
    headers = {"Authorization": f"Bearer {THANOS_TOKEN}"}

    # Add namespace filter to the query if specified
    if namespace:
        # Skip if namespace already exists in the query
        if f'namespace="{namespace}"' in query:
            pass  # Already has the correct namespace
        else:
            # Simple string replacements for common patterns

            # Pattern 1: sum(metric_name)
            pattern1 = r"sum\(([a-zA-Z_:][a-zA-Z0-9_:]*)\)"
            if re.search(pattern1, query):
                query = re.sub(pattern1, f'sum(\\1{{namespace="{namespace}"}})', query)

            # Pattern 2: sum(rate(metric_name[5m]))
            elif re.search(r"sum\(rate\([a-zA-Z_:][a-zA-Z0-9_:]*\[[^\]]+\]\)\)", query):
                pattern2 = r"sum\(rate\(([a-zA-Z_:][a-zA-Z0-9_:]*)\[([^\]]+)\]\)\)"
                query = re.sub(
                    pattern2, f'sum(rate(\\1{{namespace="{namespace}"}}[\\2]))', query
                )

            # Pattern 3: rate(metric_name[5m])
            elif re.search(r"rate\([a-zA-Z_:][a-zA-Z0-9_:]*\[[^\]]+\]\)", query):
                pattern3 = r"rate\(([a-zA-Z_:][a-zA-Z0-9_:]*)\[([^\]]+)\]\)"
                query = re.sub(
                    pattern3, f'rate(\\1{{namespace="{namespace}"}}[\\2])', query
                )

            # Pattern 4: metric_name{existing_labels}
            elif re.search(r"[a-zA-Z_:][a-zA-Z0-9_:]*\{[^}]*\}", query):
                pattern4 = r"([a-zA-Z_:][a-zA-Z0-9_:]*)\{([^}]*)\}"
                query = re.sub(pattern4, f'\\1{{namespace="{namespace}",\\2}}', query)

            # Pattern 5: simple metric_name (no labels)
            elif re.search(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$", query):
                query = f'{query}{{namespace="{namespace}"}}'

            # Pattern 6: handle other aggregations (avg, count, etc.)
            else:
                for func in ["avg", "count", "max", "min"]:
                    pattern = f"{func}\\(([a-zA-Z_:][a-zA-Z0-9_:]*)\\)"
                    if re.search(pattern, query):
                        query = re.sub(
                            pattern, f'{func}(\\1{{namespace="{namespace}"}})', query
                        )
                        break

    try:
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query_range",
            headers=headers,
            params={"query": query, "start": start, "end": end, "step": "30s"},
            verify=VERIFY_SSL,
            timeout=30,  # Add timeout
        )
        response.raise_for_status()
        result = response.json()["data"]["result"]
    except requests.exceptions.ConnectionError as e:
        print(f"⚠️ Prometheus connection error for OpenShift query '{query}': {e}")
        return pd.DataFrame()  # Return empty DataFrame on connection error
    except requests.exceptions.Timeout as e:
        print(f"⚠️ Prometheus timeout for OpenShift query '{query}': {e}")
        return pd.DataFrame()  # Return empty DataFrame on timeout
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Prometheus request error for OpenShift query '{query}': {e}")
        return pd.DataFrame()  # Return empty DataFrame on other request errors

    rows = []
    for series in result:
        for val in series["values"]:
            ts = datetime.fromtimestamp(float(val[0]))
            value = float(val[1])

            # Handle NaN values that can't be JSON serialized
            if pd.isna(value) or value != value:  # Check for NaN
                value = 0.0  # Convert NaN to 0 for JSON compatibility

            row = dict(series["metric"])
            row["timestamp"] = ts
            row["value"] = value
            rows.append(row)

    return pd.DataFrame(rows) 
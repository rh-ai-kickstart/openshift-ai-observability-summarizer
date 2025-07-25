from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta, time
from scipy.stats import linregress
import os
import json
import re
from typing import List, Dict, Any, Optional
import uuid
import sys
from collections import defaultdict
from dateparser.search import search_dates

# Add current directory and parent directory to Python path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)  # For local development (metric_ui/)
sys.path.insert(0, current_dir)  # For container deployment (/app/)

from report_assets.report_renderer import (
    generate_html_report,
    generate_markdown_report,
    generate_pdf_report,
    ReportSchema,
    MetricCard,
)

# Import from core business logic
from core.metrics import get_models_helper
from core.llm_client import (
    summarize_with_llm,
    build_prompt,
    build_chat_prompt, 
    build_openshift_prompt,
    build_openshift_chat_prompt,
    build_flexible_llm_prompt,
    _make_api_request,
    _validate_and_extract_response,
    _clean_llm_summary_string,
    extract_time_range_with_info,
    extract_time_range,
    add_namespace_filter,
    fix_promql_syntax,
    format_alerts_for_ui
)


app = FastAPI()

# --- CONFIG ---
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
LLAMA_STACK_URL = os.getenv("LLAMA_STACK_URL", "http://localhost:8321/v1/openai/v1")
LLM_API_TOKEN = os.getenv("LLM_API_TOKEN", "")

# Load unified model configuration from environment
MODEL_CONFIG = {}
try:
    model_config_str = os.getenv("MODEL_CONFIG", "{}")
    MODEL_CONFIG = json.loads(model_config_str)
except Exception as e:
    print(f"Warning: Could not parse MODEL_CONFIG: {e}")
    MODEL_CONFIG = {}

# Handle token input from volume or literal
token_input = os.getenv(
    "THANOS_TOKEN", "/var/run/secrets/kubernetes.io/serviceaccount/token"
)
if os.path.exists(token_input):
    with open(token_input, "r") as f:
        THANOS_TOKEN = f.read().strip()
else:
    THANOS_TOKEN = token_input

# CA bundle location (mounted via ConfigMap)
CA_BUNDLE_PATH = "/etc/pki/ca-trust/extracted/pem/ca-bundle.crt"
verify = CA_BUNDLE_PATH if os.path.exists(CA_BUNDLE_PATH) else True

# --- Dynamic Metric Discovery Functions ---


def discover_vllm_metrics():
    """Dynamically discover available vLLM metrics from Prometheus, including GPU metrics"""
    try:
        headers = {"Authorization": f"Bearer {THANOS_TOKEN}"}
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/label/__name__/values",
            headers=headers,
            verify=verify,
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
            verify=verify,
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
            verify=verify,
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
        
        return cluster_metrics
    except Exception as e:
        print(f"Error discovering cluster metrics dynamically: {e}")
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


def fetch_openshift_metrics(query, start, end, namespace=None):
    """Fetch OpenShift metrics with optional namespace filtering"""
    headers = {"Authorization": f"Bearer {THANOS_TOKEN}"}

    # Add namespace filter to the query if specified
    if namespace:
        import re

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
            verify=verify,
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


def build_openshift_prompt(
    metric_dfs, metric_category, namespace=None, scope_description=None
):
    """Build prompt for OpenShift metrics analysis"""
    if scope_description:
        scope = scope_description
    else:
        scope = f"namespace **{namespace}**" if namespace else "cluster-wide"

    header = f"You are evaluating OpenShift **{metric_category}** metrics for {scope}.\n\n📊 **Metrics**:\n"
    analysis_focus = f"{metric_category.lower()} performance and health"

    lines = []
    for label, df in metric_dfs.items():
        if df.empty:
            lines.append(f"- {label}: No data")
            continue
        avg = df["value"].mean()
        latest = df["value"].iloc[-1] if not df.empty else 0
        trend = describe_trend(df)
        anomaly = detect_anomalies(df, label)
        lines.append(
            f"- {label}: Avg={avg:.2f}, Latest={latest:.2f}, Trend={trend}, {anomaly}"
        )

    analysis_questions = f"""🔍 Please analyze:
1. What's the current state of {analysis_focus}?
2. Are there any performance or reliability concerns?
3. What actions should be taken?
4. Any optimization recommendations?"""

    return f"""{header}
{chr(10).join(lines)}

{analysis_questions}
""".strip()


# --- Request Models ---
class AnalyzeRequest(BaseModel):
    model_name: str
    start_ts: int
    end_ts: int
    summarize_model_id: str
    api_key: Optional[str] = None


class ChatRequest(BaseModel):
    model_name: str
    prompt_summary: str
    question: str
    summarize_model_id: str
    api_key: Optional[str] = None


class ChatPrometheusRequest(BaseModel):
    model_name: str
    question: str
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    namespace: str
    summarize_model_id: str
    api_key: Optional[str] = None
    messages: Optional[List[Dict[str, str]]] = None


class ChatMetricsRequest(BaseModel):
    model_name: str
    question: str
    start_ts: Optional[int] = None
    end_ts: Optional[int] = None
    namespace: str
    summarize_model_id: str
    api_key: Optional[str] = None
    chat_scope: Optional[str] = "namespace_specific"  # "fleet_wide" or "namespace_specific"


class OpenShiftAnalyzeRequest(BaseModel):
    metric_category: str  # Specific category
    scope: str  # "cluster_wide" or "namespace_scoped"
    namespace: Optional[str] = None  # Required if scope is "namespace_scoped"
    start_ts: int
    end_ts: int
    summarize_model_id: str
    api_key: Optional[str] = None


class OpenShiftChatRequest(BaseModel):
    metric_category: str  # Specific category
    scope: str  # "cluster_wide" or "namespace_scoped"
    question: str
    namespace: Optional[str] = None  # Required if scope is "namespace_scoped"
    start_ts: int
    end_ts: int
    summarize_model_id: str
    api_key: Optional[str] = None


class ReportRequest(BaseModel):
    model_name: str
    start_ts: int
    end_ts: int
    summarize_model_id: str
    format: str
    api_key: Optional[str] = None
    health_prompt: Optional[str] = None
    llm_summary: Optional[str] = None
    metrics_data: Optional[Dict[str, Any]] = None
    trend_chart_image: Optional[str] = None


class MetricsCalculationRequest(BaseModel):
    metrics_data: Dict[str, List[Dict[str, Any]]]


class MetricsCalculationResponse(BaseModel):
    calculated_metrics: Dict[str, Dict[str, Optional[float]]]


# --- Helpers ---
def fetch_metrics(query, model_name, start, end, namespace=None):
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
            verify=verify,
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


def detect_anomalies(df, label):
    if df.empty:
        return "No data"
    mean = df["value"].mean()
    std = df["value"].std()
    p90 = df["value"].quantile(0.9)
    latest_val = df["value"].iloc[-1]
    if latest_val > p90:
        return f"⚠️ {label} spike (latest={latest_val:.2f}, >90th pct)"
    elif latest_val < (mean - std):
        return f"⚠️ {label} unusually low (latest={latest_val:.2f}, mean={mean:.2f})"
    return f"{label} stable (latest={latest_val:.2f}, mean={mean:.2f})"


def describe_trend(df):
    if df.empty or len(df) < 2:
        return "not enough data"
    df = df.sort_values("timestamp")
    x = (df["timestamp"] - df["timestamp"].min()).dt.total_seconds()
    y = df["value"]
    if x.nunique() <= 1:
        return "flat"
    slope, *_ = linregress(x, y)
    if slope > 0.01:
        return "increasing"
    elif slope < -0.01:
        return "decreasing"
    return "stable"


def compute_health_score(metric_dfs):
    score, reasons = 0, []
    if "P95 Latency (s)" in metric_dfs and not metric_dfs["P95 Latency (s)"].empty:
        mean = metric_dfs["P95 Latency (s)"]["value"].mean()
        if mean > 2:
            score -= 2
            reasons.append(f"High Latency (avg={mean:.2f}s)")
    if "GPU Usage (%)" in metric_dfs and not metric_dfs["GPU Usage (%)"].empty:
        mean = metric_dfs["GPU Usage (%)"]["value"].mean()
        if mean < 10:
            score -= 1
            reasons.append(f"Low GPU Utilization (avg={mean:.2f}%)")
    if "Requests Running" in metric_dfs and not metric_dfs["Requests Running"].empty:
        mean = metric_dfs["Requests Running"]["value"].mean()
        if mean > 10:
            score -= 1
            reasons.append(f"Too many requests (avg={mean:.2f})")
    return score, reasons


def build_prompt(metric_dfs, model_name):
    score, _ = compute_health_score(metric_dfs)
    
    # Check if this looks like a new deployment (all metrics empty)
    all_empty = all(df.empty for df in metric_dfs.values())
    
    if all_empty:
        # New deployment prompt
        if "|" in model_name:
            namespace = model_name.split("|")[0].strip()
            actual_model = model_name.split("|")[1].strip()
        else:
            namespace = "unknown"
            actual_model = model_name
            
        header = f"You are evaluating model **{actual_model}** in namespace **{namespace}**.\n\n🚀 **New Deployment Detected**: No metrics data available yet.\n\n📊 **Status**:\n"
        
        lines = [f"- {label}: Awaiting first metrics (new deployment)" for label in metric_dfs.keys()]
        
        return f"""{header}
{chr(10).join(lines)}

🚀 **New Deployment Analysis**:
1. **What's going well?**
   - Deployment appears successful (model is discoverable in monitoring)
   - No immediate errors detected during startup

2. **What's problematic?**
   - No data is available to indicate what's problematic with the model.

3. **Recommendations?**
   - **Monitor the model's performance and gather data on its strengths and weaknesses**: Since this is a new deployment, metrics will appear once the model starts processing requests.
   - **Adjust the model's configuration or training data to improve its performance**: Review the model's configuration to ensure it's optimized for the specific use case.
   - **Consider using a different model or architecture that better suits the task at hand**: If needed, evaluate other model options.
   - **Continuously evaluate and refine the model to ensure it meets the desired standards**: Set up regular monitoring and performance reviews.
   - **Provide more detailed metrics**: The model's performance metrics are currently incomplete. Providing more detailed metrics will help identify areas for improvement.

📋 **Next Steps**: Metrics typically appear within 5-10 minutes after the first inference request is processed. Check back shortly!
""".strip()
    else:
        # Standard prompt for models with data
        header = f"You are evaluating model **{model_name}**.\n\n🩺 Health Score: {score}\n\n📊 **Metrics**:\n"
        lines = []
        for label, df in metric_dfs.items():
            if df.empty:
                lines.append(f"- {label}: No data")
                continue
            trend = describe_trend(df)
            anomaly = detect_anomalies(df, label)
            avg = df["value"].mean()
            # Add an indication if data is present
            data_status = "Data present" if not df.empty else "No data"
            lines.append(
                f"- {label}: Avg={avg:.2f}, Trend={trend}, {anomaly} ({data_status})"
            )
        return f"""{header}
{chr(10).join(lines)}

🔍 Please analyze:
1. What's going well?
2. What's problematic?
3. Recommendations?
""".strip()


def build_chat_prompt(user_question: str, metrics_summary: str) -> str:
    return f"""
You are a senior MLOps engineer reviewing real-time Prometheus metrics and providing operational insights.

Your task is to answer **ANY type of observability question**, whether specific (e.g., "What is GPU usage?") or generic (e.g., "What's going wrong?", "Can I send more load?").

Use ONLY the information in the **metrics summary** to answer.

---
📊 Metrics Summary:
{metrics_summary.strip()}
---

🧠 Guidelines:
- Use your judgment as an MLOps expert.
- If the metrics look abnormal or risky, call it out.
- If something seems healthy, confirm it clearly.
- Do NOT restate the user's question.
- NEVER say "I'm an assistant" or explain how you're generating your response.
- Do NOT exceed 100 words.
- Use real metric names (e.g., "GPU Usage (%)", "P95 Latency (s)").
- Be direct, like a technical Slack message.
- If the user asks about scaling or sending more load, use "Requests Running", "Latency", or "GPU Usage" to justify your answer.

---
👤 User Prompt:
{user_question.strip()}
---

Now provide a concise, technical summary that answers it.
""".strip()








# helper functions for Chat with Prometheus








def fetch_alerts_from_prometheus(
    start_ts: int, end_ts: int, namespace: Optional[str] = None
):
    """
    Fetches active alerts for a time range and enriches them with their
    full rule definitions for maximum context.
    """

    headers = {"Authorization": f"Bearer {THANOS_TOKEN}"}
    promql_query = f'ALERTS{{namespace="{namespace}"}}' if namespace else "ALERTS"
    params = {
        "query": promql_query,
        "start": start_ts,
        "end": end_ts,
        "step": "30s",
    }
    try:
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query_range",
            headers=headers,
            params=params,
            verify=verify,
            timeout=30,  # Add timeout
        )
        response.raise_for_status()
        result = response.json()["data"]["result"]
    except requests.exceptions.ConnectionError as e:
        print(f"⚠️ Prometheus connection error for alerts query '{promql_query}': {e}")
        return promql_query, []  # Return empty alerts on connection error
    except requests.exceptions.Timeout as e:
        print(f"⚠️ Prometheus timeout for alerts query '{promql_query}': {e}")
        return promql_query, []  # Return empty alerts on timeout
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Prometheus request error for alerts query '{promql_query}': {e}")
        return promql_query, []  # Return empty alerts on other request errors

    alerts_data = []
    for series in result:
        alertname = series["metric"].get("alertname")
        severity = series["metric"].get("severity")
        alertstate = series["metric"].get("alertstate")  # "firing" or "inactive"
        for_duration = series["metric"].get("for")
        labels = series["metric"]
        for val in series["values"]:
            timestamp = datetime.fromtimestamp(float(val[0]))
            is_firing = int(float(val[1]))
            alerts_data.append(
                {
                    "alertname": alertname,
                    "severity": severity,
                    "alertstate": alertstate,
                    "timestamp": timestamp.isoformat(),
                    "is_firing": is_firing,
                    "for_duration": for_duration,
                    "labels": labels,
                }
            )
    return promql_query, alerts_data











# This is a helper function to get all rule definitions
def _fetch_all_rule_definitions() -> Dict[str, Dict[str, Any]]:
    """
    Fetches all rule definitions from the Prometheus API and returns them
    as a dictionary keyed by alert name.
    """
    definitions = {}
    try:
        response = requests.get(f"{PROMETHEUS_URL}/api/v1/rules", verify=verify)
        response.raise_for_status()
        groups = response.json()["data"]["groups"]
        for group in groups:
            for rule in group.get("rules", []):
                alert_name = rule.get("alert") or rule.get("name")
                if alert_name:
                    # Store the entire rule object for full context
                    definitions[alert_name] = {
                        "name": alert_name,
                        "expression": rule.get("expr", "N/A"),
                        "duration": rule.get("for", "0s"),
                        "labels": rule.get("labels", {}),
                    }
    except Exception as e:
        print(f"Error fetching rule definitions: {e}")
    return definitions


@app.get("/health")
def health():
    return {"status": "ok"}





@app.get("/models")
def list_models():
    return get_models_helper()


@app.get("/namespaces")
def list_namespaces():
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
        time_windows = [7 * 24 * 3600, 24 * 3600, 3600]  # 7 days  # 24 hours  # 1 hour

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
                        verify=verify,
                    )
                    response.raise_for_status()
                    series = response.json()["data"]

                    for entry in series:
                        namespace = entry.get("namespace", "").strip()
                        model = entry.get("model_name", "").strip()
                        if namespace and model:
                            namespace_set.add(namespace)

                    # If we found namespaces, return them
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


@app.get("/multi_models")
def list_multi_models():
    """Get available summarization models from configuration"""
    return list(MODEL_CONFIG.keys())


@app.get("/model_config")
def get_model_config():
    return MODEL_CONFIG


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    try:
        vllm_metrics = get_vllm_metrics()
        metric_dfs = {
            label: fetch_metrics(query, req.model_name, req.start_ts, req.end_ts)
            for label, query in vllm_metrics.items()
        }
        prompt = build_prompt(metric_dfs, req.model_name)

        summary = summarize_with_llm(prompt, req.summarize_model_id, req.api_key)

        # Ensure both columns exist, even if the DataFrame is empty
        serialized_metrics = {}
        for label, df in metric_dfs.items():
            for col in ["timestamp", "value"]:
                if col not in df.columns:
                    df[col] = pd.Series(
                        dtype="datetime64[ns]" if col == "timestamp" else "float"
                    )
            serialized_metrics[label] = df[["timestamp", "value"]].to_dict(
                orient="records"
            )

        return {
            "model_name": req.model_name,
            "health_prompt": prompt,
            "llm_summary": summary,
            "metrics": serialized_metrics,
        }
    except Exception as e:
        # Handle API key errors and other LLM-related errors
        raise HTTPException(
            status_code=500, detail="Please check your API Key or try again later."
        )


@app.post("/chat")
def chat(req: ChatRequest):
    try:
        prompt = build_chat_prompt(
            user_question=req.question, metrics_summary=req.prompt_summary
        )

        # Get LLM response using helper function
        response = summarize_with_llm(prompt, req.summarize_model_id, req.api_key)

        return {"response": _clean_llm_summary_string(response)}
    except Exception as e:
        # Handle API key errors and other LLM-related errors
        raise HTTPException(
            status_code=500, detail="Please check your API Key or try again later."
        )


@app.post("/chat-metrics")
def chat_metrics(req: ChatMetricsRequest):
    """
    Enhanced chat endpoint that supports ALL metrics (vLLM, OpenShift, GPU) with fleet-wide and namespace-specific scope
    """
    try:
        # 1. Extract time range with enhanced info for LLM context
        start_ts, end_ts, time_range_info = extract_time_range_with_info(req.question, req.start_ts, req.end_ts)
        
        # 2. Determine scope - fleet-wide or namespace-specific
        is_fleet_wide = req.chat_scope == "fleet_wide"
        target_namespace = None if is_fleet_wide else req.namespace
        
        # 3. Determine query type and fetch appropriate metrics
        question_lower = req.question.lower()
        is_all_models_query = (
            "all models currently deployed" in question_lower
            or "list all models" in question_lower
            or "what models are deployed" in question_lower.replace("?", "")
        )
        is_tokens_generated_query = "how many tokens generated" in question_lower

        metrics_data_summary = ""
        generated_tokens_sum_value = None

        # 4. Get ALL available metrics (vLLM, OpenShift, GPU)
        all_metrics = get_all_metrics()
        
        # 5. Fetch metric data for relevant metrics based on query type and scope
        if is_tokens_generated_query:
            # Focus on token generation metrics
            vllm_metrics = get_vllm_metrics()
            metric_dfs = {
                label: fetch_metrics(
                    query, req.model_name, start_ts, end_ts, namespace=target_namespace
                )
                for label, query in vllm_metrics.items()
                if "token" in label.lower()
            }
            
            output_tokens_df = metric_dfs.get("Output Tokens Created")
            if output_tokens_df is not None and not output_tokens_df.empty:
                generated_tokens_sum_value = output_tokens_df["value"].sum()
                scope_desc = "across all namespaces" if is_fleet_wide else f"in namespace {req.namespace}"
                metrics_data_summary = f"Output Tokens Created {scope_desc}: Total Generated = {generated_tokens_sum_value:.2f}"
            else:
                scope_desc = "across all namespaces" if is_fleet_wide else f"in namespace {req.namespace}"
                metrics_data_summary = f"Output Tokens Created {scope_desc}: No data available to calculate sum."

        elif is_all_models_query:
            # For "all models" query, fetch globally deployed models directly
            deployed_models_list = _get_models_helper()

            if is_fleet_wide:
                # Show all models across all namespaces
                metrics_data_summary = (
                    f"Models deployed fleet-wide: " + ", ".join(deployed_models_list)
                    if deployed_models_list
                    else "No models found fleet-wide"
                )
            else:
                # Filter models by namespace from the request
                deployed_models_list = [
                    model for model in deployed_models_list if f"| {req.namespace}" in model
                ]
                metrics_data_summary = (
                    f"Models in namespace {req.namespace}: " + ", ".join(deployed_models_list)
                    if deployed_models_list
                    else f"No models found in namespace {req.namespace}"
                )
        else:
            # For other metric-specific queries, intelligently select relevant metrics
            # Try to determine what type of metrics the user is asking about
            
            # PRIORITIZE fleet-wide vs namespace-specific detection first
            if is_fleet_wide or any(keyword in question_lower for keyword in ["cluster", "fleet", "entire", "total", "all namespaces", "cluster-wide"]):
                # FLEET-WIDE queries - use only OpenShift/Kubernetes metrics, never vLLM
                print(f"🔍 Detected FLEET-WIDE query: {req.question}")
                openshift_metrics = get_openshift_metrics()
                
                # Smart metric selection based on query content
                relevant_metrics = {}
                
                # Services & Networking
                if any(keyword in question_lower for keyword in ["service", "services", "endpoint", "endpoints", "ingress", "network", "loadbalancer", "clusterip"]):
                    relevant_metrics.update(openshift_metrics.get("Services & Networking", {}))
                
                # Jobs & Workloads  
                if any(keyword in question_lower for keyword in ["job", "jobs", "cronjob", "cronjobs", "daemonset", "daemonsets", "statefulset", "statefulsets"]):
                    relevant_metrics.update(openshift_metrics.get("Jobs & Workloads", {}))
                
                # Storage & Config
                if any(keyword in question_lower for keyword in ["volume", "volumes", "pv", "pvc", "storage", "configmap", "configmaps", "secret", "secrets", "storageclass"]):
                    relevant_metrics.update(openshift_metrics.get("Storage & Config", {}))
                
                # Pods & Containers
                if any(keyword in question_lower for keyword in ["pod", "pods", "container", "containers"]):
                    relevant_metrics.update(openshift_metrics.get("Workloads & Pods", {}))
                    relevant_metrics.update(openshift_metrics.get("Fleet Overview", {}))
                
                # CPU, Memory, Nodes
                if any(keyword in question_lower for keyword in ["cpu", "memory", "node", "nodes", "compute"]):
                    relevant_metrics.update(openshift_metrics.get("Fleet Overview", {}))
                
                # Deployments
                if any(keyword in question_lower for keyword in ["deployment", "deployments", "replica", "replicas"]):
                    relevant_metrics.update({
                        "Total Deployments": "sum(kube_deployment_status_replicas_ready)",
                        "Deployment Replicas Available": "sum(kube_deployment_status_replicas_available)", 
                        "Deployment Replicas Desired": "sum(kube_deployment_spec_replicas)",
                        "Total Pods Running": "sum(kube_pod_status_phase{phase='Running'})",
                    })
                
                # Images & Containers
                if any(keyword in question_lower for keyword in ["image", "images", "container images"]):
                    relevant_metrics.update({
                        "Container Images": "count(count by (image)(container_spec_image))",
                        "Running Containers": "sum(kube_pod_container_status_running)",
                        "Total Pods Running": "sum(kube_pod_status_phase{phase='Running'})",
                    })
                
                # If no specific category detected, try dynamic discovery or use Fleet Overview
                if not relevant_metrics:
                    print("🔍 No static metrics matched, trying dynamic discovery...")
                    dynamic_metrics = discover_cluster_metrics_dynamically()
                    
                    # Try to find relevant metrics from dynamic discovery based on keywords
                    for label, query in dynamic_metrics.items():
                        label_lower = label.lower()
                        if any(keyword in label_lower for keyword in question_lower.split()):
                            relevant_metrics[label] = query
                            if len(relevant_metrics) >= 6:  # Limit to avoid overwhelming
                                break
                    
                    # If still no metrics found, use Fleet Overview as final fallback
                    if not relevant_metrics:
                        relevant_metrics = openshift_metrics.get("Fleet Overview", {})
                
                metric_dfs = {
                    label: fetch_openshift_metrics(
                        query, start_ts, end_ts, namespace=None  # No namespace filter for fleet-wide
                    )
                    for label, query in list(relevant_metrics.items())[:8]
                }
                
                # Build fleet-wide summary
                metrics_data_summary = f"Fleet-wide OpenShift cluster analysis:\n"
                for label, df in metric_dfs.items():
                    if df.empty:
                        metrics_data_summary += f"- {label}: No data available\n"
                    else:
                        latest = df["value"].iloc[-1] if not df.empty else 0
                        avg = df["value"].mean()
                        metrics_data_summary += f"- {label}: Current={latest:.2f}, Average={avg:.2f}\n"
                        
            elif any(keyword in question_lower for keyword in [
                # Core Kubernetes resources
                "pod", "pods", "container", "containers", "kubernetes", "openshift", "k8s",
                "node", "nodes", "deployment", "deployments", "service", "services", 
                "pv", "pvc", "ingress", "endpoint", "endpoints",
                # Workload types
                "job", "jobs", "cronjob", "cronjobs", "daemonset", "daemonsets", 
                "statefulset", "statefulsets", "replicaset", "replicasets",
                # Storage & Config
                "volume", "volumes", "storage", "configmap", "configmaps", 
                "secret", "secrets", "storageclass", "snapshot",
                # Networking
                "network", "networking", "loadbalancer", "clusterip", "nodeport",
                # Additional cluster concepts
                "namespace", "namespaces", "cluster", "kube", "replica", "replicas"
            ]):
                # Namespace-specific OpenShift/Kubernetes query
                print(f"🔍 Detected namespace-specific OpenShift/Kubernetes query: {req.question}")
                openshift_metrics = get_openshift_metrics()
                
                # For pod queries, focus on Workloads & Pods
                if "pod" in question_lower:
                    relevant_metrics = openshift_metrics.get("Workloads & Pods", {})
                else:
                    # Use all openshift metrics, flattened
                    relevant_metrics = {}
                    for category, metrics in openshift_metrics.items():
                        relevant_metrics.update(metrics)
                
                metric_dfs = {
                    label: fetch_openshift_metrics(
                        query, start_ts, end_ts, namespace=target_namespace
                    )
                    for label, query in list(relevant_metrics.items())[:8]
                }
                
                # Build namespace-specific summary
                metrics_data_summary = f"OpenShift metrics analysis for namespace {target_namespace}:\n"
                for label, df in metric_dfs.items():
                    if df.empty:
                        metrics_data_summary += f"- {label}: No data\n"
                    else:
                        avg = df["value"].mean()
                        latest = df["value"].iloc[-1] if not df.empty else 0
                        metrics_data_summary += f"- {label}: Latest={latest:.2f}, Avg={avg:.2f}\n"
                        
            elif any(keyword in question_lower for keyword in ["gpu", "temperature", "power", "memory utilization", "energy", "dcgm"]):
                # GPU-related query - fetch GPU metrics
                print(f"🔍 Detected GPU query: {req.question}")
                gpu_metrics = {k: v for k, v in all_metrics.items() if "GPU:" in k}
                metric_dfs = {
                    label.replace("GPU: ", ""): fetch_metrics(
                        query, req.model_name, start_ts, end_ts, namespace=target_namespace
                    )
                    for label, query in gpu_metrics.items()
                }
                # Build metrics summary using existing function
                metrics_data_summary = build_prompt(metric_dfs, req.model_name)
                
            else:
                # vLLM or general query - fetch vLLM metrics
                print(f"🔍 Detected vLLM/general query: {req.question}")
                vllm_metrics = get_vllm_metrics()
                metric_dfs = {
                    label: fetch_metrics(
                        query, req.model_name, start_ts, end_ts, namespace=target_namespace
                    )
                    for label, query in vllm_metrics.items()
                }
                # Build metrics summary using existing function
                metrics_data_summary = build_prompt(metric_dfs, req.model_name)

        # 6. Build the enhanced prompt with time range and scope context
        # Determine if this is an OpenShift query for specialized prompt
        is_openshift_query = (
            is_fleet_wide or 
            any(keyword in question_lower for keyword in ["cluster", "fleet", "entire", "total", "all namespaces", "cluster-wide"]) or
            any(keyword in question_lower for keyword in [
                # Core Kubernetes resources
                "pod", "pods", "container", "containers", "kubernetes", "openshift", "k8s",
                "node", "nodes", "deployment", "deployments", "service", "services", 
                "pv", "pvc", "ingress", "endpoint", "endpoints",
                # Workload types
                "job", "jobs", "cronjob", "cronjobs", "daemonset", "daemonsets", 
                "statefulset", "statefulsets", "replicaset", "replicasets",
                # Storage & Config
                "volume", "volumes", "storage", "configmap", "configmaps", 
                "secret", "secrets", "storageclass", "snapshot",
                # Networking
                "network", "networking", "loadbalancer", "clusterip", "nodeport",
                # System resources
                "cpu usage", "memory usage", "cpu", "memory", "compute",
                # Images and additional concepts
                "image", "images", "replica", "replicas", "namespace", "namespaces"
            ])
        )
        
        if is_openshift_query:
            prompt = build_openshift_chat_prompt(
                question=req.question,
                metrics_context=metrics_data_summary,
                time_range_info=time_range_info,
                chat_scope=req.chat_scope,
                target_namespace=target_namespace,
            )
        else:
            prompt = build_flexible_llm_prompt(
                question=req.question,
                model_name=req.model_name,
                metrics_context=metrics_data_summary,
                generated_tokens_sum=generated_tokens_sum_value,
                selected_namespace=target_namespace,
                time_range_info=time_range_info,
                chat_scope=req.chat_scope,
            )
        
        # 6. Get LLM response
        llm_response = summarize_with_llm(prompt, req.summarize_model_id, req.api_key)

        # Debug LLM response
        print("🧠 Raw LLM response:", llm_response)

        # 7. Enhanced JSON parsing (from the better chat-metrics implementation you showed)
        try:
            # Step 1: Clean the response
            cleaned_response = llm_response.strip()
            print("⚙️ After initial strip:", cleaned_response)

            # Remove any markdown code block markers
            cleaned_response = re.sub(r"```json\s*|\s*```", "", cleaned_response)
            print("⚙️ After markdown removal:", cleaned_response)

            # Remove any leading/trailing whitespace and newlines
            cleaned_response = cleaned_response.strip()
            print("⚙️ After final strip:", cleaned_response)

            # Find the first complete JSON object (more precise matching)
            json_start = cleaned_response.find('{')
            if json_start == -1:
                raise ValueError(f"No JSON object found in response: '{cleaned_response}'")
            
            # Find the matching closing brace for the first JSON object
            brace_count = 0
            json_end = json_start
            in_string = False
            escape_next = False
            
            for i, char in enumerate(cleaned_response[json_start:], json_start):
                if escape_next:
                    escape_next = False
                    continue
                if char == '\\':
                    escape_next = True
                    continue
                if char == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            json_end = i + 1
                            break
            
            if brace_count != 0:
                raise ValueError(f"Malformed JSON object in response: '{cleaned_response}'")
            
            json_string = cleaned_response[json_start:json_end]
            print("⚙️ Extracted JSON string:", json_string)

            # Clean the JSON string - be more careful with escaped quotes
            # First, fix escaped quotes in PromQL queries - specifically in namespace values
            json_string = re.sub(r'namespace=\\"([^"]*)\\"', r'namespace="\1"', json_string)
            print("⚙️ After fixing namespace escaped quotes:", json_string)
            
            # Fix any remaining escaped quotes
            json_string = re.sub(r'\\\"', '"', json_string)
            print("⚙️ After fixing escaped quotes:", json_string)
            
            # Remove any excessive whitespace but preserve structure
            json_string = re.sub(r'\n\s*', ' ', json_string)
            json_string = re.sub(r'\s{2,}', ' ', json_string)
            print("⚙️ After whitespace normalization:", json_string)

            # Remove trailing commas
            json_string = re.sub(r",\s*}", "}", json_string)
            json_string = re.sub(r",\s*]", "]", json_string)
            print("⚙️ After trailing comma removal:", json_string)

            print("🔍 Final Cleaned JSON string for parsing:", json_string)

            # Parse the JSON - try multiple approaches for robustness
            try:
                parsed = json.loads(json_string)
            except json.JSONDecodeError:
                # If that fails, try with ast.literal_eval for safer parsing
                import ast
                try:
                    # Replace any remaining problematic patterns
                    fixed_json = json_string.replace('\\"', '"')
                    parsed = json.loads(fixed_json)
                except json.JSONDecodeError:
                    # Last resort: manually extract the important parts
                    promqls_match = re.search(r'"promqls":\s*\[(.*?)\]', json_string, re.DOTALL)
                    summary_match = re.search(r'"summary":\s*"(.*?)"(?=\s*[,}])', json_string, re.DOTALL)
                    
                    parsed = {
                        "promqls": [promqls_match.group(1).strip('"')] if promqls_match else [],
                        "summary": summary_match.group(1) if summary_match else ""
                    }

            # Extract and clean the fields
            promqls = parsed.get("promqls", [])
            promql = promqls[0] if promqls else ""
            if isinstance(promql, list):
                promql = promql[0] if promql else ""
            promql = promql.strip() if promql else ""
            summary = _clean_llm_summary_string(parsed.get("summary", ""))

            # Fix PromQL syntax issues and ensure proper time range syntax
            if promql:
                promql = fix_promql_syntax(promql, time_range_info.get("rate_syntax", "5m"))
                
                # Handle namespace filtering based on chat scope
                if is_fleet_wide:
                    # Fleet-wide: Remove any namespace filters to query across all namespaces
                    promql = re.sub(r"\{([^}]*)namespace=[^,}]*(,)?", r"{\1", promql)
                    promql = re.sub(r"namespace=[^,}]*(,)?", "", promql)
                    # Clean up any resulting empty braces or trailing commas
                    promql = re.sub(r"\{\s*,", "{", promql)
                    promql = re.sub(r",\s*\}", "}", promql)
                    promql = re.sub(r"\{\s*\}", "", promql)
                elif req.namespace:
                    # Namespace-specific: Ensure the correct namespace is in PromQL
                    # Remove existing namespace labels from PromQL if present
                    promql = re.sub(r"\{([^}]*)namespace=[^,}]*(,)?", r"{\1", promql)
                    # Add the correct namespace
                    if "{" in promql:
                        promql = promql.replace("{", f'{{namespace="{req.namespace}", ')
                    else:
                        # If no existing curly braces, add them with the namespace
                        promql = f"{promql}{{namespace='{req.namespace}'}}"
                
                # Final cleanup of any syntax issues
                promql = fix_promql_syntax(promql, time_range_info.get("rate_syntax", "5m"))

            if not summary:
                raise ValueError("Empty summary in response")

            return {
                "promql": promql, 
                "summary": summary,
                "time_range": time_range_info.get("duration_str", ""),
                "metrics_type": "all",
                "chat_scope": req.chat_scope
            }

        except json.JSONDecodeError as e:
            print(f"⚠️ JSON Decode Error: {e}")
            return {
                "promql": "",
                "summary": f"Failed to parse response: {e}. Raw response: {llm_response}",
                "time_range": time_range_info.get("duration_str", ""),
                "chat_scope": req.chat_scope
            }
        except ValueError as e:
            print(f"⚠️ Value Error: {e}")
            return {
                "promql": "",
                "summary": f"Failed to process response: {e}. Raw response: {llm_response}",
                "time_range": time_range_info.get("duration_str", ""),
                "chat_scope": req.chat_scope
            }
        except Exception as e:
            print(f"⚠️ Unexpected error: {e}")
            return {
                "promql": "",
                "summary": f"An unexpected error occurred: {e}. Raw response: {llm_response}",
                "time_range": time_range_info.get("duration_str", ""),
                "chat_scope": req.chat_scope
            }

    except Exception as e:
        print(f"Error in chat_metrics: {e}")
        raise HTTPException(
            status_code=500, detail="Please check your API Key or try again later."
        )


def build_openshift_chat_prompt(
    question: str,
    metrics_context: str,
    time_range_info: Optional[Dict[str, Any]] = None,
    chat_scope: str = "namespace_specific",
    target_namespace: str = None,
) -> str:
    """
    Build specialized prompt for OpenShift/Kubernetes queries
    """
    # Build scope context
    if chat_scope == "fleet_wide":
        scope_context = "You are analyzing **fleet-wide OpenShift/Kubernetes metrics across ALL namespaces**.\n\n"
    elif target_namespace:
        scope_context = f"You are analyzing OpenShift/Kubernetes metrics for namespace: **{target_namespace}**.\n\n"
    else:
        scope_context = "You are analyzing OpenShift/Kubernetes metrics.\n\n"
    
    # Build time range context
    time_context = ""
    time_range_syntax = "5m"  # default
    if time_range_info:
        time_duration = time_range_info.get("duration_str", "")
        time_range_syntax = time_range_info.get("rate_syntax", "5m")
        time_context = f"""**🕐 TIME RANGE CONTEXT:**
The user asked about: **{time_duration}**
Use time range syntax `[{time_range_syntax}]` in PromQL queries where appropriate.

"""

    # Common OpenShift metrics for reference
    common_metrics = """**📊 Comprehensive OpenShift/Kubernetes Metrics:**
- Pods: `sum(kube_pod_status_phase{phase="Running"})`, `sum(kube_pod_status_phase{phase="Failed"})`
- Deployments: `sum(kube_deployment_status_replicas_ready)`, `sum(kube_deployment_spec_replicas)`
- Services: `sum(kube_service_info)`, `sum(kube_endpoint_address_available)`
- Jobs: `sum(kube_job_status_active)`, `sum(kube_job_status_succeeded)`, `sum(kube_job_status_failed)`
- Storage: `sum(kube_persistentvolume_info)`, `sum(kube_persistentvolumeclaim_info)`
- Config: `sum(kube_configmap_info)`, `sum(kube_secret_info)`
- Nodes: `sum(kube_node_info)`, `sum(kube_node_status_condition{condition="Ready"})`
- CPU: `100 - (avg(rate(node_cpu_seconds_total{mode='idle'}[5m])) * 100)`
- Memory: `100 - (sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes) * 100)`
- Containers: `count(count by (image)(container_spec_image))`, `sum(kube_pod_container_status_running)`
- Workloads: `sum(kube_daemonset_status_number_ready)`, `sum(kube_statefulset_status_replicas_ready)`

"""

    return f"""
You are a Senior Site Reliability Engineer (SRE) expert in OpenShift/Kubernetes observability. Your task is to analyze the provided metrics and answer the user's question with precise, actionable insights.

{scope_context}{time_context}{common_metrics}

**Current Metrics Status:**
{metrics_context.strip()}

**Your Task:**
Provide a concise, technical response that directly answers the user's question based on the metrics data provided.

**JSON Output Schema:**
- "promqls" (list of strings, optional): 1-2 relevant PromQL queries that support your answer. Use correct OpenShift/Kubernetes metric names and proper time ranges [{time_range_syntax}].
- "summary" (string, required): A direct, technical answer (2-3 sentences) explaining the current state and any recommendations.

**Rules:**
- Use double quotes for all JSON keys and string values
- No trailing commas
- Base your answer ONLY on the provided metrics data
- If no data is available, state that clearly
- For pod counts, use `kube_pod_status_phase` metrics
- For cluster-wide queries, avoid namespace filters
- Be specific with numbers when available

**User Question:** {question}
**Response:**""".strip()


def build_flexible_llm_prompt(
    question: str,
    model_name: str,
    metrics_context: str,
    generated_tokens_sum: Optional[float] = None,
    selected_namespace: str = None,
    alerts_context: str = "",
    time_range_info: Optional[Dict[str, Any]] = None,
    chat_scope: str = "namespace_specific",
) -> str:

    # Safely handle generated_tokens_sum formatting
    summary_tokens_generated = ""
    if generated_tokens_sum is not None:
        try:
            # Convert to float if it's a string
            if isinstance(generated_tokens_sum, str):
                tokens_value = float(generated_tokens_sum)
            else:
                tokens_value = float(generated_tokens_sum)
            summary_tokens_generated = f"A total of {tokens_value:.2f} tokens were generated across all models and namespaces."
        except (ValueError, TypeError):
            summary_tokens_generated = f"Token generation data: {generated_tokens_sum}"

    # Build scope context
    if chat_scope == "fleet_wide":
        namespace_context = f"You are analyzing **fleet-wide metrics across ALL namespaces** for model **{model_name}**.\n\n"
    elif selected_namespace:
        namespace_context = f"You are currently focused on the namespace: **{selected_namespace}** and model **{model_name}**.\n\n"
    else:
        namespace_context = ""
    
    # Build time range context for the LLM
    time_context = ""
    time_range_syntax = "5m"  # default
    if time_range_info:
        time_duration = time_range_info.get("duration_str", "")
        time_range_syntax = time_range_info.get("rate_syntax", "5m")
        time_context = f"""**🕐 CRITICAL TIME RANGE REQUIREMENTS:**
The user asked about: **{time_duration}**

**MANDATORY PromQL Syntax Rules:**
✅ ALWAYS add time range `[{time_range_syntax}]` to metrics that need it
✅ For P95/P99 latency: `histogram_quantile(0.95, sum(rate(vllm:e2e_request_latency_seconds_bucket[{time_range_syntax}])) by (le))`  
✅ For rates: `rate(vllm:request_prompt_tokens_created[{time_range_syntax}])`
✅ For averages over time: `avg_over_time(vllm:num_requests_running[{time_range_syntax}])`
❌ NEVER use: `vllm:metric_name{{namespace="...", }}` (trailing comma)
❌ NEVER use: `vllm:metric_name{{namespace="..."}}` (missing time range)

"""

    vllm_metrics = get_vllm_metrics()
    metrics_list = "\n".join(
        [
            f'- Label: "{label}"\n  PromQL: `{add_namespace_filter(query, selected_namespace)}`'
            for label, query in vllm_metrics.items()
        ]
    )

    # The task is to analyze and connect the dots.
    return f"""
You are a world-class Senior Production Engineer, an expert in observability and root cause analysis. Your primary skill is correlating different types of telemetry data (metrics, alerts, logs, traces) to form a complete picture of system health and answer user questions with deep, actionable insights.

{namespace_context}{time_context}**Complete Observability Context:**
# Available Metrics:
# {metrics_list}

# Current Metric Status:
{metrics_context.strip()}

# Current Alert Status:
# {alerts_context.strip()}

{summary_tokens_generated.strip()}


**Your Task:**
Analyze the complete operational context provided above to give a concise, insightful, and actionable answer to the user's question.
- **Correlate data:** If a metric is abnormal, check if any alerts or other data could explain why.
- **Handling Insufficient Data:** If the context does not contain the information needed to answer the user's question, you MUST state that clearly and directly in the summary. Do not try to guess or hallucinate an answer.
- **Respond in JSON:** Your entire response must be a single, complete JSON object.

**JSON Output Schema:**
- "promqls" (list of strings, optional): A list of 1-2 (not more) relevant PromQL query strings that support your summary. You MUST use valid PromQL queries with proper time ranges [{time_range_syntax}]. Do not use the friendly name. Include alert query or metric query or both based on the context.
- "summary" (string, required): A thoughtful paragraph (2-4 sentences) that directly answers the user's question. Explain the meaning of the metric value or alert, what it implies for the system, and recommend an action if needed. Connect different pieces of context where possible. Sound like a senior engineer.


# Rules for JSON output:
# - Use double quotes for all keys and string values.
# - No trailing commas.
# - No line breaks within string values.
# - No comments.
# - Use only the context provided.
# - If appropriate, briefly recommend one action or area to investigate.
# - The summary field in the JSON should contain a single plain-text paragraph
# - Do NOT restate the question.
# - Do NOT copy the example, only learn how to answer the question.
# - If the context indicates there are no alerts, the 'summary' MUST explicitly state that no alerts were found.
# - MANDATORY: All PromQL queries MUST include proper time range syntax like [{time_range_syntax}]
---
Example for P95 latency question:
{{
  "promqls": ["histogram_quantile(0.95, sum(rate(vllm:e2e_request_latency_seconds_bucket[{time_range_syntax}])) by (le))"],
  "summary": "Based on the current metrics, the P95 latency is 0.00 seconds, indicating that the system is handling requests efficiently and without significant delays. However, I recommend monitoring this metric closely to ensure that any future changes in latency do not impact system performance."
}}

Example for token generation question:
{{
  "promqls": ["sum(rate(vllm:request_prompt_tokens_created[{time_range_syntax}]))"],
  "summary": "The token generation rate shows the system is processing requests at a steady pace. Based on the current data, the system appears to be operating within normal parameters for token generation workloads."
}}
---

Now respond to the user's actual question.

**User Question:** {question}
**Response:**""".strip()


@app.post("/chat-openshift")
def chat_openshift(req: OpenShiftChatRequest):
    """Chat about OpenShift metrics for a specific category"""
    try:
        openshift_metrics = get_openshift_metrics()

        # Validate metric category based on scope
        if req.scope == "namespace_scoped" and req.namespace:
            # For namespace-scoped analysis, check against namespace-specific metrics first
            namespace_metrics = get_namespace_specific_metrics(req.metric_category)
            if not namespace_metrics:
                # Fall back to cluster-wide metrics if no namespace-specific metrics available
                if req.metric_category not in openshift_metrics:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid metric category: {req.metric_category}",
                    )
                metrics_to_fetch = openshift_metrics[req.metric_category]
            else:
                metrics_to_fetch = namespace_metrics
        else:
            # For cluster-wide analysis, check against cluster-wide metrics
            if req.metric_category not in openshift_metrics:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid metric category: {req.metric_category}",
                )
            metrics_to_fetch = openshift_metrics[req.metric_category]

        # Determine namespace for fetching based on scope
        namespace_for_query = req.namespace if req.scope == "namespace_scoped" else None

        metric_dfs = {}
        # Fetch metrics for the specified category
        for label, query in metrics_to_fetch.items():
            try:
                df = fetch_openshift_metrics(
                    query, req.start_ts, req.end_ts, namespace_for_query
                )
                metric_dfs[label] = df
            except Exception as e:
                print(f"Error fetching {label}: {e}")
                metric_dfs[label] = pd.DataFrame()

        # Build scope description
        scope_description = f"{req.scope.replace('_', ' ').title()}"
        if req.scope == "namespace_scoped" and req.namespace:
            scope_description += f" ({req.namespace})"

        # Build metrics summary for the LLM
        metrics_data_summary = build_openshift_prompt(
            metric_dfs, req.metric_category, namespace_for_query, scope_description
        )

        # Create a simple prompt for OpenShift chat
        context_description = (
            f"OpenShift {req.metric_category} metrics for **{scope_description}**"
        )

        prompt = f"""You are a senior Site Reliability Engineer (SRE) analyzing {context_description}.

Current Metrics:
{metrics_data_summary}

User Question: {req.question}

Provide a concise technical response focusing on operational insights and recommendations. Respond with JSON format:
{{"promql": "relevant_query_if_applicable", "summary": "your_analysis"}}"""

        llm_response = summarize_with_llm(prompt, req.summarize_model_id, req.api_key)

        # Simple JSON parsing
        try:
            # Try to extract JSON from response
            json_match = re.search(r"\{.*\}", llm_response, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(0))
                promql = parsed.get("promql", "").strip()
                summary = parsed.get("summary", llm_response).strip()

                # Add namespace filtering to PromQL if specified and not already present
                if promql and req.namespace and "namespace=" not in promql:
                    if "{" in promql:
                        promql = promql.replace("{", f'{{namespace="{req.namespace}", ')
                    else:
                        promql = f'{promql}{{namespace="{req.namespace}"}}'

                return {"promql": promql, "summary": summary}
        except json.JSONDecodeError:
            pass

        # Fallback
        return {"promql": "", "summary": llm_response}

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in chat_openshift: {e}")
        raise HTTPException(
            status_code=500, detail="Please check your API Key or try again later."
        )


@app.post("/analyze-openshift")
def analyze_openshift(req: OpenShiftAnalyzeRequest):
    """Analyze OpenShift metrics for a specific category and scope"""
    try:
        openshift_metrics = get_openshift_metrics()
        
        # Validate metric category based on scope
        if req.scope == "namespace_scoped" and req.namespace:
            # For namespace-scoped analysis, check against namespace-specific metrics first
            namespace_metrics = get_namespace_specific_metrics(req.metric_category)
            if not namespace_metrics:
                # Fall back to cluster-wide metrics if no namespace-specific metrics available
                if req.metric_category not in openshift_metrics:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid metric category: {req.metric_category}",
                    )
                metrics_to_fetch = openshift_metrics[req.metric_category]
            else:
                metrics_to_fetch = namespace_metrics
        else:
            # For cluster-wide analysis, check against cluster-wide metrics
            if req.metric_category not in openshift_metrics:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid metric category: {req.metric_category}",
                )
            metrics_to_fetch = openshift_metrics[req.metric_category]

        # Determine namespace for fetching based on scope
        namespace_for_query = req.namespace if req.scope == "namespace_scoped" else None

        metric_dfs = {}
        # Fetch metrics for the specified category
        for label, query in metrics_to_fetch.items():
            try:
                df = fetch_openshift_metrics(
                    query, req.start_ts, req.end_ts, namespace_for_query
                )
                metric_dfs[label] = df
            except Exception as e:
                print(f"Error fetching {label}: {e}")
                metric_dfs[label] = pd.DataFrame()

        # Build scope description for prompt
        scope_description = f"{req.scope.replace('_', ' ').title()}"
        if req.scope == "namespace_scoped" and req.namespace:
            scope_description += f" ({req.namespace})"

        # Build metrics summary for the LLM using OpenShift-specific prompt
        prompt = build_openshift_prompt(
            metric_dfs, req.metric_category, namespace_for_query, scope_description
        )

        # Get LLM summary
        summary = summarize_with_llm(prompt, req.summarize_model_id, req.api_key)

        # Serialize metrics data
        serialized_metrics = {}
        for label, df in metric_dfs.items():
            for col in ["timestamp", "value"]:
                if col not in df.columns:
                    df[col] = pd.Series(
                        dtype="datetime64[ns]" if col == "timestamp" else "float"
                    )
            serialized_metrics[label] = df[["timestamp", "value"]].to_dict(
                orient="records"
            )

        return {
            "metric_category": req.metric_category,
            "scope": req.scope,
            "namespace": req.namespace,
            "health_prompt": prompt,
            "llm_summary": summary,
            "metrics": serialized_metrics,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in analyze_openshift: {e}")
        raise HTTPException(
            status_code=500, detail="Please check your API Key or try again later."
        )


@app.post("/calculate-metrics")
def calculate_metrics(req: MetricsCalculationRequest):
    """Calculate statistics for provided metrics data"""
    try:
        calculated_metrics = {}
        
        for label, data_points in req.metrics_data.items():
            if not data_points:
                calculated_metrics[label] = {
                    "avg": None,
                    "min": None,
                    "max": None,
                    "latest": None,
                    "count": 0
                }
                continue
                
            # Extract values from data points
            values = []
            for point in data_points:
                if isinstance(point, dict) and "value" in point:
                    try:
                        value = float(point["value"])
                        if not (pd.isna(value) or value != value):  # Check for NaN
                            values.append(value)
                    except (ValueError, TypeError):
                        continue
                        
            if values:
                calculated_metrics[label] = {
                    "avg": sum(values) / len(values),
                    "min": min(values),
                    "max": max(values), 
                    "latest": values[-1],
                    "count": len(values)
                }
            else:
                calculated_metrics[label] = {
                    "avg": None,
                    "min": None,
                    "max": None,
                    "latest": None,
                    "count": 0
                }
        
        return {"calculated_metrics": calculated_metrics}
        
    except Exception as e:
        print(f"Error calculating metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Error calculating metrics: {e}")


@app.get("/openshift-metric-groups") 
def get_openshift_metric_groups():
    """Get available OpenShift metric group categories"""
    openshift_metrics = get_openshift_metrics()
    return list(openshift_metrics.keys())


@app.get("/openshift-namespace-metric-groups")
def get_openshift_namespace_metric_groups():
    """Get OpenShift metric groups that work with namespace filtering"""
    return [
        "Workloads & Pods",
        "Storage & Networking", 
        "Application Services"
    ]


@app.get("/vllm-metrics")
def get_vllm_metrics_endpoint():
    """Get available vLLM metrics endpoint for UI"""
    return get_vllm_metrics()


@app.get("/gpu-info") 
def get_gpu_info():
    """Get GPU information"""
    try:
        # Get GPU metrics to determine GPU info
        dcgm_metrics = discover_dcgm_metrics()
        
        gpu_info = {
            "total_gpus": 0,
            "vendors": [],
            "models": [], 
            "temperatures": [],
            "power_usage": []
        }
        
        # Try to get some basic GPU info from metrics
        if dcgm_metrics:
            # Estimate number of GPUs (this is a rough estimate)
            if "GPU Temperature (°C)" in dcgm_metrics:
                try:
                    headers = {"Authorization": f"Bearer {THANOS_TOKEN}"}
                    response = requests.get(
                        f"{PROMETHEUS_URL}/api/v1/query",
                        headers=headers,
                        params={"query": "DCGM_FI_DEV_GPU_TEMP"},
                        verify=verify,
                        timeout=30,
                    )
                    response.raise_for_status()
                    result = response.json()["data"]["result"]
                    
                    gpu_info["total_gpus"] = len(result)
                    temperatures = [float(series["value"][1]) for series in result if series["value"]]
                    gpu_info["temperatures"] = temperatures
                    
                    if temperatures:
                        gpu_info["vendors"] = ["NVIDIA"]  # DCGM typically means NVIDIA
                        gpu_info["models"] = ["GPU"]  # Generic for now
                        
                except Exception as e:
                    print(f"Error getting GPU details: {e}")
        
        return gpu_info
        
    except Exception as e:
        print(f"Error getting GPU info: {e}")
        return {
            "total_gpus": 0,
            "vendors": [],
            "models": [],
            "temperatures": [],
            "power_usage": []
        }


@app.get("/openshift-namespaces")
def get_openshift_namespaces():
    """Get list of namespaces with OpenShift resources"""
    try:
        headers = {"Authorization": f"Bearer {THANOS_TOKEN}"}
        
        # Query for namespaces from kube metrics
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            headers=headers,
            params={"query": "group by (namespace) (kube_pod_info)"},
            verify=verify,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()["data"]["result"]
        
        namespaces = set()
        for series in result:
            namespace = series["metric"].get("namespace", "").strip()
            if namespace and namespace not in ["", "kube-system", "openshift-system"]:
                namespaces.add(namespace)
        
        # Add fallback namespaces if none found
        if not namespaces:
            namespaces.update(["default", "openshift-monitoring", "knative-serving"])
            
        return sorted(list(namespaces))
        
    except Exception as e:
        print(f"Error getting OpenShift namespaces: {e}")
        return ["default", "openshift-monitoring", "knative-serving"]


@app.get("/debug-metrics")
def debug_metrics():
    """Debug endpoint to diagnose metric collection issues"""
    debug_info = {
        "prometheus_url": PROMETHEUS_URL,
        "connection_status": "unknown",
        "available_metrics": {},
        "gpu_metrics_found": [],
        "dcgm_metrics_found": [],
        "alternative_gpu_metrics": [],
        "retention_info": {},
        "time_range_availability": {}
    }
    
    try:
        headers = {"Authorization": f"Bearer {THANOS_TOKEN}"}
        
        # Test Prometheus connection
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/label/__name__/values",
            headers=headers,
            verify=verify,
            timeout=30,
        )
        response.raise_for_status()
        all_metrics = response.json()["data"]
        debug_info["connection_status"] = "success"
        debug_info["total_metrics_available"] = len(all_metrics)
        
        # Look for GPU-related metrics
        gpu_related = []
        dcgm_metrics = []
        nvidia_metrics = []
        
        for metric in all_metrics:
            metric_lower = metric.lower()
            if "gpu" in metric_lower or "dcgm" in metric_lower:
                gpu_related.append(metric)
            if metric.startswith("DCGM_"):
                dcgm_metrics.append(metric)
            if "nvidia" in metric_lower:
                nvidia_metrics.append(metric)
        
        debug_info["gpu_metrics_found"] = gpu_related[:20]  # Limit to first 20
        debug_info["dcgm_metrics_found"] = dcgm_metrics
        debug_info["nvidia_metrics_found"] = nvidia_metrics
        
        # Check for alternative GPU monitoring metrics
        alternative_patterns = ["nvidia_smi", "gpu_utilization", "gpu_memory", "gpu_temperature"]
        alternative_metrics = []
        for pattern in alternative_patterns:
            matches = [m for m in all_metrics if pattern in m.lower()]
            alternative_metrics.extend(matches)
        debug_info["alternative_gpu_metrics"] = alternative_metrics
        
        # Test historical data availability by checking different time windows
        test_metric = "kube_pod_status_phase"  # Common metric that should exist
        if test_metric in all_metrics:
            time_windows = {
                "1_day": 24 * 3600,
                "1_week": 7 * 24 * 3600,
                "1_month": 30 * 24 * 3600,
                "3_months": 90 * 24 * 3600,
                "6_months": 180 * 24 * 3600,
                "1_year": 365 * 24 * 3600
            }
            
            current_time = int(datetime.now().timestamp())
            for window_name, seconds_ago in time_windows.items():
                start_time = current_time - seconds_ago
                try:
                    test_response = requests.get(
                        f"{PROMETHEUS_URL}/api/v1/query_range",
                        headers=headers,
                        params={
                            "query": test_metric,
                            "start": start_time,
                            "end": current_time,
                            "step": "1h"
                        },
                        verify=verify,
                        timeout=30,
                    )
                    if test_response.status_code == 200:
                        result = test_response.json()["data"]["result"]
                        debug_info["time_range_availability"][window_name] = {
                            "available": len(result) > 0,
                            "data_points": sum(len(series["values"]) for series in result) if result else 0
                        }
                    else:
                        debug_info["time_range_availability"][window_name] = {
                            "available": False,
                            "error": f"HTTP {test_response.status_code}"
                        }
                except Exception as e:
                    debug_info["time_range_availability"][window_name] = {
                        "available": False,
                        "error": str(e)
                    }
        
        # Get retention info if available
        try:
            config_response = requests.get(
                f"{PROMETHEUS_URL}/api/v1/status/config",
                headers=headers,
                verify=verify,
                timeout=30,
            )
            if config_response.status_code == 200:
                config_data = config_response.json()
                if "data" in config_data and "yaml" in config_data["data"]:
                    yaml_config = config_data["data"]["yaml"]
                    if "retention" in yaml_config.lower():
                        debug_info["retention_info"]["config_available"] = True
                        # Extract retention setting (simplified)
                        import re
                        retention_match = re.search(r'retention[:\s]+([^\n\r]+)', yaml_config, re.IGNORECASE)
                        if retention_match:
                            debug_info["retention_info"]["retention_setting"] = retention_match.group(1).strip()
        except Exception as e:
            debug_info["retention_info"]["error"] = str(e)
        
    except Exception as e:
        debug_info["connection_status"] = f"failed: {e}"
    
    return debug_info


@app.get("/test-historical")
def test_historical_queries():
    """Test endpoint for historical data queries"""
    test_queries = [
        "CPU usage in June",
        "Memory usage in April", 
        "Pods running last 6 months",
        "GPU utilization past 3 months",
        "Show me data from May 2024"
    ]
    
    results = {}
    for query in test_queries:
        try:
            start_ts, end_ts, time_info = extract_time_range_with_info(query, None, None)
            start_date = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M")
            end_date = datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M")
            
            results[query] = {
                "start_date": start_date,
                "end_date": end_date,
                "duration": time_info.get("duration_str", ""),
                "rate_syntax": time_info.get("rate_syntax", ""),
                "hours_span": time_info.get("hours", 0)
            }
        except Exception as e:
            results[query] = {"error": str(e)}
    
    return results


@app.get("/all-metrics")
def get_all_metrics_endpoint():
    """Debug endpoint to list all available metrics, grouped by type (vLLM, GPU, OpenShift)"""
    all_metrics = get_all_metrics()
    
    # Group metrics by type
    grouped_metrics = {
        "vLLM": {k: v for k, v in all_metrics.items() if "vLLM:" in k},
        "GPU": {k: v for k, v in all_metrics.items() if "GPU:" in k},
        "OpenShift": {k: v for k, v in all_metrics.items() if "OpenShift" in k}
    }
    
    return grouped_metrics


@app.get("/deployment-info")
def get_deployment_info(namespace: str, model: str):
    """Get deployment information for a specific model in a namespace"""
    try:
        headers = {"Authorization": f"Bearer {THANOS_TOKEN}"}
        
        # Try to get pod creation date from Kubernetes metrics
        query = f'kube_pod_info{{namespace="{namespace}"}}'
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            headers=headers,
            params={"query": query},
            verify=verify,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()["data"]["result"]
        
        deployment_date = None
        is_new_deployment = False
        
        if result:
            # Find the most recent pod for this model
            current_time = datetime.now()
            for pod_info in result:
                pod_name = pod_info["metric"].get("pod", "")
                # Check if pod name contains model-related keywords
                if any(keyword in pod_name.lower() for keyword in [model.lower().split("/")[-1], "llama", "instruct"]):
                    # Get pod creation time - this is an approximation since we don't have exact creation time
                    # We'll estimate based on when we first saw metrics for this namespace
                    break
            
            # Check if we have recent vLLM metrics for this namespace (within last 7 days)
            try:
                one_week_ago = int((current_time - timedelta(days=7)).timestamp())
                vllm_query = f'vllm:cache_config_info{{namespace="{namespace}"}}'
                vllm_response = requests.get(
                    f"{PROMETHEUS_URL}/api/v1/query_range",
                    headers=headers,
                    params={
                        "query": vllm_query,
                        "start": one_week_ago,
                        "end": int(current_time.timestamp()),
                        "step": "1h"
                    },
                    verify=verify,
                    timeout=30,
                )
                vllm_response.raise_for_status()
                vllm_result = vllm_response.json()["data"]["result"]
                
                if not vllm_result:
                    # No vLLM metrics in the last week - likely new deployment
                    is_new_deployment = True
                    deployment_date = current_time.strftime("%Y-%m-%d")
                else:
                    # Check if metrics started recently (within last 3 days)
                    three_days_ago = current_time - timedelta(days=3)
                    for series in vllm_result:
                        values = series.get("values", [])
                        if values:
                            first_timestamp = float(values[0][0])
                            first_time = datetime.fromtimestamp(first_timestamp)
                            if first_time > three_days_ago:
                                is_new_deployment = True
                                deployment_date = first_time.strftime("%Y-%m-%d")
                            break
                            
            except Exception as e:
                print(f"Error checking vLLM metrics timeline: {e}")
                # Fallback: assume new deployment if no vLLM metrics
                is_new_deployment = True
                deployment_date = current_time.strftime("%Y-%m-%d")
        else:
            # No pod info found - likely new deployment
            is_new_deployment = True
            deployment_date = datetime.now().strftime("%Y-%m-%d")
        
        message = None
        if is_new_deployment:
            message = (f"New deployment detected in namespace '{namespace}'. "
                      f"Metrics will appear once the model starts processing requests. "
                      f"This typically takes 5-10 minutes after the first inference request.")
        
        return {
            "is_new_deployment": is_new_deployment,
            "deployment_date": deployment_date,
            "message": message,
            "namespace": namespace,
            "model": model
        }
        
    except Exception as e:
        print(f"Error getting deployment info for {namespace}/{model}: {e}")
        # Fallback response
        return {
            "is_new_deployment": True,
            "deployment_date": datetime.now().strftime("%Y-%m-%d"),
            "message": f"Unable to determine deployment status. If this is a new deployment, metrics will appear shortly.",
            "namespace": namespace,
            "model": model
        }

# --- Report Generation ---

# helper functions for report generation
def save_report(report_content, format: str) -> str:
    report_id = str(uuid.uuid4())
    reports_dir = "/tmp/reports"
    os.makedirs(reports_dir, exist_ok=True)

    report_path = os.path.join(reports_dir, f"{report_id}.{format.lower()}")

    # Handle both string and bytes content
    if isinstance(report_content, bytes):
        with open(report_path, "wb") as f:
            f.write(report_content)
    else:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_content)

    return report_id


def get_report_path(report_id: str) -> str:
    """Get file path for report ID"""

    reports_dir = "/tmp/reports"

    # Try to find the file with any extension
    for file in os.listdir(reports_dir):
        if file.startswith(report_id):
            return os.path.join(reports_dir, file)

    raise FileNotFoundError(f"Report {report_id} not found")


def calculate_metric_stats(metric_data):
    """Calculate average and max values for metrics data"""
    if not metric_data:
        return None, None
    try:
        values = [point["value"] for point in metric_data]
        avg_val = sum(values) / len(values) if values else None
        max_val = max(values) if values else None
        return avg_val, max_val
    except Exception:
        return None, None


def build_report_schema(
    metrics_data: Dict[str, Any],
    summary: str,
    model_name: str,
    start_ts: int,
    end_ts: int,
    summarize_model_id: str,
    trend_chart_image: Optional[str] = None,
) -> ReportSchema:
    from datetime import datetime

    # Extract available metrics from the metrics_data dictionary
    key_metrics = list(metrics_data.keys())
    metric_cards = []
    for metric_name in key_metrics:
        data = metrics_data.get(metric_name, [])
        avg_val, max_val = calculate_metric_stats(data)
        metric_cards.append(
            MetricCard(
                name=metric_name,
                avg=avg_val,
                max=max_val,
                values=data,
            )
        )
    return ReportSchema(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        model_name=model_name,
        start_date=datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M:%S"),
        end_date=datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M:%S"),
        summarize_model_id=summarize_model_id,
        summary=summary,
        metrics=metric_cards,
        trend_chart_image=trend_chart_image,
    )


@app.get("/download_report/{report_id}")
def download_report(report_id: str):
    """Download generated report"""
    report_path = get_report_path(report_id)
    return FileResponse(report_path)


@app.post("/generate_report")
def generate_report(request: ReportRequest):
    """Generate report in requested format"""
    # Check if we have analysis data from UI session
    if (
        request.health_prompt is None
        or request.llm_summary is None
        or request.metrics_data is None
    ):
        raise HTTPException(
            status_code=400,
            detail="No analysis data provided. Please run analysis first.",
        )

    # Build the unified report schema once
    report_schema = build_report_schema(
        request.metrics_data,
        request.llm_summary,
        request.model_name,
        request.start_ts,
        request.end_ts,
        request.summarize_model_id,
        request.trend_chart_image,
    )

    # Generate report content based on format
    match request.format.lower():
        case "html":
            report_content = generate_html_report(report_schema)
        case "pdf":
            report_content = generate_pdf_report(report_schema)
        case "markdown":
            report_content = generate_markdown_report(report_schema)
        case _:
            raise HTTPException(
                status_code=400, detail=f"Unsupported format: {request.format}"
            )

    # Save and send
    report_id = save_report(report_content, request.format)
    return {"report_id": report_id, "download_url": f"/download_report/{report_id}"}

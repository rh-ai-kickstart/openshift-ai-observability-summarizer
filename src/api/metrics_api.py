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
sys.path.insert(0, parent_dir)  # For local development (src/)

# Import LLM client for summarization
from core.llm_client import summarize_with_llm
sys.path.insert(0, current_dir)  # For container deployment (/app/)

from report_assets.report_renderer import (
    generate_html_report,
    generate_markdown_report,
    generate_pdf_report,
    ReportSchema,
    MetricCard,
)

# Import from core business logic
from core.metrics import (
    get_models_helper,
    discover_vllm_metrics,
    discover_dcgm_metrics,
    discover_openshift_metrics,
    get_vllm_metrics,
    get_openshift_metrics,
    discover_cluster_metrics_dynamically,
    get_all_metrics,
    get_namespace_specific_metrics,
    fetch_metrics,
    fetch_openshift_metrics,
)
from core.analysis import (
    detect_anomalies,
    describe_trend,
    compute_health_score,
)
from core.reports import (
    save_report,
    get_report_path,
    build_report_schema,
)
from core.alerts import (
    fetch_alerts_from_prometheus,
    fetch_all_rule_definitions as _fetch_all_rule_definitions,
)
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
from core.models import (
    AnalyzeRequest,
    ChatRequest,
    ChatPrometheusRequest,
    ChatMetricsRequest,
    OpenShiftAnalyzeRequest,
    OpenShiftChatRequest,
    ReportRequest,
    MetricsCalculationRequest,
    MetricsCalculationResponse,
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


@app.get("/health")
def health():
    return {"status": "ok", "message": "✅ Correct Flow: Natural Language → PromQL → Thanos → LLM → Summary"}


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
        response = summarize_with_llm(prompt, req.summarize_model_id, req.api_key, max_tokens=1500)
        return {"response": _clean_llm_summary_string(response)}
    except Exception as e:
        # Handle API key errors and other LLM-related errors
        raise HTTPException(
            status_code=500, detail="Please check your API Key or try again later."
        )


@app.post("/chat-metrics")
def chat_metrics(req: ChatMetricsRequest):
    """
    Simple flow: Natural Language → PromQL → Thanos → LLM → Summary
    """
    try:
        print(f"🎯 Processing: {req.question}")
        
        # Step 1: Extract time range
        start_ts, end_ts = extract_time_range(req.question, req.start_ts, req.end_ts)
        print(f"⏰ Time range: {datetime.fromtimestamp(start_ts)} to {datetime.fromtimestamp(end_ts)}")
        
        # Step 2: Natural Language → PromQL
        # Clean model name for PromQL (extract model name after | if present)
        clean_model_name = req.model_name.split(' | ')[1].strip() if ' | ' in req.model_name else req.model_name
        
        # Determine if fleet-wide (no namespace filter) or namespace-specific
        is_fleet_wide = req.chat_scope == "fleet_wide"
        target_namespace = None if is_fleet_wide else req.namespace
        
        promql_queries = generate_promql_from_question(req.question, target_namespace, clean_model_name, start_ts, end_ts, is_fleet_wide)
        print(f"📝 Generated {len(promql_queries)} PromQL queries")
        
        # Step 3: Query Thanos with PromQL
        thanos_data = query_thanos_with_promql(promql_queries, start_ts, end_ts)
        
        # Step 4: Send results to LLM for summary
        summary_namespace = "Fleet-wide" if is_fleet_wide else req.namespace
        summary = generate_llm_summary(req.question, thanos_data, req.summarize_model_id, req.api_key, summary_namespace)
        # Step 5: Return summary for UI
        # Find the most relevant PromQL for the question (not just the first one)
        primary_promql = find_primary_promql_for_question(req.question, promql_queries)
        return {
            "promql": primary_promql,
            "summary": summary
        }
        
    except Exception as e:
        print(f"❌ Error in chat_metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def generate_promql_from_question(question: str, namespace: Optional[str], model_name: str, start_ts: int, end_ts: int, is_fleet_wide: bool = False) -> List[str]:
    """
    Step 2: Dynamically generate PromQL based on natural language analysis and available metrics
    """
    question_lower = question.lower()
    queries = []
    
    print(f"🔍 Analyzing question: {question}")
    
    # Calculate time range duration for dynamic intervals
    duration_seconds = end_ts - start_ts
    duration_hours = duration_seconds / 3600
    
    # Smart interval selection based on time range
    if duration_hours <= 1:
        rate_interval = "5m"  # For 1 hour, use 5m intervals (12 data points)
    elif duration_hours <= 6:
        rate_interval = "15m"  # For up to 6 hours, use 15m intervals
    elif duration_hours <= 24:
        rate_interval = "1h"  # For up to a day, use 1h intervals
    else:
        rate_interval = "6h"   # For longer periods, use 6h intervals
    
    print(f"📊 Time range: {duration_hours:.1f} hours, using interval: {rate_interval}")
    print(f"🌐 Scope: {'Fleet-wide' if is_fleet_wide else f'Namespace: {namespace}'}")
    
    # Step 1: Discover available metrics from Thanos
    available_metrics = discover_available_metrics_from_thanos(namespace, model_name, is_fleet_wide)
    print(f"🔍 Discovered {len(available_metrics)} available metrics")
    
    # Step 2: Direct pattern matching (simple and reliable)
    selected_queries, pattern_detected = select_queries_directly(question_lower, namespace, model_name, rate_interval, is_fleet_wide)
    print(f"🎯 Selected {len(selected_queries)} direct queries")
    
    # Step 3: Add the selected queries
    for query in selected_queries:
        if query and query not in queries:
            queries.append(query)
    
    # If no specific metrics discovered/selected, add intelligent defaults
    # But DON'T add defaults if user asked a SPECIFIC question that was successfully detected
    if len(queries) == 0 or (len(queries) == 1 and not pattern_detected):
        print("🔧 No specific metrics discovered, adding basic defaults")
        if is_fleet_wide:
            default_queries = [
                f'vllm:num_requests_running{{model_name="{model_name}"}}',  # No namespace filter
                'sum(kube_pod_status_phase{phase="Running"})',  # No namespace filter
                'avg(DCGM_FI_DEV_GPU_UTIL)'
            ]
        else:
            default_queries = [
                f'vllm:num_requests_running{{namespace="{namespace}", model_name="{model_name}"}}',
                f'sum(kube_pod_status_phase{{phase="Running", namespace="{namespace}"}})',
                f'avg(DCGM_FI_DEV_GPU_UTIL)'
            ]
        queries.extend(default_queries)
    
    return queries[:6]  # Limit to 6 queries


def extract_time_period_from_question(question: str) -> Optional[str]:
    """
    Extract time periods mentioned in the question and convert to PromQL rate intervals
    Examples: "1 hour" -> "1h", "30 minutes" -> "30m", "1.5 hours" -> "1h30m"
    """
    import re
    
    question_lower = question.lower()
    
    # Pattern matching for various time formats
    patterns = [
        # "1 hour", "2 hours", "1.5 hours" 
        (r'(\d+(?:\.\d+)?)\s*(?:hour|hr|hrs|hours)', lambda m: f"{int(float(m.group(1)) * 60)}m" if float(m.group(1)) < 1 else f"{int(float(m.group(1)))}h"),
        
        # "30 minutes", "45 mins"
        (r'(\d+(?:\.\d+)?)\s*(?:minute|min|mins|minutes)', lambda m: f"{int(float(m.group(1)))}m"),
        
        # "2 days", "1 day" 
        (r'(\d+(?:\.\d+)?)\s*(?:day|days)', lambda m: f"{int(float(m.group(1)) * 24)}h"),
        
        # "1h", "30m", "2d" (already in PromQL format)
        (r'(\d+(?:\.\d+)?[hdm])', lambda m: m.group(1)),
    ]
    
    for pattern, converter in patterns:
        match = re.search(pattern, question_lower)
        if match:
            try:
                result = converter(match)
                print(f"🕐 Extracted time period: '{match.group(0)}' -> '{result}'")
                return result
            except:
                continue
    
    return None


def select_queries_directly(question: str, namespace: Optional[str], model_name: str, rate_interval: str, is_fleet_wide: bool) -> tuple[List[str], bool]:
    """
    Direct pattern matching for reliable metric selection - simple and effective approach
    """
    queries = []
    question_lower = question.lower()
    pattern_detected = False  # Track if we successfully detect a specific pattern
    
    # Extract time period from question text for PromQL rate intervals
    question_rate_interval = extract_time_period_from_question(question_lower) or rate_interval
    print(f"🕐 Using rate interval: {question_rate_interval} (from question: {extract_time_period_from_question(question_lower) is not None})")
    
    # Helper function for clean label construction
    def get_vllm_labels():
        """Generate labels for vLLM metrics"""
        if is_fleet_wide:
            return f'{{model_name="{model_name}"}}' if model_name else ""
        else:
            return f'{{namespace="{namespace}", model_name="{model_name}"}}' if model_name else f'{{namespace="{namespace}"}}'
    
    # === ALERTS (HIGH PRIORITY) ===
    
    if any(word in question_lower for word in ["alert", "alerts", "firing", "warning", "critical", "yesterday", "problem", "issue"]):
        print("🎯 Detected: Alerts question")
        pattern_detected = True
        if is_fleet_wide:
            queries.append("ALERTS")  # No namespace filter for fleet-wide
        else:
            queries.append(f'ALERTS{{namespace="{namespace}"}}')  # Namespace-specific alerts
    
    # === vLLM METRICS ===
    
    # Latency patterns
    elif any(word in question_lower for word in ["latency", "p95", "p99", "percentile", "response time", "slow", "fast"]):
        print("🎯 Detected: Latency question")
        pattern_detected = True
        # Use _count metric as user specifically requested
        labels = get_vllm_labels()
        queries.append(f"rate(vllm:e2e_request_latency_seconds_count{labels}[{question_rate_interval}])")
    
    # Request patterns (specifically for vLLM requests, not Kubernetes pods)
    elif any(word in question_lower for word in ["vllm request", "model request", "inference request", "llm request"]):
        print("🎯 Detected: vLLM Request question")
        pattern_detected = True
        labels = get_vllm_labels()
        queries.append(f"vllm:num_requests_running{labels}")
    
    # Token patterns
    elif any(word in question_lower for word in ["token", "tokens", "prompt", "generation", "output"]):
        print("🎯 Detected: Token question")
        pattern_detected = True
        labels = get_vllm_labels()
        if "prompt" in question_lower:
            queries.append(f"sum(rate(vllm:request_prompt_tokens_created{labels}[{question_rate_interval}]))")
        elif "output" in question_lower or "generation" in question_lower:
            queries.append(f"sum(rate(vllm:request_generation_tokens_created{labels}[{question_rate_interval}]))")
        else:
            queries.append(f"sum(rate(vllm:request_prompt_tokens_created{labels}[{question_rate_interval}]))")
    
    # === GPU METRICS ===
    
    elif any(word in question_lower for word in ["gpu", "temperature", "utilization", "power"]):
        print("🎯 Detected: GPU question")
        pattern_detected = True
        if "temperature" in question_lower:
            queries.append("avg(DCGM_FI_DEV_GPU_TEMP)")
        elif "utilization" in question_lower or "usage" in question_lower:
            queries.append("avg(DCGM_FI_DEV_GPU_UTIL)")
        elif "power" in question_lower:
            queries.append("avg(DCGM_FI_DEV_POWER_USAGE)")
        else:
            # Default GPU question - show utilization
            queries.append("avg(DCGM_FI_DEV_GPU_UTIL)")
    
    # === KUBERNETES/OPENSHIFT METRICS ===
    
    elif any(word in question_lower for word in ["pod", "pods", "number of pods", "how many pods"]):
        print("🎯 Detected: Pod question")
        pattern_detected = True
        if is_fleet_wide:
            if "running" in question_lower:
                queries.append('sum(kube_pod_status_phase{phase="Running"})')
            elif "failed" in question_lower:
                queries.append('sum(kube_pod_status_phase{phase="Failed"})')
            else:
                queries.append('sum(kube_pod_status_phase{phase="Running"})')
        else:
            if "running" in question_lower:
                queries.append(f'sum(kube_pod_status_phase{{phase="Running", namespace="{namespace}"}})')
            elif "failed" in question_lower:
                queries.append(f'sum(kube_pod_status_phase{{phase="Failed", namespace="{namespace}"}})')
            else:
                queries.append(f'sum(kube_pod_status_phase{{phase="Running", namespace="{namespace}"}})')
    
    elif any(word in question_lower for word in ["deployment", "deployments"]):
        print("🎯 Detected: Deployment question")
        pattern_detected = True
        if is_fleet_wide:
            queries.append('sum(kube_deployment_spec_replicas)')
        else:
            queries.append(f'sum(kube_deployment_spec_replicas{{namespace="{namespace}"}})')
    
    elif any(word in question_lower for word in ["service", "services"]):
        print("🎯 Detected: Service question")
        pattern_detected = True
        if is_fleet_wide:
            queries.append('sum(kube_service_info)')
        else:
            queries.append(f'sum(kube_service_info{{namespace="{namespace}"}})')
    
    elif any(word in question_lower for word in ["node", "nodes"]):
        print("🎯 Detected: Node question")
        pattern_detected = True
        queries.append('sum(kube_node_info)')  # Nodes are cluster-wide
    
    elif any(word in question_lower for word in ["cpu", "memory", "resource"]):
        print("🎯 Detected: Resource question")
        pattern_detected = True
        if "cpu" in question_lower:
            if is_fleet_wide:
                queries.append(f'sum(rate(container_cpu_usage_seconds_total[{question_rate_interval}]))')
            else:
                queries.append(f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}"}}[{question_rate_interval}]))')
        elif "memory" in question_lower:
            if is_fleet_wide:
                queries.append('sum(container_memory_usage_bytes)')
            else:
                queries.append(f'sum(container_memory_usage_bytes{{namespace="{namespace}"}})')
        else:
            if is_fleet_wide:
                queries.append(f'sum(rate(container_cpu_usage_seconds_total[{question_rate_interval}]))')
            else:
                queries.append(f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}"}}[{question_rate_interval}]))')
    
    # === GENERIC FALLBACKS ===
    
    elif "model" in question_lower and ("deploy" in question_lower or "available" in question_lower):
        print("🎯 Detected: Model deployment question")
        pattern_detected = True
        labels = get_vllm_labels()
        queries.append(f"vllm:num_requests_running{labels}")
    
    elif any(word in question_lower for word in ["all models", "models in", "model info", "model status"]):
        print("🎯 Detected: All models question")
        pattern_detected = True
        if is_fleet_wide:
            queries.extend([
                "sum by (model_name) (vllm:num_requests_running)",
                f"sum by (model_name) (rate(vllm:e2e_request_latency_seconds_count[{question_rate_interval}]))"
            ])
        else:
            queries.extend([
                f'sum by (model_name) (vllm:num_requests_running{{namespace="{namespace}"}})',
                f'sum by (model_name) (rate(vllm:e2e_request_latency_seconds_count{{namespace="{namespace}"}}[{question_rate_interval}]))'
            ])
    
    else:
        print("🎯 No specific pattern detected, using defaults")
        # Add some default useful metrics including alerts for context
        labels = get_vllm_labels()
        queries.extend([
            f"vllm:num_requests_running{labels}",
            f'sum(kube_pod_status_phase{{phase="Running", namespace="{namespace}"}})' if not is_fleet_wide else 'sum(kube_pod_status_phase{phase="Running"})',
            f'ALERTS{{namespace="{namespace}"}}' if not is_fleet_wide else 'ALERTS',
            "avg(DCGM_FI_DEV_GPU_UTIL)"
        ])
    
    print(f"🔧 Generated queries: {queries}")
    print(f"🔍 Pattern detected: {pattern_detected}")
    return queries[:3], pattern_detected  # Limit to 3 queries for focus


def discover_available_metrics_from_thanos(namespace: Optional[str], model_name: str, is_fleet_wide: bool) -> List[Dict[str, Any]]:
    """
    1. Fetch ALL metrics from cluster
    2. Categorize EVERYTHING  
    3. Return comprehensive categorized list
    """
    try:
        headers = {"Authorization": f"Bearer {THANOS_TOKEN}"}
        
        # Step 1: Get ALL available metric names from cluster
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/label/__name__/values",
            headers=headers,
            verify=verify,
            timeout=30
        )
        response.raise_for_status()
        all_metric_names = response.json()["data"]
        
        print(f"📋 Found {len(all_metric_names)} total metrics in Thanos")
        
        # Step 2: Categorize ALL metrics (not just filtered subset)
        categorized_metrics = []
        
        for metric_name in all_metric_names:
            metric_info = categorize_any_metric(metric_name, namespace, model_name, is_fleet_wide)
            if metric_info:  # Only add if we can categorize it
                categorized_metrics.append(metric_info)
        
        print(f"🏷️  Categorized {len(categorized_metrics)} metrics into types")
        return categorized_metrics
        
    except Exception as e:
        print(f"❌ Error discovering metrics from Thanos: {e}")
        return []


def categorize_any_metric(metric_name: str, namespace: Optional[str], model_name: str, is_fleet_wide: bool) -> Optional[Dict[str, Any]]:
    """
    Comprehensive categorization of ANY metric from the cluster
    """
    # Convert to lowercase for pattern matching
    name_lower = metric_name.lower()
    
    # === vLLM/LLM Metrics (HIGHEST PRIORITY) ===
    if metric_name.startswith("vllm:") or "llm" in name_lower:
        return categorize_vllm_metric(metric_name, namespace, model_name, is_fleet_wide)
    
    # === Prometheus/Monitoring Metrics (HIGH PRIORITY - before generic latency) ===
    elif metric_name.startswith("prometheus_") or metric_name.startswith("alertmanager_"):
        return categorize_monitoring_metric(metric_name)
    
    # === Alerting Metrics ===
    elif metric_name == "ALERTS" or "alert" in name_lower:
        return {
            "name": metric_name,
            "type": "alerts",
            "category": "alerting",
            "description": "System alerts and notifications"
        }
    
    # === Kubernetes/OpenShift Metrics ===
    elif metric_name.startswith("kube_") or metric_name.startswith("openshift_"):
        return categorize_k8s_metric(metric_name, namespace, is_fleet_wide)
    
    # === GPU/Hardware Metrics ===
    elif metric_name.startswith("DCGM_") or "gpu" in name_lower or "nvidia" in name_lower:
        return categorize_gpu_metric(metric_name)
    
    # === Container/Docker Metrics ===
    elif metric_name.startswith("container_"):
        return categorize_container_metric(metric_name)
    
    # === Node/System Metrics ===
    elif metric_name.startswith("node_"):
        return categorize_node_metric(metric_name)
    
    # === Network Metrics ===
    elif any(net_keyword in name_lower for net_keyword in ["network", "net_", "tcp_", "udp_", "http_"]):
        return categorize_network_metric(metric_name)
    
    # === Storage/Disk Metrics ===
    elif any(storage_keyword in name_lower for storage_keyword in ["disk_", "filesystem_", "storage_", "volume_"]):
        return categorize_storage_metric(metric_name)
    
    # === Application/Custom Metrics (LOWER PRIORITY) ===
    elif any(app_keyword in name_lower for app_keyword in ["request", "response", "latency", "error", "rate", "duration"]):
        return categorize_application_metric(metric_name)
    
    # === Generic/Unknown Metrics ===
    else:
        return categorize_generic_metric(metric_name)


def categorize_container_metric(metric_name: str) -> Optional[Dict[str, Any]]:
    """Categorize container metrics"""
    if "cpu" in metric_name:
        return {"name": metric_name, "type": "container_cpu", "category": "resource", "description": "Container CPU usage"}
    elif "memory" in metric_name:
        return {"name": metric_name, "type": "container_memory", "category": "resource", "description": "Container memory usage"}
    elif "network" in metric_name:
        return {"name": metric_name, "type": "container_network", "category": "network", "description": "Container network stats"}
    elif "fs" in metric_name or "filesystem" in metric_name:
        return {"name": metric_name, "type": "container_storage", "category": "storage", "description": "Container filesystem usage"}
    else:
        return {"name": metric_name, "type": "container_other", "category": "resource", "description": "Container metrics"}


def categorize_node_metric(metric_name: str) -> Optional[Dict[str, Any]]:
    """Categorize node/system metrics"""
    if "cpu" in metric_name:
        return {"name": metric_name, "type": "node_cpu", "category": "system", "description": "Node CPU metrics"}
    elif "memory" in metric_name:
        return {"name": metric_name, "type": "node_memory", "category": "system", "description": "Node memory metrics"}
    elif "disk" in metric_name or "filesystem" in metric_name:
        return {"name": metric_name, "type": "node_storage", "category": "system", "description": "Node storage metrics"}
    elif "network" in metric_name:
        return {"name": metric_name, "type": "node_network", "category": "system", "description": "Node network metrics"}
    elif "load" in metric_name:
        return {"name": metric_name, "type": "node_load", "category": "system", "description": "Node load metrics"}
    else:
        return {"name": metric_name, "type": "node_other", "category": "system", "description": "Node system metrics"}


def categorize_network_metric(metric_name: str) -> Optional[Dict[str, Any]]:
    """Categorize network metrics"""
    if "latency" in metric_name or "rtt" in metric_name:
        return {"name": metric_name, "type": "network_latency", "category": "network", "description": "Network latency metrics"}
    elif "throughput" in metric_name or "bandwidth" in metric_name:
        return {"name": metric_name, "type": "network_throughput", "category": "network", "description": "Network throughput metrics"}
    elif "error" in metric_name or "drop" in metric_name:
        return {"name": metric_name, "type": "network_errors", "category": "network", "description": "Network error metrics"}
    elif "connection" in metric_name or "tcp" in metric_name:
        return {"name": metric_name, "type": "network_connections", "category": "network", "description": "Network connection metrics"}
    else:
        return {"name": metric_name, "type": "network_other", "category": "network", "description": "Network metrics"}


def categorize_storage_metric(metric_name: str) -> Optional[Dict[str, Any]]:
    """Categorize storage metrics"""
    if "usage" in metric_name or "used" in metric_name:
        return {"name": metric_name, "type": "storage_usage", "category": "storage", "description": "Storage usage metrics"}
    elif "iops" in metric_name or "ops" in metric_name:
        return {"name": metric_name, "type": "storage_iops", "category": "storage", "description": "Storage IOPS metrics"}
    elif "latency" in metric_name or "response" in metric_name:
        return {"name": metric_name, "type": "storage_latency", "category": "storage", "description": "Storage latency metrics"}
    else:
        return {"name": metric_name, "type": "storage_other", "category": "storage", "description": "Storage metrics"}


def categorize_application_metric(metric_name: str) -> Optional[Dict[str, Any]]:
    """Categorize application/custom metrics"""
    if "latency" in metric_name and "bucket" in metric_name:
        return {"name": metric_name, "type": "app_latency_histogram", "category": "performance", "description": "Application latency histogram"}
    elif "latency" in metric_name and "sum" in metric_name:
        return {"name": metric_name, "type": "app_latency_sum", "category": "performance", "description": "Application latency sum"}
    elif "latency" in metric_name and "count" in metric_name:
        return {"name": metric_name, "type": "app_latency_count", "category": "performance", "description": "Application latency count"}
    elif "request" in metric_name and ("total" in metric_name or "count" in metric_name):
        return {"name": metric_name, "type": "app_requests", "category": "throughput", "description": "Application request metrics"}
    elif "error" in metric_name or "failed" in metric_name:
        return {"name": metric_name, "type": "app_errors", "category": "reliability", "description": "Application error metrics"}
    elif "duration" in metric_name:
        return {"name": metric_name, "type": "app_duration", "category": "performance", "description": "Application duration metrics"}
    else:
        return {"name": metric_name, "type": "app_custom", "category": "application", "description": "Custom application metrics"}


def categorize_monitoring_metric(metric_name: str) -> Optional[Dict[str, Any]]:
    """Categorize monitoring system metrics"""
    return {"name": metric_name, "type": "monitoring", "category": "infrastructure", "description": "Monitoring system metrics"}


def categorize_generic_metric(metric_name: str) -> Optional[Dict[str, Any]]:
    """Fallback categorization for unknown metrics"""
    # Try to infer from common patterns
    if "_total" in metric_name or "_count" in metric_name:
        return {"name": metric_name, "type": "counter", "category": "generic", "description": "Counter metric"}
    elif "_bucket" in metric_name:
        return {"name": metric_name, "type": "histogram", "category": "generic", "description": "Histogram metric"}
    elif "_sum" in metric_name:
        return {"name": metric_name, "type": "sum", "category": "generic", "description": "Sum metric"}
    else:
        return {"name": metric_name, "type": "gauge", "category": "generic", "description": "Gauge metric"}


def categorize_vllm_metric(metric_name: str, namespace: Optional[str], model_name: str, is_fleet_wide: bool) -> Optional[Dict[str, Any]]:
    """
    Categorize and validate vLLM metrics based on what's actually available
    """
    # Check if this metric might have data for our specific context
    if not is_fleet_wide and namespace:
        # For namespace-specific queries, we'd ideally check if this metric has data for our namespace
        # For now, we'll include all vLLM metrics and let the PromQL filter
        pass
    
    # Categorize based on metric name patterns
    if "latency" in metric_name and "bucket" in metric_name:
        return {
            "name": metric_name,
            "type": "latency_histogram", 
            "category": "performance",
            "description": "Request latency histogram for percentile calculations"
        }
    elif "latency" in metric_name and ("sum" in metric_name or "total" in metric_name):
        return {
            "name": metric_name,
            "type": "latency_sum",
            "category": "performance", 
            "description": "Total latency sum"
        }
    elif "latency" in metric_name and "count" in metric_name:
        return {
            "name": metric_name,
            "type": "latency_count",
            "category": "performance",
            "description": "Request count for latency calculation"
        }
    elif "requests_running" in metric_name or "num_requests" in metric_name:
        return {
            "name": metric_name,
            "type": "requests_active",
            "category": "load",
            "description": "Currently active requests"
        }
    elif "token" in metric_name and "prompt" in metric_name:
        return {
            "name": metric_name,
            "type": "tokens_prompt", 
            "category": "throughput",
            "description": "Prompt token metrics"
        }
    elif "token" in metric_name and ("generation" in metric_name or "output" in metric_name):
        return {
            "name": metric_name,
            "type": "tokens_output",
            "category": "throughput", 
            "description": "Output token metrics"
        }
    elif "throughput" in metric_name:
        return {
            "name": metric_name,
            "type": "throughput",
            "category": "performance",
            "description": "Generation throughput"
        }
    elif "cache" in metric_name:
        return {
            "name": metric_name,
            "type": "cache",
            "category": "resource",
            "description": "Cache utilization"
        }
    
    return None


def categorize_k8s_metric(metric_name: str, namespace: Optional[str], is_fleet_wide: bool) -> Optional[Dict[str, Any]]:
    """
    Categorize Kubernetes/OpenShift metrics
    """
    if "pod_status_phase" in metric_name:
        return {
            "name": metric_name,
            "type": "pods",
            "category": "workload",
            "description": "Pod status information"
        }
    elif "deployment" in metric_name and "replicas" in metric_name:
        return {
            "name": metric_name,
            "type": "deployments", 
            "category": "workload",
            "description": "Deployment replica status"
        }
    elif "container_cpu_usage" in metric_name:
        return {
            "name": metric_name,
            "type": "cpu",
            "category": "resource",
            "description": "CPU usage"
        }
    elif "container_memory_usage" in metric_name:
        return {
            "name": metric_name,
            "type": "memory",
            "category": "resource", 
            "description": "Memory usage"
        }
    
    return None


def categorize_gpu_metric(metric_name: str) -> Optional[Dict[str, Any]]:
    """
    Categorize GPU/DCGM metrics
    """
    if "GPU_TEMP" in metric_name:
        return {
            "name": metric_name,
            "type": "gpu_temperature",
            "category": "gpu",
            "description": "GPU temperature"
        }
    elif "GPU_UTIL" in metric_name:
        return {
            "name": metric_name,
            "type": "gpu_utilization",
            "category": "gpu", 
            "description": "GPU utilization"
        }
    elif "POWER_USAGE" in metric_name:
        return {
            "name": metric_name,
            "type": "gpu_power",
            "category": "gpu",
            "description": "GPU power usage"
        }
    
    return None


def intelligent_metric_selection(question: str, available_metrics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Intelligently select the most relevant metrics based on the question and what's available
    Now works with comprehensive metric categorization
    """
    selected = []
    question_lower = question.lower()
    
    print(f"🧠 Analyzing question: '{question}'")
    print(f"📊 Available metric categories: {set(m['category'] for m in available_metrics)}")
    
    # Intent detection with priority scoring  
    intents = []
    
    # === High Priority Intents ===
    
    # Latency intent (highest priority)
    if any(word in question_lower for word in ["latency", "p95", "p99", "percentile", "response time", "slow", "fast", "duration"]):
        intents.append(("latency", 10))
    
    # Performance intent
    if any(word in question_lower for word in ["performance", "throughput", "tps", "qps", "speed"]):
        intents.append(("performance", 9))
    
    # Request/load intent  
    if any(word in question_lower for word in ["request", "requests", "running", "active", "concurrent", "load"]):
        intents.append(("requests", 8))
    
    # === Medium Priority Intents ===
    
    # Token/LLM specific intent
    if any(word in question_lower for word in ["token", "tokens", "prompt", "generation", "output", "llm", "model"]):
        intents.append(("tokens", 7))
    
    # System resource intent
    if any(word in question_lower for word in ["cpu", "memory", "disk", "storage", "resource"]):
        intents.append(("resources", 7))
    
    # GPU intent
    if any(word in question_lower for word in ["gpu", "temperature", "utilization", "power", "nvidia"]):
        intents.append(("gpu", 6))
    
    # === Lower Priority Intents ===
    
    # Network intent
    if any(word in question_lower for word in ["network", "connection", "bandwidth", "tcp", "udp"]):
        intents.append(("network", 5))
    
    # Error/reliability intent
    if any(word in question_lower for word in ["error", "failed", "failure", "down", "unavailable"]):
        intents.append(("errors", 6))
    
    # Alerting intent
    if any(word in question_lower for word in ["alert", "alerts", "firing", "warning", "critical"]):
        intents.append(("alerts", 8))
    
    # Pod/workload intent
    if any(word in question_lower for word in ["pod", "pods", "deployment", "replica", "container"]):
        intents.append(("workload", 6))
    
    # Sort by priority
    intents.sort(key=lambda x: x[1], reverse=True)
    
    print(f"🎯 Detected intents: {[(intent, priority) for intent, priority in intents[:3]]}")
    
    # Select metrics based on intent priority (process top 3 intents)
    for intent_type, priority in intents[:3]:
        
        if intent_type == "latency":
            selected.extend(select_latency_metrics(available_metrics, question_lower))
        
        elif intent_type == "performance":
            # Performance metrics: throughput, latency, requests
            perf_metrics = [m for m in available_metrics if m["category"] in ["performance", "throughput"]]
            selected.extend(perf_metrics[:2])
        
        elif intent_type == "requests":
            # Request metrics: active requests, request counts
            request_metrics = [m for m in available_metrics if 
                             m["type"] in ["requests_active", "app_requests"] or "request" in m["type"]]
            selected.extend(request_metrics[:1])
        
        elif intent_type == "tokens":
            # Token/LLM specific metrics
            if "prompt" in question_lower:
                token_metrics = [m for m in available_metrics if "prompt" in m["type"]]
            elif "output" in question_lower or "generation" in question_lower:
                token_metrics = [m for m in available_metrics if "output" in m["type"] or "generation" in m["type"]]
            else:
                token_metrics = [m for m in available_metrics if "token" in m["type"]]
            selected.extend(token_metrics[:1])
        
        elif intent_type == "resources":
            # System resource metrics: CPU, memory, storage
            if "cpu" in question_lower:
                resource_metrics = [m for m in available_metrics if "cpu" in m["type"]]
            elif "memory" in question_lower:
                resource_metrics = [m for m in available_metrics if "memory" in m["type"]]
            elif "disk" in question_lower or "storage" in question_lower:
                resource_metrics = [m for m in available_metrics if m["category"] == "storage"]
            else:
                resource_metrics = [m for m in available_metrics if m["category"] in ["resource", "system"]]
            selected.extend(resource_metrics[:2])
        
        elif intent_type == "gpu":
            # GPU metrics
            if "temperature" in question_lower:
                gpu_metrics = [m for m in available_metrics if m["type"] == "gpu_temperature"]
            elif "utilization" in question_lower:
                gpu_metrics = [m for m in available_metrics if m["type"] == "gpu_utilization"]
            elif "power" in question_lower:
                gpu_metrics = [m for m in available_metrics if m["type"] == "gpu_power"]
            else:
                gpu_metrics = [m for m in available_metrics if m["category"] == "gpu"]
            selected.extend(gpu_metrics[:1])
        
        elif intent_type == "network":
            # Network metrics
            net_metrics = [m for m in available_metrics if m["category"] == "network"]
            selected.extend(net_metrics[:1])
        
        elif intent_type == "errors":
            # Error/reliability metrics
            error_metrics = [m for m in available_metrics if 
                           m["category"] == "reliability" or "error" in m["type"]]
            selected.extend(error_metrics[:1])
        
        elif intent_type == "alerts":
            # Alert metrics
            alert_metrics = [m for m in available_metrics if m["type"] == "alerts"]
            selected.extend(alert_metrics[:1])
        
        elif intent_type == "workload":
            # Pod/workload metrics
            workload_metrics = [m for m in available_metrics if 
                              m["category"] == "workload" or "pod" in m["type"]]
            selected.extend(workload_metrics[:1])
    
    # Remove duplicates while preserving order
    seen = set()
    unique_selected = []
    for metric in selected:
        metric_id = metric["name"]
        if metric_id not in seen:
            seen.add(metric_id)
            unique_selected.append(metric)
    
    print(f"✅ Selected {len(unique_selected)} metrics: {[m['type'] for m in unique_selected]}")
    return unique_selected[:4]  # Limit to top 4 most relevant


def select_latency_metrics(available_metrics: List[Dict[str, Any]], question_lower: str) -> List[Dict[str, Any]]:
    """
    Smart latency metric selection with family grouping and priority-based selection
    """
    # Group related latency metrics by base name
    latency_families = {}
    
    latency_types = ["latency_histogram", "latency_sum", "latency_count", 
                    "app_latency_histogram", "app_latency_sum", "app_latency_count",
                    "network_latency", "storage_latency"]
    
    for m in available_metrics:
        if m["type"] in latency_types:
            # Extract base metric name (remove suffixes)
            base_name = m["name"]
            for suffix in ["_bucket", "_count", "_sum", "_total"]:
                if base_name.endswith(suffix):
                    base_name = base_name[:-len(suffix)]
                    break
            
            if base_name not in latency_families:
                latency_families[base_name] = []
            latency_families[base_name].append(m)
    
    # Priority order for latency families (higher = better)
    def get_family_priority(family_name: str) -> int:
        if family_name.startswith("vllm:"):
            return 100  # Highest - vLLM metrics
        elif "request" in family_name and "latency" in family_name:
            return 80   # High - Application request latency
        elif family_name.startswith("alertmanager_"):
            return 20   # Low - Monitoring latency  
        elif family_name.startswith("prometheus_"):
            return 10   # Lowest - Prometheus internal latency
        else:
            return 50   # Medium - Other latency metrics
    
    # Sort families by priority
    sorted_families = sorted(
        latency_families.items(),
        key=lambda x: get_family_priority(x[0]),
        reverse=True
    )
    
    print(f"🔧 Latency families by priority: {[(name, get_family_priority(name)) for name, _ in sorted_families[:3]]}")
    
    selected = []
    
    # For each latency family (prioritized), pick the best metric
    for family_name, family_metrics in sorted_families[:2]:  # Top 2 priority families
        if "p95" in question_lower or "p99" in question_lower or "percentile" in question_lower:
            # For percentiles, prefer buckets, fallback to count
            bucket_metric = next((m for m in family_metrics if "histogram" in m["type"]), None)
            if bucket_metric:
                selected.append(bucket_metric)
            else:
                count_metric = next((m for m in family_metrics if "count" in m["type"]), None)
                if count_metric:
                    selected.append(count_metric)
        else:
            # Default: prefer count metric (as user specified)
            count_metric = next((m for m in family_metrics if "count" in m["type"]), None)
            if count_metric:
                selected.append(count_metric)
            else:
                # Fallback to any latency metric in family
                selected.append(family_metrics[0])
    
    return selected


def generate_promql_from_discovered_metric(metric_info: Dict[str, Any], namespace: Optional[str], model_name: str, rate_interval: str, is_fleet_wide: bool) -> str:
    """
    Generate PromQL using discovered metric information
    """
    metric_name = metric_info["name"]
    metric_type = metric_info["type"]
    
    def get_labels() -> str:
        """Generate label filters"""
        labels = []
        if not is_fleet_wide and namespace:
            labels.append(f'namespace="{namespace}"')
        if "vllm:" in metric_name:  # Only add model_name for vLLM metrics
            labels.append(f'model_name="{model_name}"')
        return "{" + ", ".join(labels) + "}" if labels else ""
    
    labels = get_labels()
    
    # Generate PromQL based on metric type
    if metric_type == "latency_histogram":
        # For histogram buckets, generate percentile query
        return f'histogram_quantile(0.95, sum(rate({metric_name}{labels}[{rate_interval}])) by (le))'
    
    elif metric_type == "latency_sum" or metric_type == "latency_count":
        # For sum/count metrics, calculate average latency
        return f'rate({metric_name}{labels}[{rate_interval}])'
    
    elif metric_type == "requests_active":
        # Current active requests (no rate needed)
        return f'{metric_name}{labels}'
    
    elif metric_type in ["tokens_prompt", "tokens_output", "throughput"]:
        # Rate-based metrics
        return f'sum(rate({metric_name}{labels}[{rate_interval}]))'
    
    elif metric_type == "cache":
        # Average cache utilization
        return f'avg({metric_name}{labels})'
    
    elif metric_type == "pods":
        # Pod status with phase filter
        if is_fleet_wide:
            return f'sum({metric_name}{{phase="Running"}})'
        else:
            return f'sum({metric_name}{{phase="Running", namespace="{namespace}"}})'
    
    elif metric_type in ["cpu", "memory"]:
        # Resource metrics
        if "cpu" in metric_name:
            return f'sum(rate({metric_name}{labels}[{rate_interval}]))'
        else:
            return f'sum({metric_name}{labels})'
    
    elif metric_type.startswith("gpu_"):
        # GPU metrics (typically no labels needed)
        return f'avg({metric_name})'
    
    else:
        # Generic fallback
        return f'{metric_name}{labels}'


def analyze_question_for_metrics(question: str) -> List[str]:
    """
    Analyze the natural language question to detect what metrics are being asked about
    """
    detected = []
    
    # Performance and latency detection
    if any(word in question for word in ["latency", "p95", "p99", "percentile", "response time", "slow", "fast"]):
        detected.append("latency")
    
    if any(word in question for word in ["throughput", "tps", "requests per", "qps", "performance"]):
        detected.append("throughput")
    
    # Token and generation detection
    if any(word in question for word in ["token", "tokens generated", "prompt token", "output token"]):
        if any(word in question for word in ["prompt", "input"]):
            detected.append("prompt_tokens")
        elif any(word in question for word in ["output", "generation", "generated"]):
            detected.append("output_tokens")
        else:
            detected.append("tokens")
    
    # Request detection
    if any(word in question for word in ["request", "requests running", "active request", "concurrent"]):
        detected.append("requests")
    
    # Cache detection
    if any(word in question for word in ["cache", "cache usage", "cache hit", "memory cache"]):
        detected.append("cache")
    
    # GPU detection
    if any(word in question for word in ["gpu", "graphics", "cuda", "acceleration"]):
        if any(word in question for word in ["temperature", "temp", "heat", "thermal"]):
            detected.append("gpu_temperature")
        elif any(word in question for word in ["utilization", "usage", "busy", "load"]):
            detected.append("gpu_utilization")
        elif any(word in question for word in ["power", "watt", "energy"]):
            detected.append("gpu_power")
        elif any(word in question for word in ["memory", "vram", "framebuffer"]):
            detected.append("gpu_memory")
        else:
            detected.append("gpu_general")
    
    # Kubernetes/OpenShift detection
    if any(word in question for word in ["pod", "pods", "container"]):
        if any(word in question for word in ["running", "active", "healthy"]):
            detected.append("pods_running")
        elif any(word in question for word in ["failed", "error", "crash"]):
            detected.append("pods_failed")
        elif any(word in question for word in ["restart", "restarting"]):
            detected.append("pod_restarts")
        else:
            detected.append("pods_general")
    
    if any(word in question for word in ["deployment", "deploy", "replica"]):
        detected.append("deployments")
    
    if any(word in question for word in ["service", "endpoint", "network"]):
        detected.append("services")
    
    if any(word in question for word in ["cpu", "processor", "compute"]):
        detected.append("cpu")
    
    if any(word in question for word in ["memory", "ram", "storage"]):
        detected.append("memory")
    
    # Alert detection
    if any(word in question for word in ["alert", "alarm", "notification"]):
        if any(word in question for word in ["firing", "active", "triggered"]):
            detected.append("alerts_firing")
        elif any(word in question for word in ["critical", "severe", "urgent"]):
            detected.append("alerts_critical")
        else:
            detected.append("alerts_general")
    
    return detected


def generate_dynamic_promql(metric_type: str, namespace: Optional[str], model_name: str, rate_interval: str, question: str, is_fleet_wide: bool = False) -> str:
    """
    Generate PromQL dynamically based on the specific metric type and question context
    """
    
    def get_labels(include_model: bool = True, include_namespace: bool = True) -> str:
        """Helper to generate label filters based on scope"""
        labels = []
        if include_namespace and not is_fleet_wide and namespace:
            labels.append(f'namespace="{namespace}"')
        if include_model:
            labels.append(f'model_name="{model_name}"')
        return "{" + ", ".join(labels) + "}" if labels else ""
    
    # vLLM Metrics
    if metric_type == "latency":
        labels = get_labels()
        # Check if specific percentile is mentioned
        if "p99" in question or "99th" in question:
            return f'histogram_quantile(0.99, sum(rate(vllm:e2e_request_latency_seconds_bucket{labels}[{rate_interval}])) by (le))'
        elif "p90" in question or "90th" in question:
            return f'histogram_quantile(0.90, sum(rate(vllm:e2e_request_latency_seconds_bucket{labels}[{rate_interval}])) by (le))'
        else:  # Default to P95
            return f'histogram_quantile(0.95, sum(rate(vllm:e2e_request_latency_seconds_bucket{labels}[{rate_interval}])) by (le))'
    
    elif metric_type == "throughput":
        labels = get_labels()
        return f'sum(rate(vllm:avg_generation_throughput_toks_per_s{labels}[{rate_interval}]))'
    
    elif metric_type == "prompt_tokens":
        labels = get_labels()
        return f'sum(rate(vllm:request_prompt_tokens_created{labels}[{rate_interval}]))'
    
    elif metric_type == "output_tokens":
        labels = get_labels()
        return f'sum(rate(vllm:request_generation_tokens_created{labels}[{rate_interval}]))'
    
    elif metric_type == "tokens":
        labels = get_labels()
        # If general tokens, include both prompt and output
        return f'sum(rate(vllm:request_prompt_tokens_created{labels}[{rate_interval}])) + sum(rate(vllm:request_generation_tokens_created{labels}[{rate_interval}]))'
    
    elif metric_type == "requests":
        labels = get_labels()
        return f'vllm:num_requests_running{labels}'
    
    elif metric_type == "cache":
        labels = get_labels()
        return f'avg(vllm:gpu_cache_usage_perc{labels})'
    
    # GPU Metrics (typically no namespace filters)
    elif metric_type == "gpu_temperature":
        return 'avg(DCGM_FI_DEV_GPU_TEMP)'
    
    elif metric_type == "gpu_utilization":
        return 'avg(DCGM_FI_DEV_GPU_UTIL)'
    
    elif metric_type == "gpu_power":
        return 'avg(DCGM_FI_DEV_POWER_USAGE)'
    
    elif metric_type == "gpu_memory":
        return 'avg(DCGM_FI_DEV_FB_USED) / (1024*1024*1024)'
    
    elif metric_type == "gpu_general":
        return 'avg(DCGM_FI_DEV_GPU_UTIL)'
    
    # Kubernetes/OpenShift Metrics
    elif metric_type == "pods_running":
        if is_fleet_wide:
            return 'sum(kube_pod_status_phase{phase="Running"})'
        else:
            return f'sum(kube_pod_status_phase{{phase="Running", namespace="{namespace}"}})'
    
    elif metric_type == "pods_failed":
        if is_fleet_wide:
            return 'sum(kube_pod_status_phase{phase="Failed"})'
        else:
            return f'sum(kube_pod_status_phase{{phase="Failed", namespace="{namespace}"}})'
    
    elif metric_type == "pod_restarts":
        if is_fleet_wide:
            return f'sum(rate(kube_pod_container_status_restarts_total[{rate_interval}]))'
        else:
            return f'sum(rate(kube_pod_container_status_restarts_total{{namespace="{namespace}"}}[{rate_interval}]))'
    
    elif metric_type == "pods_general":
        if is_fleet_wide:
            return 'sum(kube_pod_status_phase{phase="Running"})'
        else:
            return f'sum(kube_pod_status_phase{{phase="Running", namespace="{namespace}"}})'
    
    elif metric_type == "deployments":
        if is_fleet_wide:
            return 'sum(kube_deployment_status_replicas_ready)'
        else:
            return f'sum(kube_deployment_status_replicas_ready{{namespace="{namespace}"}})'
    
    elif metric_type == "services":
        if is_fleet_wide:
            return 'sum(kube_service_info)'
        else:
            return f'sum(kube_service_info{{namespace="{namespace}"}})'
    
    elif metric_type == "cpu":
        if is_fleet_wide:
            return f'sum(rate(container_cpu_usage_seconds_total[{rate_interval}]))'
        else:
            return f'sum(rate(container_cpu_usage_seconds_total{{namespace="{namespace}"}}[{rate_interval}]))'
    
    elif metric_type == "memory":
        if is_fleet_wide:
            return 'sum(container_memory_usage_bytes)'
        else:
            return f'sum(container_memory_usage_bytes{{namespace="{namespace}"}})'
    
    # Alert Metrics
    elif metric_type == "alerts_firing":
        if is_fleet_wide:
            return 'ALERTS{alertstate="firing"}'
        else:
            return f'ALERTS{{namespace="{namespace}", alertstate="firing"}}'
    
    elif metric_type == "alerts_critical":
        if is_fleet_wide:
            return 'ALERTS{severity="critical"}'
        else:
            return f'ALERTS{{namespace="{namespace}", severity="critical"}}'
    
    elif metric_type == "alerts_general":
        if is_fleet_wide:
            return 'ALERTS'
        else:
            return f'ALERTS{{namespace="{namespace}"}}'
    
    else:
        return None


def find_primary_promql_for_question(question: str, promql_queries: List[str]) -> str:
    """
    Find the most relevant PromQL query to display - prioritize non-ALERTS queries
    """
    if not promql_queries:
        return "No PromQL generated"
    
    # Simple approach: return the first non-ALERTS query
    # If only ALERTS, return ALERTS
    for query in promql_queries:
        if "ALERTS" not in query:
            return query
    
    # If we only have ALERTS queries, return the first one
    return promql_queries[0]


def query_thanos_with_promql(promql_queries: List[str], start_ts: int, end_ts: int) -> Dict[str, Any]:
    """
    Step 3: Execute PromQL queries against Thanos
    """
    results = {}
    headers = {"Authorization": f"Bearer {THANOS_TOKEN}"}
    
    for promql in promql_queries:
        try:
            print(f"🔍 Executing PromQL: {promql}")
            
            # Use instant query for ALERTS since they represent current state
            if "ALERTS" in promql:
                response = requests.get(
                    f"{PROMETHEUS_URL}/api/v1/query",
                    headers=headers,
                    params={"query": promql},
                    verify=verify,
                    timeout=30
                )
                result = response.json()["data"]["result"]
                
                # Convert instant query result to time series format for consistency
                data_points = []
                for series in result:
                    # For ALERTS, we only have the current value
                    timestamp = end_ts  # Use end timestamp
                    value = float(series["value"][1]) if "value" in series else 1.0
                    data_points.append({
                        "timestamp": datetime.fromtimestamp(float(timestamp)),
                        "value": value,
                        "labels": series["metric"]
                    })
            else:
                # Use range query for regular metrics
                response = requests.get(
                    f"{PROMETHEUS_URL}/api/v1/query_range",
                    headers=headers,
                    params={
                        "query": promql,
                        "start": start_ts,
                        "end": end_ts,
                        "step": "30s"
                    },
                    verify=verify,
                    timeout=30
                )
                result = response.json()["data"]["result"]
                
                # Process data points for range queries
                data_points = []
                for series in result:
                    for timestamp, value in series["values"]:
                        try:
                            data_points.append({
                                "timestamp": datetime.fromtimestamp(float(timestamp)),
                                "value": float(value),
                                "labels": series["metric"]
                            })
                        except (ValueError, TypeError):
                            continue
            response.raise_for_status()
            
            # Generate summary stats
            if data_points:
                values = [dp["value"] for dp in data_points]
                key = get_metric_key(promql)
                results[key] = {
                    "query": promql,
                    "data_points_count": len(data_points),
                    "latest_value": values[-1] if values else 0,
                    "average_value": sum(values) / len(values) if values else 0,
                    "min_value": min(values) if values else 0,
                    "max_value": max(values) if values else 0,
                    "raw_data": data_points[:10]  # First 10 for reference
                }
            else:
                key = get_metric_key(promql)
                results[key] = {
                    "query": promql,
                    "data_points_count": 0,
                    "latest_value": 0,
                    "average_value": 0,
                    "min_value": 0,
                    "max_value": 0,
                    "raw_data": []
                }
            
            print(f"✅ Query returned {len(data_points)} data points")
            
        except Exception as e:
            print(f"❌ Query failed: {e}")
            key = get_metric_key(promql)
            results[key] = {
                "query": promql,
                "error": str(e),
                "data_points_count": 0,
                "latest_value": 0,
                "average_value": 0
            }
    
    return results


def get_metric_key(promql: str) -> str:
    """Helper to generate readable key from PromQL - COMPREHENSIVE COVERAGE"""
    
    # 🚨 ALERTS
    if "ALERTS" in promql:
        if "alertstate" in promql and "firing" in promql:
            return "Alerts Firing"
        elif "severity" in promql and "critical" in promql:
            return "Critical Alerts"
        elif "severity" in promql and "warning" in promql:
            return "Warning Alerts"
        elif "alertstate" in promql and "pending" in promql:
            return "Pending Alerts"
        else:
            return "Alerts"
    
    # 🤖 vLLM METRICS
    elif "vllm:" in promql:
        if "num_requests_running" in promql:
            return "vLLM Requests Running"
        elif "request_prompt_tokens" in promql:
            return "vLLM Prompt Tokens Rate"
        elif "request_generation_tokens" in promql:
            return "vLLM Generation Tokens Rate"
        elif "e2e_request_latency" in promql:
            return "vLLM P95 Latency"
        elif "avg_generation_throughput" in promql:
            return "vLLM Generation Throughput"
        elif "request_inference_time" in promql:
            return "vLLM Inference Time"
        elif "gpu_cache_usage_perc" in promql:
            return "vLLM GPU Cache Usage"
        else:
            return "vLLM Metric"
    
    # 🖥️ GPU/DCGM METRICS
    elif "DCGM_FI_DEV" in promql:
        if "GPU_TEMP" in promql:
            return "GPU Temperature"
        elif "POWER_USAGE" in promql:
            return "GPU Power Usage"
        elif "GPU_UTIL" in promql:
            return "GPU Utilization"
        elif "FB_USED" in promql:
            return "GPU Memory Usage"
        elif "TOTAL_ENERGY_CONSUMPTION" in promql:
            return "GPU Energy Consumption"
        elif "MEMORY_TEMP" in promql:
            return "GPU Memory Temperature"
        elif "SM_CLOCK" in promql:
            return "GPU SM Clock"
        elif "MEM_CLOCK" in promql:
            return "GPU Memory Clock"
        else:
            return "GPU Metric"
    
    # ☸️ KUBERNETES/OPENSHIFT METRICS
    elif "kube_" in promql:
        # Pod metrics
        if "pod_status_phase" in promql:
            if "Running" in promql:
                return "Pods Running"
            elif "Failed" in promql:
                return "Pods Failed"
            elif "Pending" in promql:
                return "Pods Pending"
            else:
                return "Pod Status"
        elif "pod_container_status_restarts" in promql:
            return "Pod Restart Rate"
        
        # Deployment metrics
        elif "deployment_status_replicas_ready" in promql:
            return "Deployments Ready"
        elif "deployment_status_replicas_available" in promql:
            return "Deployments Available"
        
        # Service metrics
        elif "service_info" in promql:
            return "Services"
        elif "endpoint_address_available" in promql:
            return "Service Endpoints"
        
        # Storage metrics
        elif "persistentvolume_info" in promql:
            return "Persistent Volumes"
        elif "persistentvolumeclaim_info" in promql:
            return "PV Claims"
        elif "persistentvolume_capacity_bytes" in promql:
            return "Storage Capacity"
        
        # Config metrics
        elif "configmap_info" in promql:
            return "ConfigMaps"
        elif "secret_info" in promql:
            return "Secrets"
        
        # Workload metrics
        elif "job_status_active" in promql:
            return "Active Jobs"
        elif "cronjob_info" in promql:
            return "CronJobs"
        elif "daemonset_status_number_ready" in promql:
            return "DaemonSets Ready"
        elif "statefulset_status_replicas_ready" in promql:
            return "StatefulSets Ready"
        
        # Network metrics
        elif "ingress_info" in promql:
            return "Ingress Rules"
        elif "networkpolicy" in promql:
            return "Network Policies"
        
        # Node metrics
        elif "node_info" in promql:
            return "Cluster Nodes"
        
        else:
            return "Kubernetes Metric"
    
    # 🌐 CONTAINER METRICS
    elif "container_" in promql:
        if "cpu_usage_seconds_total" in promql:
            return "Container CPU Usage"
        elif "memory_usage_bytes" in promql:
            return "Container Memory Usage"
        elif "network_receive_bytes_total" in promql:
            return "Network Receive Rate"
        elif "network_transmit_bytes_total" in promql:
            return "Network Transmit Rate"
        elif "network_receive_errors_total" in promql or "network_transmit_errors_total" in promql:
            return "Network Errors"
        elif "fs_reads_total" in promql or "fs_writes_total" in promql:
            return "Storage I/O Rate"
        elif "cpu_cfs_throttled_seconds_total" in promql:
            return "CPU Throttling"
        elif "memory_failcnt" in promql:
            return "Memory Failures"
        elif "oom_events_total" in promql:
            return "OOM Events"
        else:
            return "Container Metric"
    
    # 🌐 HTTP/APPLICATION METRICS
    elif "http_requests_total" in promql:
        if "5.." in promql:
            return "HTTP Error Rate"
        else:
            return "HTTP Request Rate"
    elif "http_request_duration_seconds" in promql:
        return "HTTP P95 Latency"
    
    # 📊 NODE METRICS
    elif "node_" in promql:
        if "cpu_seconds_total" in promql and "idle" in promql:
            return "Cluster CPU Usage"
        elif "memory_MemAvailable_bytes" in promql or "memory_MemTotal_bytes" in promql:
            return "Cluster Memory Usage"
        else:
            return "Node Metric"
    
    # 🔧 GENERIC METRICS
    elif "up" == promql.strip():
        return "Services Available"
    elif "nginx_ingress_controller_requests" in promql:
        return "Ingress Request Rate"
    elif "haproxy_server_up" in promql:
        return "Load Balancer Backends"
    
    else:
        # Extract metric name from PromQL as fallback
        return promql.split("{")[0].split("(")[-1] if "{" in promql else promql


def analyze_unknown_alert_with_llm(alert_name: str, namespace: str) -> str:
    """
    Use intelligent analysis for unknown alerts based on naming patterns
    """
    severity = "🔴 WARNING"  # Default to warning
    
    # Simple heuristics based on alert name
    if any(word in alert_name.lower() for word in ["down", "failed", "error", "critical"]):
        severity = "🔴 CRITICAL"
    elif any(word in alert_name.lower() for word in ["high", "slow", "latency", "pending"]):
        severity = "🟡 WARNING"
    elif any(word in alert_name.lower() for word in ["info", "recommendation", "deprecated"]):
        severity = "🟡 INFO"
    
    analysis = f"### {severity} {alert_name}\n"
    
    # Provide intelligent analysis based on naming patterns
    if "api" in alert_name.lower():
        analysis += "**What it means:** API-related issue that may affect cluster functionality\n"
        analysis += "**Investigation:** Check API server logs and endpoint availability\n"
        analysis += "**Action required:** Verify API server health and network connectivity\n"
        analysis += "**Troubleshooting commands:**\n"
        analysis += "```\noc get apiserver\noc logs -n openshift-kube-apiserver apiserver-xxx\n```"
    elif "node" in alert_name.lower() or "kubelet" in alert_name.lower():
        analysis += "**What it means:** Worker node or kubelet issue affecting workload scheduling\n"
        analysis += "**Investigation:** Check node status and kubelet logs\n"
        analysis += "**Action required:** Investigate node health and resource availability\n"
        analysis += "**Troubleshooting commands:**\n"
        analysis += "```\noc get nodes\noc describe node <node-name>\n```"
    elif "pod" in alert_name.lower() or "container" in alert_name.lower():
        analysis += "**What it means:** Pod or container issue affecting application workloads\n"
        analysis += "**Investigation:** Check pod status and logs\n"
        analysis += "**Action required:** Investigate application health and resource constraints\n"
        analysis += "**Troubleshooting commands:**\n"
        if namespace:
            analysis += f"```\noc get pods -n {namespace}\noc logs -n {namespace} <pod-name>\n```"
        else:
            analysis += "```\noc get pods -A\noc logs <pod-name> -n <namespace>\n```"
    else:
        # Generic analysis for completely unknown alerts
        analysis += f"**What it means:** Alert '{alert_name}' requires investigation\n"
        analysis += "**Investigation:** Review alert definition and current cluster state\n"
        analysis += "**Action required:** Check related OpenShift components and logs\n"
        analysis += "**Troubleshooting commands:**\n"
        analysis += f"```\noc get prometheusrule -A | grep -i {alert_name.lower()}\n```"
    
    return analysis


def generate_alert_analysis(alert_names: List[str], namespace: str) -> str:
    """
    Generate detailed, actionable analysis for SRE and MLOps teams
    """
    analysis_parts = []
    
    # Alert knowledge base with detailed troubleshooting
    alert_kb = {
        "VLLMDummyServiceInfo": {
            "severity": "🟡 INFO",
            "meaning": "Test alert for vLLM service monitoring - indicates the model is processing requests",
            "investigation": "Check vLLM service logs and request metrics",
            "action": "This is typically a test alert. Verify if this should be disabled in production.",
            "commands": [
                f"oc logs -n {namespace} -l app=llama-3-2-3b-instruct",
                f"oc get pods -n {namespace} -l app=llama-3-2-3b-instruct"
            ]
        },
        "GPUOperatorNodeDeploymentDriverFailed": {
            "severity": "🔴 WARNING", 
            "meaning": "NVIDIA GPU driver deployment failed on worker nodes",
            "investigation": "Check GPU operator pods and node status for driver installation issues",
            "action": "Investigate GPU operator logs, verify node labels, check for driver compatibility issues",
            "commands": [
                "oc get nodes -l feature.node.kubernetes.io/pci-10de.present=true",
                "oc logs -n nvidia-gpu-operator -l app=gpu-operator",
                "oc get pods -n nvidia-gpu-operator"
            ]
        },
        "GPUOperatorNodeDeploymentFailed": {
            "severity": "🔴 WARNING",
            "meaning": "NVIDIA GPU operator failed to deploy components on nodes", 
            "investigation": "Check GPU operator deployment status and node compatibility",
            "action": "Review GPU operator configuration, verify node selectors, check resource constraints",
            "commands": [
                "oc describe clusterpolicy gpu-cluster-policy",
                "oc get nodes --show-labels | grep nvidia",
                "oc logs -n nvidia-gpu-operator deployment/gpu-operator"
            ]
        },
        "GPUOperatorReconciliationFailed": {
            "severity": "🔴 WARNING",
            "meaning": "GPU operator failed to reconcile desired state with actual cluster state",
            "investigation": "Check GPU operator controller logs for reconciliation errors",
            "action": "Restart GPU operator, verify CRD status, check for resource conflicts",
            "commands": [
                "oc get clusterpolicy -o yaml",
                "oc logs -n nvidia-gpu-operator -l control-plane=controller-manager",
                "oc delete pods -n nvidia-gpu-operator -l app=gpu-operator"
            ]
        },
        "ClusterMonitoringOperatorDeprecatedConfig": {
            "severity": "🟡 INFO",
            "meaning": "Cluster monitoring is using deprecated configuration options",
            "investigation": "Review cluster-monitoring-config ConfigMap for deprecated fields",
            "action": "Update monitoring configuration to use current API versions before next upgrade",
            "commands": [
                "oc get configmap cluster-monitoring-config -n openshift-monitoring -o yaml",
                "oc get clusterversion"
            ]
        },
        "ClusterNotUpgradeable": {
            "severity": "🟡 INFO", 
            "meaning": "Cluster has conditions preventing upgrade (usually due to deprecated APIs)",
            "investigation": "Check cluster version status for upgrade blocking conditions",
            "action": "Review upgrade blockers, update deprecated API usage, resolve blocking conditions",
            "commands": [
                "oc get clusterversion -o yaml",
                "oc adm upgrade",
                "oc get clusteroperators"
            ]
        },
        "InsightsRecommendationActive": {
            "severity": "🟡 INFO",
            "meaning": "Red Hat Insights has recommendations for cluster optimization",
            "investigation": "Review insights recommendations in OpenShift console or Red Hat Hybrid Cloud Console",
            "action": "Follow insights recommendations to improve cluster security, performance, or reliability",
            "commands": [
                "oc logs -n openshift-insights deployment/insights-operator",
                "echo 'Visit: https://console.redhat.com/openshift/insights/advisor/'"
            ]
        }
    }
    
    analysis_parts.append(f"## Alert Summary: {len(alert_names)} Active Alert(s)")
    analysis_parts.append("")
    
    for alert_name in alert_names:
        if alert_name in alert_kb:
            alert = alert_kb[alert_name]
            analysis_parts.append(f"### {alert['severity']} {alert_name}")
            analysis_parts.append(f"**Issue:** {alert['meaning']}")
            analysis_parts.append(f"**Action:** {alert['action']}")
            analysis_parts.append("**Commands:**")
            for cmd in alert['commands']:
                analysis_parts.append(f"```\n{cmd}\n```")
            analysis_parts.append("")
        else:
            # Use LLM to analyze unknown alerts
            llm_analysis = analyze_unknown_alert_with_llm(alert_name, namespace)
            analysis_parts.append(llm_analysis)
            analysis_parts.append("")
    
    analysis_parts.append("### Next Steps")
    analysis_parts.append("1. Run the diagnostic commands above")
    analysis_parts.append("2. Check logs and recent changes")
    analysis_parts.append("3. Document any fixes in your runbooks")
    
    return "\n".join(analysis_parts)


def extract_alert_names_from_thanos_data(thanos_data: Dict[str, Any]) -> List[str]:
    """
    Extract actual alert names from ALERTS query results
    """
    alert_names = set()  # Use set to avoid duplicates
    
    for metric_name, data in thanos_data.items():
        # Check for "Alerts" (capitalized) or "ALERTS"
        if metric_name.lower() == "alerts" and "error" not in data and "raw_data" in data:
            raw_data = data["raw_data"]
            if raw_data:
                for entry in raw_data:
                    if "labels" in entry and "alertname" in entry["labels"]:
                        alert_name = entry["labels"]["alertname"]
                        alert_names.add(alert_name)
    
    return sorted(list(alert_names))


def generate_llm_summary(question: str, thanos_data: Dict[str, Any], model_id: str, api_key: str, namespace: str) -> str:
    """
    Step 4: Send Thanos results to LLM for summary
    """
    question_lower = question.lower()
    
    # === SPECIAL HANDLING FOR ALERTS ===
    if any(word in question_lower for word in ["alert", "alerts", "firing", "warning", "critical", "yesterday", "problem", "issue"]):
        alert_names = extract_alert_names_from_thanos_data(thanos_data)
        
        if alert_names:
            scope = "fleet-wide" if namespace == "" else f"namespace '{namespace}'"
            alert_analysis = generate_alert_analysis(alert_names, namespace)
            
            return f"🚨 **ALERT ANALYSIS FOR {scope.upper()}**\n\n{alert_analysis}"
        else:
            scope = "fleet-wide" if namespace == "" else f"namespace '{namespace}'"
            return f"✅ No alerts currently firing in {scope}. All systems appear to be operating normally."
    
    # === REGULAR METRIC HANDLING ===
    # Build focused data context - prioritize the most relevant metric
    data_context = f"Namespace: {namespace}\n\n"
    data_context += "METRIC DATA:\n"
    
    # Find the most relevant metric for the question
    relevant_metrics = []
    
    for metric_name, data in thanos_data.items():
        # Skip if error
        if "error" in data:
            continue
            
        # Prioritize based on question keywords and metric content
        is_relevant = False
        if any(word in question_lower for word in ["latency", "p95", "p99", "percentile"]) and ("latency" in metric_name.lower() or "rate(vllm:e2e" in metric_name):
            is_relevant = True
        elif any(word in question_lower for word in ["vllm request", "model request", "inference"]) and "vllm:num_requests_running" in metric_name:
            is_relevant = True
        elif any(word in question_lower for word in ["token", "prompt", "generation"]) and "token" in metric_name.lower():
            is_relevant = True
        elif any(word in question_lower for word in ["gpu", "temperature", "utilization", "usage"]) and ("DCGM" in metric_name or "GPU" in metric_name):
            is_relevant = True
        elif any(word in question_lower for word in ["pod", "pods", "number of pods"]) and "kube_pod_status_phase" in metric_name:
            is_relevant = True
        elif any(word in question_lower for word in ["deployment", "deployments"]) and "deployment" in metric_name.lower():
            is_relevant = True
        elif any(word in question_lower for word in ["service", "services"]) and "service" in metric_name.lower():
            is_relevant = True
        elif any(word in question_lower for word in ["node", "nodes"]) and "node" in metric_name.lower():
            is_relevant = True
        
        if is_relevant:
            relevant_metrics.append((metric_name, data))
    
    # If no relevant metrics found, use the first non-alert metric
    if not relevant_metrics:
        for metric_name, data in thanos_data.items():
            if "error" not in data and "Alert" not in metric_name:
                relevant_metrics.append((metric_name, data))
                break
    
    # Show only the most relevant metric(s)
    for metric_name, data in relevant_metrics[:2]:  # Max 2 metrics
        data_context += f"- {metric_name}: {data['latest_value']:.2f} (avg: {data['average_value']:.2f})\n"
    
    # Create contextual LLM prompt  
    prompt = f"""{data_context}

QUESTION: {question}

Provide a concise answer that:
1. States the current metric value with units
2. Assesses the status (Normal/Warning/Critical) 
3. Gives brief operational summary

EXAMPLE: "Current pod count: 5 Running pods. This indicates healthy horizontal scaling for the vLLM inference workload, ensuring sufficient capacity for model serving requests. Status: Normal."

GUIDELINES:
- Assess against realistic operational baselines
- Answer the question only based on the metrics data
- Do not add additional notes or explainations.
- Keep response under 100 words


RESPONSE:"""
    
    # Call LLM with reasonable token limit for contextual summaries
    try:
        return summarize_with_llm(prompt, model_id, api_key, max_tokens=1500)
    except Exception as e:
        return f"Error generating summary: {e}"


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
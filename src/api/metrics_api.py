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
from core.response_validator import ResponseType
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
    get_namespaces_helper,
    analyze_openshift_metrics,
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

# Import core services for chat-metrics functionality
# Business logic has been moved to core modules to separate concerns
from core.promql_service import (
    generate_promql_from_question,
    discover_available_metrics_from_thanos,
    intelligent_metric_selection,
    select_queries_directly,
    generate_promql_from_discovered_metric
)
from core.thanos_service import (
    query_thanos_with_promql,
    find_primary_promql_for_question
)
from core.llm_summary_service import (
    generate_llm_summary,
)
from core.config import CHAT_SCOPE_FLEET_WIDE, FLEET_WIDE_DISPLAY


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
    # Sort MODEL_CONFIG to put external:false entries first
    MODEL_CONFIG = dict(sorted(MODEL_CONFIG.items(), key=lambda x: x[1].get('external', True)))
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
    return get_namespaces_helper()


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

        summary = summarize_with_llm(prompt, req.summarize_model_id, ResponseType.VLLM_ANALYSIS, req.api_key)

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
        # Log the actual error for debugging
        print(f"❌ Error in /analyze endpoint: {e}")
        import traceback
        traceback.print_exc()
        # Handle API key errors and other LLM-related errors
        raise HTTPException(
            status_code=500, detail=f"Please check your API Key or try again later: {str(e)}"
        )


@app.post("/chat")
def chat(req: ChatRequest):
    try:
        prompt = build_chat_prompt(
            user_question=req.question, metrics_summary=req.prompt_summary
        )
        # Get LLM response using helper function
        response = summarize_with_llm(prompt, req.summarize_model_id, ResponseType.GENERAL_CHAT, req.api_key, max_tokens=1500)
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
        is_fleet_wide = req.chat_scope == CHAT_SCOPE_FLEET_WIDE
        target_namespace = None if is_fleet_wide else req.namespace
        
        promql_queries = generate_promql_from_question(req.question, target_namespace, clean_model_name, start_ts, end_ts, is_fleet_wide)
        print(f"📝 Generated {len(promql_queries)} PromQL queries")
        
        # Step 3: Query Thanos with PromQL
        thanos_data = query_thanos_with_promql(promql_queries, start_ts, end_ts)
        
        # Step 4: Send results to LLM for summary
        summary_namespace = FLEET_WIDE_DISPLAY if is_fleet_wide else req.namespace
        summary = generate_llm_summary(req.question, thanos_data, req.summarize_model_id, req.api_key, summary_namespace)
        # Step 5: Return summary for UI
        # Find the most relevant PromQL for the question (not just the first one)
        primary_promql = find_primary_promql_for_question(req.question, promql_queries)
        return {
            "promql": primary_promql,
            "summary": summary,
            "chat_scope": req.chat_scope,
        }
        
    except Exception as e:
        print(f"❌ Error in chat_metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
        llm_response = summarize_with_llm(prompt, req.summarize_model_id, ResponseType.OPENSHIFT_ANALYSIS, req.api_key)
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
    return analyze_openshift_metrics(
        metric_category=req.metric_category,
        scope=req.scope,
        namespace=req.namespace,
        start_ts=req.start_ts,
        end_ts=req.end_ts,
        summarize_model_id=req.summarize_model_id,
        api_key=req.api_key,
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
# Enhanced UI for Observability Correlation PoC
# Extends the existing AI Metric Tools with correlation capabilities

import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import os
import asyncio
import aiohttp

# --- Configuration ---
API_URL = os.getenv("MCP_API_URL", "http://localhost:8000")
KORREL8R_URL = os.getenv("KORREL8R_URL", "http://localhost:8080")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
TEMPO_URL = os.getenv("TEMPO_URL", "http://localhost:3200")

# --- Page Setup ---
st.set_page_config(page_title="AI Observability PoC", layout="wide")

# Enhanced CSS styling
st.markdown("""
<style>
    html, body, [class*="css"] { 
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto; 
    }
    h1, h2, h3 { 
        font-weight: 600; 
        color: #1c1c1e; 
        letter-spacing: -0.5px; 
    }
    .stMetric { 
        border-radius: 12px; 
        background-color: #f9f9f9; 
        padding: 1em; 
        box-shadow: 0 2px 8px rgba(0,0,0,0.05); 
        color: #1c1c1e !important; 
    }
    .correlation-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 1rem;
        border-radius: 12px;
        margin: 0.5rem 0;
    }
    .trace-card {
        background: #f8f9fa;
        border-left: 4px solid #007bff;
        padding: 1rem;
        margin: 0.5rem 0;
        border-radius: 8px;
    }
    .new-feature {
        background: linear-gradient(90deg, #4CAF50, #45a049);
        color: white;
        padding: 0.2rem 0.5rem;
        border-radius: 4px;
        font-size: 0.8rem;
        margin-left: 0.5rem;
    }
    footer, header { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# --- Enhanced Navigation ---
st.sidebar.title("🔍 AI Observability PoC")
st.sidebar.markdown("*Enhanced with Korrel8r correlation*")

page = st.sidebar.radio("Navigate to:", [
    "📊 Metrics (Original)", 
    "🤖 Chat (Original)", 
    "🔍 Traces", 
    "🔄 Correlation",
    "🧠 AI Analysis"
])

# Add PoC status indicator
st.sidebar.markdown("---")
st.sidebar.markdown("### 🚀 PoC Status")

# Check service availability
@st.cache_data(ttl=60)
def check_services():
    services = {}
    try:
        resp = requests.get(f"{KORREL8R_URL}/api/v1alpha1/status", timeout=5)
        services["korrel8r"] = resp.status_code == 200
    except:
        services["korrel8r"] = False
    
    try:
        resp = requests.get(f"{API_URL}/models", timeout=5)  
        services["mcp"] = resp.status_code == 200
    except:
        services["mcp"] = False
        
    return services

services = check_services()
for service, status in services.items():
    emoji = "✅" if status else "❌"
    st.sidebar.markdown(f"{emoji} {service.title()}")

# --- Utility Functions ---
@st.cache_data(ttl=300)
def get_models():
    """Fetch available models from API"""
    try:
        res = requests.get(f"{API_URL}/models")
        return res.json()
    except Exception as e:
        st.sidebar.error(f"Error fetching models: {e}")
        return []

@st.cache_data(ttl=300)  
def get_namespaces():
    try:
        res = requests.get(f"{API_URL}/models")
        models = res.json()
        namespaces = sorted(
            list(set(model.split(" | ")[0] for model in models if " | " in model))
        )
        return namespaces
    except Exception as e:
        st.sidebar.error(f"Error fetching namespaces: {e}")
        return []

# --- Korrel8r Integration Functions ---
def query_korrel8r(start_signal, goal_type="trace", time_range="1h"):
    """Query Korrel8r for correlations"""
    try:
        payload = {
            "goals": [goal_type],
            "start": {
                "class": "metric:prometheus",
                "queries": [start_signal],
                "constraint": {
                    "limit": 100,
                    "timeout": "30s"
                }
            }
        }
        
        response = requests.post(
            f"{KORREL8R_URL}/api/v1alpha1/graphs/goals",
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"Korrel8r query failed: {response.status_code}")
            return None
            
    except Exception as e:
        st.error(f"Error querying Korrel8r: {e}")
        return None

def get_prometheus_metrics(query, time_range="1h"):
    """Fetch metrics from Prometheus"""
    try:
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=1 if time_range == "1h" else 24)
        
        params = {
            "query": query,
            "start": start_time.isoformat() + "Z",
            "end": end_time.isoformat() + "Z", 
            "step": "30s"
        }
        
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query_range",
            params=params,
            timeout=30
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"Prometheus query failed: {response.status_code}")
            return None
            
    except Exception as e:
        st.error(f"Error querying Prometheus: {e}")
        return None

def get_tempo_traces(service_name, time_range="1h"):
    """Fetch traces from Tempo"""
    try:
        # This is a simplified example - actual Tempo API calls would be more complex
        response = requests.get(
            f"{TEMPO_URL}/api/search",
            params={
                "service.name": service_name,
                "limit": 20
            },
            timeout=30
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"Tempo query failed: {response.status_code}")
            return None
            
    except Exception as e:
        st.error(f"Error querying Tempo: {e}")
        return None

# --- Page Content ---
if page == "📊 Metrics (Original)":
    st.title("📊 AI Metric Summarizer")
    st.markdown("*Original functionality - analyze vLLM metrics with AI*")
    
    # Import and render original metrics functionality
    # This would include your existing metrics analysis code
    st.info("🔄 Your existing metrics analysis functionality goes here")
    st.markdown("This preserves all your current AI-powered metrics analysis capabilities.")

elif page == "🤖 Chat (Original)":
    st.title("🤖 Chat with Prometheus") 
    st.markdown("*Original functionality - natural language queries*")
    
    # Import and render original chat functionality
    st.info("🔄 Your existing chat functionality goes here")
    st.markdown("This preserves your current natural language Prometheus querying.")

elif page == "🔍 Traces":
    st.title("🔍 Distributed Traces")
    st.markdown('<span class="new-feature">NEW</span>', unsafe_allow_html=True)
    
    with st.sidebar:
        st.markdown("### Trace Filters")
        namespace = st.selectbox("Namespace", get_namespaces() or ["default"])
        time_range = st.selectbox("Time Range", ["1h", "6h", "24h"])
        service_filter = st.text_input("Service Name Pattern", placeholder="my-service.*")
    
    if st.button("🔍 Search Traces"):
        with st.spinner("Fetching traces..."):
            traces = get_tempo_traces(service_filter or ".*", time_range)
            
            if traces:
                st.success(f"Found {len(traces.get('traces', []))} traces")
                
                for trace in traces.get('traces', [])[:10]:  # Show first 10
                    with st.expander(f"Trace {trace.get('traceID', 'Unknown')[:16]}"):
                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("Duration", f"{trace.get('duration', 0)/1000:.2f}ms")
                        with col2:
                            st.metric("Spans", trace.get('spanCount', 0))
                        with col3:
                            st.metric("Service", trace.get('rootServiceName', 'Unknown'))
                        
                        # Correlation button
                        if st.button(f"🔄 Find Related Metrics", key=f"corr_{trace.get('traceID', '')}"):
                            correlations = query_korrel8r(
                                f"trace:{{.trace_id=\"{trace.get('traceID')}\"}}", 
                                "metric"
                            )
                            if correlations:
                                st.success("Found correlations!")
                                st.json(correlations)

elif page == "🔄 Correlation":
    st.title("🔄 Cross-Signal Correlation")
    st.markdown('<span class="new-feature">NEW</span> *Powered by Korrel8r*', unsafe_allow_html=True)
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### 📈 Start with Metrics")
        metric_query = st.text_input(
            "Prometheus Query", 
            placeholder="up{job='my-service'}"
        )
        
        if st.button("🔍 Find Related Traces"):
            if metric_query:
                with st.spinner("Correlating signals..."):
                    correlations = query_korrel8r(f"metric:{metric_query}", "trace")
                    
                    if correlations:
                        st.markdown('<div class="correlation-card">', unsafe_allow_html=True)
                        st.markdown("### 🎯 Correlation Results")
                        st.markdown(f"Found {len(correlations.get('results', []))} correlations")
                        st.markdown('</div>', unsafe_allow_html=True)
                        
                        for result in correlations.get('results', []):
                            st.markdown('<div class="trace-card">', unsafe_allow_html=True)
                            st.markdown(f"**Trace ID:** `{result.get('trace_id', 'Unknown')}`")
                            st.markdown(f"**Service:** {result.get('service_name', 'Unknown')}")
                            st.markdown(f"**Correlation Rule:** {result.get('rule', 'Unknown')}")
                            st.markdown('</div>', unsafe_allow_html=True)
    
    with col2:
        st.markdown("### 🔍 Start with Traces") 
        trace_id = st.text_input(
            "Trace ID",
            placeholder="abc123def456..."
        )
        
        if st.button("📊 Find Related Metrics"):
            if trace_id:
                with st.spinner("Correlating signals..."):
                    correlations = query_korrel8r(f"trace:{{.trace_id=\"{trace_id}\"}}", "metric")
                    
                    if correlations:
                        st.markdown('<div class="correlation-card">', unsafe_allow_html=True)
                        st.markdown("### 🎯 Correlation Results")
                        st.markdown(f"Found {len(correlations.get('results', []))} correlations")
                        st.markdown('</div>', unsafe_allow_html=True)
                        
                        for result in correlations.get('results', []):
                            st.markdown('<div class="trace-card">', unsafe_allow_html=True)
                            st.markdown(f"**Metric:** `{result.get('metric_name', 'Unknown')}`")
                            st.markdown(f"**Labels:** {result.get('labels', {})}")
                            st.markdown(f"**Correlation Rule:** {result.get('rule', 'Unknown')}")
                            st.markdown('</div>', unsafe_allow_html=True)

elif page == "🧠 AI Analysis":
    st.title("🧠 AI-Powered Correlation Analysis") 
    st.markdown('<span class="new-feature">NEW</span> *LLM + Korrel8r Integration*', unsafe_allow_html=True)
    
    st.markdown("### 🎯 Incident Analysis")
    incident_description = st.text_area(
        "Describe the issue you're investigating:",
        placeholder="High CPU usage in my-service around 14:30..."
    )
    
    if st.button("🔍 Analyze Incident"):
        if incident_description:
            with st.spinner("🤖 AI is analyzing correlations..."):
                # This would integrate with your existing LLM interface
                # For now, show a mock analysis
                st.markdown("### 🎯 Analysis Results")
                
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("#### 📊 Related Metrics")
                    st.info("• CPU usage spike: 85% → 15%")
                    st.info("• Request latency: +240ms")  
                    st.info("• Error rate: 0.1% → 2.3%")
                
                with col2:
                    st.markdown("#### 🔍 Related Traces")
                    st.info("• 12 slow traces identified")
                    st.info("• Database connection timeouts")
                    st.info("• Memory allocation errors")
                
                st.markdown("#### 🧠 AI Insights")
                st.success("""
                **Root Cause Analysis:**
                Based on the correlation between metrics and traces, the incident appears to be caused by:
                1. Database connection pool exhaustion leading to timeouts
                2. Memory pressure causing GC pauses
                3. Cascading effect on downstream services
                
                **Recommendations:**
                - Scale database connection pool
                - Increase memory allocation for the service
                - Add circuit breaker for resilience
                """)

# --- Footer ---
st.markdown("---")
st.markdown("🚀 **PoC Status**: This demonstrates correlation capabilities between Prometheus metrics and Tempo traces using Korrel8r")
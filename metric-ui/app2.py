import streamlit as st
import pandas as pd
import requests
from datetime import datetime
import matplotlib.pyplot as plt
import os
import json

# --- Config from ENV ---
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL")
LLM_URL = os.getenv("LLM_URL")
LLM_API_TOKEN = os.getenv("LLM_API_TOKEN", "")
LLM_MODEL_SUMMARIZATION = "meta-llama/Llama-3.2-3B-Instruct"  # Fixed model
model_list_json = os.getenv("LLM_MODELS", '["Unknown"]')
model_names = json.loads(model_list_json)

# --- Metrics definitions ---
ALL_METRICS = {
    "Prompt Tokens Created": "vllm:prompt_tokens_created",
    "P95 Latency (s)": "vllm:e2e_request_latency_seconds_count",
    "Requests Running": "vllm:num_requests_running",
    "GPU Usage (%)": "vllm:gpu_cache_usage_perc",
    "Output Tokens Created": "vllm:request_generation_tokens_created",
    "Inference Time (s)": "vllm:request_inference_time_seconds_count"
}
DASHBOARD_METRICS = ["GPU Usage (%)", "P95 Latency (s)"]

# --- Session Init ---
if "analyzed" not in st.session_state:
    st.session_state.analyzed = False
if "prompt" not in st.session_state:
    st.session_state.prompt = ""
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "last_user_input" not in st.session_state:
    st.session_state.last_user_input = ""

# --- Utilities ---
def fetch_metrics(query):
    response = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, verify=False)
    response.raise_for_status()
    result = response.json()["data"]["result"]
    rows = []
    for r in result:
        metric = r["metric"]
        value = float(r["value"][1])
        timestamp = datetime.fromtimestamp(float(r["value"][0]))
        metric["value"] = value
        metric["timestamp"] = timestamp
        rows.append(metric)
    return pd.DataFrame(rows)

def build_prompt(metric_dfs, model_name):
    prompt = f"""You're an observability expert analyzing AI model performance.\nModel: `{model_name}`\n\nLatest metrics:\n"""
    for label, df in metric_dfs.items():
        if df.empty:
            prompt += f"- {label}: No data\n"
        else:
            prompt += f"- {label}: Avg={df['value'].mean():.2f}, Max={df['value'].max():.2f}, Count={len(df)}\n"
    prompt = f"""
    You are an MLOps expert analyzing performance metrics for the AI model `{model}`.

    Below is the latest summary of key metrics:
    {st.session_state.prompt}

    Your task:
    1. Highlight what is going well.
    2. Identify potential problems or anomalies.
    3. Recommend next steps or improvements.

    Please write your analysis in clear bullet points.
    """

    return prompt

def summarize_with_llm(prompt):
    headers = {
        "Content-Type": "application/json"
    }
    if LLM_API_TOKEN:
        headers["Authorization"] = f"Bearer {LLM_API_TOKEN}"

    # Fetch available models from LLM_URL
    try:
        model_response = requests.get(f"{LLM_URL}/v1/models", headers=headers, verify=False)
        model_response.raise_for_status()
        model_data = model_response.json()
        model_id = model_data["data"][0]["id"]
    except Exception as e:
        raise RuntimeError(f"Failed to fetch summarization model ID from {LLM_URL}: {e}")

    # Prepare payload
    payload = {
        "model": model_id,
        "prompt": prompt,
        "temperature": 0.5,
        "max_tokens": 300
    }

    # Send summarization request
    response = requests.post(f"{LLM_URL}/v1/completions", headers=headers, json=payload, verify=False)
    response.raise_for_status()
    return response.json()["choices"][0]["text"].strip()

# --- Layout Config ---
st.set_page_config(page_title="AI Model Metrics Dashboard", page_icon="📊", layout="wide")

# --- Logo + Title Styling ---
st.markdown("""
<div style="display: flex; align-items: center; gap: 10px;">
    <span style="font-size: 2.2em;">📈</span>
    <h1 style="margin-bottom: 0;">AI Model Metrics Summarizer</h1>
</div>
""", unsafe_allow_html=True)

# --- Page Toggle ---
page = st.sidebar.radio("Navigation", ["📈 Analyze Metrics", "💬 Chat with Assistant"])
model = st.sidebar.selectbox("Select AI Model", model_names)
st.session_state["selected_model"] = model

# --- Page: Analyze Metrics ---
if page == "📈 Analyze Metrics":
    if st.button("Analyze Metrics"):
        with st.spinner("Fetching and analyzing..."):
            try:
                metric_dfs = {label: fetch_metrics(query) for label, query in ALL_METRICS.items()}
                summary_prompt = build_prompt(metric_dfs, model)
                summary = summarize_with_llm(summary_prompt)

                st.session_state.analyzed = True
                st.session_state.prompt = summary_prompt

                col1, col2 = st.columns([1.5, 2])
                with col1:
                    st.subheader("📋 Summary")
                    st.markdown(f"**Model:** `{model}`")
                    st.markdown(summary)

                with col2:
                    st.subheader("📈 GPU & Latency Dashboard")
                    for label, df in metric_dfs.items():
                        if not df.empty:
                            st.metric(label, f"{df['value'].mean():.2f}", delta=f"Max: {df['value'].max():.2f}")
                    avg_data = {
                        label: metric_dfs[label]["value"].mean()
                        for label in DASHBOARD_METRICS if not metric_dfs[label].empty
                    }
                    if avg_data:
                        pie_df = pd.Series(avg_data)
                        fig, ax = plt.subplots()
                        pie_df.plot.pie(autopct="%.1f%%", ax=ax, title="GPU vs Latency (Avg)", ylabel="")
                        st.pyplot(fig)

            except Exception as e:
                st.error(f"❌ Error: {e}")

# --- Page: Chat Interface ---
elif page == "💬 Chat with Assistant":
    st.subheader("💬 Ask Questions About Your Metrics")
    user_input = st.chat_input("Ask something like 'what's going right?'")

    if user_input and user_input != st.session_state.last_user_input:
        st.session_state.last_user_input = user_input
        prompt = (
            f"You're a helpful MLOps assistant. Metrics for model `{model}`:\n\n{st.session_state.prompt}\n\n"
            f"User question: {user_input}"
            if st.session_state.analyzed else
            f"You're a helpful MLOps assistant. Metrics haven't been analyzed yet.\nUser question: {user_input}"
        )

        with st.spinner("Thinking..."):
            reply = summarize_with_llm(prompt)

        st.session_state.chat_history.append(("user", user_input))
        st.session_state.chat_history.append(("ai", reply))

    for role, msg in st.session_state.chat_history:
        st.chat_message(role).write(msg)

# --- Footer Branding ---
st.markdown("---")
st.caption("Built with ❤️ by your-team · Powered by Prometheus & LlamaStack")

"""
LLM Client and Prompt Building Functions

Contains all business logic for interacting with LLMs (local and external),
building prompts, and processing LLM responses.
"""

import re
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta, timezone, time
from dateparser.search import search_dates

from .config import MODEL_CONFIG, LLM_API_TOKEN, LLAMA_STACK_URL, VERIFY_SSL
from .response_validator import ResponseValidator, ResponseType

# LLM Generation Configuration Constants
DETERMINISTIC_TEMPERATURE = 0  # Zero temperature for consistent, deterministic output
DEFAULT_MAX_TOKENS = 6000  # Maximum tokens allowed
DEFAULT_SSL_VERIFICATION = True  # Enable SSL verification for external API calls

# Time Range Configuration Constants
DEFAULT_TIME_RANGE_HOURS = 1  # Default lookback period when no time specified
DEFAULT_RATE_SYNTAX = "1h"  # Default PromQL time range syntax
FALLBACK_RATE_SYNTAX = "5m"  # Fallback PromQL time range for shorter queries


def _make_api_request(
    url: str, headers: dict, payload: dict, verify_ssl: bool = True
) -> dict:
    """Make API request with consistent error handling"""
    response = requests.post(url, headers=headers, json=payload, verify=verify_ssl)
    response.raise_for_status()
    return response.json()


def _validate_and_extract_response(
    response_json: dict, is_external: bool, provider: str = "LLM"
) -> str:
    """Validate response format and extract content"""
    if is_external:
        if provider == "google":
            # Google Gemini response format
            if "candidates" not in response_json or not response_json["candidates"]:
                raise ValueError(f"Invalid {provider} response format")

            candidate = response_json["candidates"][0]
            if "content" not in candidate or "parts" not in candidate["content"]:
                raise ValueError(f"Invalid {provider} response structure")

            parts = candidate["content"]["parts"]
            if not parts or "text" not in parts[0]:
                raise ValueError(f"Invalid {provider} response content")

            return parts[0]["text"].strip()
        else:
            # OpenAI and other providers using "choices" format
            if "choices" not in response_json or not response_json["choices"]:
                raise ValueError(f"Invalid {provider} response format")

            return response_json["choices"][0]["message"]["content"].strip()
    else:
        # Local model response format
        if "choices" not in response_json or not response_json["choices"]:
            raise ValueError(f"Invalid {provider} response format")
        return response_json["choices"][0]["text"].strip()


def _clean_llm_summary_string(text: str) -> str:
    """Remove non-printable ASCII characters and normalize whitespace"""
    # Remove any non-printable ASCII characters (except common whitespace like space, tab, newline)
    cleaned_text = re.sub(r"[^\x20-\x7E\n\t]", "", text)
    # Replace multiple spaces/newlines/tabs with single spaces, then strip leading/trailing whitespace
    return re.sub(r"\s+", " ", cleaned_text).strip()


def summarize_with_llm(
    prompt: str,
    summarize_model_id: str,
    response_type: ResponseType,
    api_key: Optional[str] = None,
    messages: Optional[List[Dict[str, str]]] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    enable_validation: bool = True,
) -> str:
    """
    Summarize content using an LLM (local or external).
    
    Args:
        prompt: The content to summarize
        summarize_model_id: Model identifier from MODEL_CONFIG
        response_type: Expected response type for validation (OPENSHIFT, VLLM, CHAT) - required
        api_key: API key for external models (optional for local models)
        messages: Previous conversation messages (optional)
        max_tokens: Maximum number of tokens to generate (default: 6000)
        enable_validation: Whether to enable response validation and cleanup (default: True)

    Returns:
        LLM-generated summary text (cleaned if validation enabled)
    """
    headers = {"Content-Type": "application/json"}

    # Get model configuration
    model_info = MODEL_CONFIG.get(summarize_model_id, {})
    is_external = model_info.get("external", False)

    # Building LLM messages array
    llm_messages = []
    if messages:
        llm_messages.extend(messages)
    # Ensure the new prompt is always added as the last user message
    llm_messages.append({"role": "user", "content": prompt})

    if is_external:
        # External model (like OpenAI, Anthropic, etc.)
        if not api_key:
            raise ValueError(
                f"API key required for external model {summarize_model_id}"
            )

        # Get provider-specific configuration
        provider = model_info.get("provider", "openai")
        api_url = model_info.get("apiUrl", "https://api.openai.com/v1/chat/completions")
        model_name = model_info.get("modelName")

        # Provider-specific authentication and payload
        if provider == "google":
            # Google Gemini API format
            headers["x-goog-api-key"] = api_key

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
            }
        else:
            # OpenAI and compatible APIs
            headers["Authorization"] = f"Bearer {api_key}"

            payload = {
                "model": model_name,
                "messages": llm_messages,
                "temperature": DETERMINISTIC_TEMPERATURE,  # Deterministic output
                "max_tokens": max_tokens,
            }

        response_json = _make_api_request(api_url, headers, payload, verify_ssl=DEFAULT_SSL_VERIFICATION)
        raw_response = _validate_and_extract_response(
            response_json, is_external=True, provider=provider
        )

        # For external models, no need to do response validation and cleanup
        return raw_response

    else:
        # Local model (deployed in cluster)
        if LLM_API_TOKEN:
            headers["Authorization"] = f"Bearer {LLM_API_TOKEN}"

        # Combine all messages into a single prompt
        prompt_text = ""
        if messages:
            for msg in messages:
                prompt_text += f"{msg['role']}: {msg['content']}\n"
        prompt_text += prompt  # Add the current prompt

        payload = {
            "model": summarize_model_id,
            "prompt": prompt_text,
            "temperature": DETERMINISTIC_TEMPERATURE,  # Deterministic output
            "max_tokens": max_tokens,
        }
        response_json = _make_api_request(
            f"{LLAMA_STACK_URL}/completions", headers, payload, verify_ssl=VERIFY_SSL
        )
        raw_response = _validate_and_extract_response(
            response_json, is_external=False, provider="LLM"
        )

        # Apply response validation and cleanup if enabled
        if enable_validation:
            validation_result = ResponseValidator.clean_response(raw_response, response_type, prompt)
            return validation_result['cleaned_response']
        else:
            return raw_response





def build_chat_prompt(user_question: str, metrics_summary: str) -> str:
    """Build a chat prompt combining user question with metrics context"""
    prompt = f"""
Metrics Summary:
{metrics_summary}

Question: {user_question}

Provide a concise answer that:
1. Directly answers the question using the metrics data
2. States the current status (Normal/Warning/Critical)
3. Gives brief recommendations if needed

Keep response under 100 words.
Stop after you have provided the answer and do not add additional explanations or notes.

ANSWER:
"""
    return prompt.strip()


def build_prompt(metric_dfs, model_name: str) -> str:
    """Build analysis prompt for vLLM metrics data"""
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    prompt = f"""
You are a machine learning model performance analysis expert. Please analyze the following vLLM metrics for model '{model_name}' and provide a comprehensive summary.

Current Analysis Time: {current_time}

METRICS DATA:
"""
    
    for metric_name, df in metric_dfs.items():
        if df is not None and not df.empty:
            prompt += f"\n=== {metric_name.upper()} ===\n"
            # Add DataFrame summary
            prompt += f"Data points: {len(df)}\n"
            if 'value' in df.columns:
                prompt += f"Latest value: {df['value'].iloc[-1] if len(df) > 0 else 'N/A'}\n"
                prompt += f"Average: {df['value'].mean():.2f}\n"
                prompt += f"Min: {df['value'].min():.2f}, Max: {df['value'].max():.2f}\n"
    
    prompt += """

ANALYSIS REQUIREMENTS:
1. **Performance Summary**: Overall health and performance status
2. **Key Metrics Analysis**: Interpret the most important metrics
3. **Trends and Patterns**: Identify any concerning trends
4. **Recommendations**: Actionable suggestions for optimization
5. **Attentions**: Summarize top 3 issues that are happening and need attention

In your response, do not add or ask additional questions. 
Answer each requirement above concisely as a summary in less than 150 words. 
Stop after you have answered requirement 5 and do not add explainations or notes.
Please provide a clear, structured analysis that would be useful for both technical teams and stakeholders.
"""
    
    return prompt.strip()


def build_openshift_prompt(
    metric_dfs, metric_category, namespace=None, scope_description=None
):
    """
    Build prompt for OpenShift metrics analysis
    
    Note: This function depends on describe_trend() and detect_anomalies() 
    which will be moved to core/metrics.py later.
    """
    if scope_description:
        scope = scope_description
    else:
        scope = f"namespace **{namespace}**" if namespace else "cluster-wide"

    header = f"You are an expert in OpenShift platform monitoring and operations. You are evaluating OpenShift **{metric_category}** metrics for {scope}.\n\n📊 **Metrics**:\n"
    analysis_focus = f"{metric_category.lower()} performance and health"

    lines = []
    for label, df in metric_dfs.items():
        if df.empty:
            lines.append(f"- {label}: No data")
            continue
        avg = df["value"].mean()
        latest = df["value"].iloc[-1] if not df.empty else 0
        # TODO: Import these functions from core.metrics when available
        # trend = describe_trend(df)
        # anomaly = detect_anomalies(df, label)
        trend = "stable"  # Placeholder
        anomaly = "normal"  # Placeholder
        lines.append(
            f"- {label}: Avg={avg:.2f}, Latest={latest:.2f}, Trend={trend}, {anomaly}"
        )

    analysis_questions = f"""🔍 Please analyze:
1. What's the current state of {analysis_focus}?
2. Are there any performance or reliability concerns?
3. What actions should be taken?
4. Any optimization recommendations?

Do not add or ask additional questions. Your response should only include the questions and answers for the above questions.
For each question, state the question in bold font, and then answer each question concisely and directly with maximum of 150 words.
If there is no direct answer to a question, say so and do not speculate or add additional information. 
Stop after you have answered question 4 and do not add explainations or notes.
"""
    return f"""{header}
{chr(10).join(lines)}

{analysis_questions}
""".strip()


def build_openshift_chat_prompt(
    question: str,
    metrics_context: str,
    time_range_info: Optional[Dict[str, Any]] = None,
    chat_scope: str = "namespace_specific",
    target_namespace: str = None,
    alerts_context: str = "",
) -> str:
    """Build specialized prompt for OpenShift/Kubernetes queries"""
    # Build scope context
    if chat_scope == "fleet_wide":
        scope_context = "You are analyzing **fleet-wide OpenShift/Kubernetes metrics across ALL namespaces**.\n\n"
    elif target_namespace:
        scope_context = f"You are analyzing OpenShift/Kubernetes metrics for namespace: **{target_namespace}**.\n\n"
    else:
        scope_context = "You are analyzing OpenShift/Kubernetes metrics.\n\n"
    
    # Build time range context
    time_context = ""
    time_range_syntax = FALLBACK_RATE_SYNTAX  # default
    if time_range_info:
        time_duration = time_range_info.get("duration_str", "")
        time_range_syntax = time_range_info.get("rate_syntax", FALLBACK_RATE_SYNTAX)
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

**Current Alert Status:**
{alerts_context.strip()}

{{
  "promqls": ["ALERTS"],
  "summary": "Answer to: {question}"
}}
""".strip()


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
    """
    Build flexible LLM prompt for various metric analysis scenarios
    
    Note: This function depends on get_vllm_metrics() and add_namespace_filter()
    which will be moved to core/metrics.py and core/utils.py later.
    """
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
    time_range_syntax = FALLBACK_RATE_SYNTAX  # default
    if time_range_info:
        time_duration = time_range_info.get("duration_str", "")
        time_range_syntax = time_range_info.get("rate_syntax", FALLBACK_RATE_SYNTAX)
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

    # TODO: Import get_vllm_metrics() and add_namespace_filter() from core modules when available
    # For now, use placeholder metrics list
    metrics_list = "- Placeholder metrics list (to be replaced with actual metrics from core.metrics)"

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

{{
  "promqls": ["ALERTS"],
  "summary": "Answer to: {question}"
}}
""".strip()


def extract_time_range_with_info(
    query: str, start_ts: Optional[int], end_ts: Optional[int]
) -> tuple[int, int, Dict[str, Any]]:
    """
    Enhanced time range extraction that DYNAMICALLY parses any time expression from user's question
    Supports historical queries for months/years
    """
    query_lower = query.lower()
    
    # Priority 1: DYNAMIC parsing using regex patterns for any time expression  
    time_patterns = [
        # Pattern: "past/last X minutes/hours/days/weeks/months/years"
        r"(?:past|last|previous)\s+(\d+(?:\.\d+)?)\s+(minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?)",
        # Pattern: "X minutes/hours/days/weeks/months/years ago"  
        r"(\d+(?:\.\d+)?)\s+(minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?)\s+ago",
        # Pattern: "in the past X minutes/hours/days/months/years"
        r"in\s+the\s+past\s+(\d+(?:\.\d+)?)\s+(minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?)",
        # Pattern: "over the last X minutes/hours/days/months/years"
        r"over\s+the\s+last\s+(\d+(?:\.\d+)?)\s+(minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?)",
        # Pattern: "since X months/years ago"
        r"since\s+(\d+(?:\.\d+)?)\s+(months?|years?)\s+ago",
    ]
    
    for pattern in time_patterns:
        match = re.search(pattern, query_lower)
        if match:
            number = float(match.group(1))
            unit = match.group(2)
            
            print(f"🔍 Dynamic time found: {number} {unit}")
            
            # Convert to hours
            if unit.startswith('min'):
                hours = number / 60
                if number == 1:
                    rate_syntax = "1m"
                    duration_str = "past 1 minute"
                elif number < 60:
                    rate_syntax = f"{int(number)}m"
                    duration_str = f"past {int(number)} minutes"
                else:
                    rate_syntax = f"{int(number)}m"
                    duration_str = f"past {number} minutes"
            elif unit.startswith('hour') or unit.startswith('hr'):
                hours = number
                if number == 1:
                    rate_syntax = "1h"
                    duration_str = "past 1 hour"
                else:
                    rate_syntax = f"{int(number)}h" if number == int(number) else f"{number}h"
                    duration_str = f"past {int(number) if number == int(number) else number} hours"
            elif unit.startswith('day'):
                hours = number * 24
                if number == 1:
                    rate_syntax = "1d"
                    duration_str = "past 1 day"
                else:
                    rate_syntax = f"{int(number)}d" if number == int(number) else f"{number}d"
                    duration_str = f"past {int(number) if number == int(number) else number} days"
            elif unit.startswith('week'):
                hours = number * 24 * 7
                if number == 1:
                    rate_syntax = "7d"
                    duration_str = "past 1 week"
                else:
                    days = int(number * 7)
                    rate_syntax = f"{days}d"
                    duration_str = f"past {int(number) if number == int(number) else number} weeks"
            elif unit.startswith('month'):
                hours = number * 24 * 30  # Approximate
                days = int(number * 30)
                rate_syntax = f"{days}d"
                duration_str = f"past {int(number) if number == int(number) else number} months"
            elif unit.startswith('year'):
                hours = number * 24 * 365  # Approximate
                days = int(number * 365)
                rate_syntax = f"{days}d"
                duration_str = f"past {int(number) if number == int(number) else number} years"
            
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=hours)
            
            time_range_info = {
                "duration_str": duration_str,
                "rate_syntax": rate_syntax,
                "hours": hours
            }
            
            print(f"✅ Parsed: {duration_str} → {rate_syntax}")
            return int(start_time.timestamp()), int(end_time.timestamp()), time_range_info
    
    # Priority 2: Handle special keywords and month names
    special_cases = {
        "yesterday": (24, "1d", "yesterday"),
        "today": (24, "1d", "today"), 
        "last hour": (1, "1h", "past 1 hour"),
        "past hour": (1, "1h", "past 1 hour"),
        "last day": (24, "1d", "past 1 day"),
        "last week": (168, "7d", "past 1 week"),
        "past week": (168, "7d", "past 1 week"),
        "last month": (720, "30d", "past 1 month"),
        "past month": (720, "30d", "past 1 month"),
        "last year": (8760, "365d", "past 1 year"),
        "past year": (8760, "365d", "past 1 year"),
    }
    
    # Handle specific month names (for historical queries)
    current_date = datetime.now()
    month_mapping = {
        "january": 1, "jan": 1,
        "february": 2, "feb": 2,
        "march": 3, "mar": 3,
        "april": 4, "apr": 4,
        "may": 5,
        "june": 6, "jun": 6,
        "july": 7, "jul": 7,
        "august": 8, "aug": 8,
        "september": 9, "sep": 9, "sept": 9,
        "october": 10, "oct": 10,
        "november": 11, "nov": 11,
        "december": 12, "dec": 12
    }
    
    # Check for month names in query
    for month_name, month_num in month_mapping.items():
        if month_name in query_lower:
            # Calculate time range for the entire month
            current_year = current_date.year
            target_year = current_year
            
            # If the month is in the future this year, assume previous year
            if month_num > current_date.month:
                target_year = current_year - 1
            
            # Get start and end of target month
            if month_num == 12:
                next_month = 1
                next_year = target_year + 1
            else:
                next_month = month_num + 1
                next_year = target_year
                
            month_start = datetime(target_year, month_num, 1)
            month_end = datetime(next_year, next_month, 1) - timedelta(seconds=1)
            
            # Calculate how long ago this was
            time_diff = current_date - month_end
            hours_ago = time_diff.total_seconds() / 3600
            
            time_range_info = {
                "duration_str": f"{month_name.title()} {target_year}",
                "rate_syntax": "1h",  # Use hourly resolution for month-long queries
                "hours": hours_ago,
                "is_historical_month": True
            }
            
            print(f"🗓️ Historical month query: {month_name.title()} {target_year}")
            return int(month_start.timestamp()), int(month_end.timestamp()), time_range_info
    
    for keyword, (hours, rate_syntax, duration_str) in special_cases.items():
        if keyword in query_lower:
            print(f"🔍 Special case found: {keyword} → {hours} hours")
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=hours)
            
            time_range_info = {
                "duration_str": duration_str,
                "rate_syntax": rate_syntax,
                "hours": hours
            }
            
            return int(start_time.timestamp()), int(end_time.timestamp()), time_range_info

    # Priority 2: Parse specific dates using dateparser
    found_dates = search_dates(query, settings={"PREFER_DATES_FROM": "past"})

    if found_dates:
        print("Specific date found in query, building full day range from parsed date...")

        # Take the date part from the first result given by dateparser
        target_date = found_dates[0][1].date()

        # Create "naive" datetime objects for start and end of day
        start_time_naive = datetime.combine(target_date, time.min)
        end_time_naive = datetime.combine(target_date, time.max)

        # Make the datetime objects UTC-aware ---
        start_time_utc = start_time_naive.replace(tzinfo=timezone.utc)
        end_time_utc = end_time_naive.replace(tzinfo=timezone.utc)

        time_range_info = {
                "duration_str": f"on {target_date.strftime('%Y-%m-%d')}",
                "rate_syntax": FALLBACK_RATE_SYNTAX,
                "hours": 24
            }

        return int(start_time_utc.timestamp()), int(end_time_utc.timestamp()), time_range_info

    # Priority 3: Use timestamps from the request if explicitly provided
    if start_ts and end_ts:
        print("No time in query, using provided timestamps as fallback.")
        time_range_hours = (end_ts - start_ts) / 3600
        
        # Use exact time range from timestamps
        if time_range_hours <= 1:
            duration_str = "past 1 hour"
            rate_syntax = "1h"
        elif time_range_hours < 24:
            duration_str = f"past {int(time_range_hours)} hours"
            rate_syntax = f"{int(time_range_hours)}h"
        elif time_range_hours <= 24:
            duration_str = "past 1 day"
            rate_syntax = "1d"
        elif time_range_hours < 168:
            days = int(time_range_hours / 24)
            duration_str = f"past {days} days"
            rate_syntax = f"{days}d"
        else:
            days = int(time_range_hours / 24)
            duration_str = f"past {days} days"
            rate_syntax = f"{days}d"
        
        time_range_info = {
            "duration_str": duration_str,
            "rate_syntax": rate_syntax,
            "hours": time_range_hours
        }
        
        return start_ts, end_ts, time_range_info

    # Priority 4: Fallback to a default time range (last 1 hour)
    print("No time in query or request, defaulting to the last 1 hour.")
    now = datetime.now()
    end_time = now
    start_time = end_time - timedelta(hours=DEFAULT_TIME_RANGE_HOURS)
    
    time_range_info = {
        "duration_str": "past 1 hour",
        "rate_syntax": DEFAULT_RATE_SYNTAX,  # Use default rate syntax for fallback
        "hours": DEFAULT_TIME_RANGE_HOURS
    }
    
    return int(start_time.timestamp()), int(end_time.timestamp()), time_range_info


def extract_time_range(
    query: str, start_ts: Optional[int], end_ts: Optional[int]
) -> (int, int):
    """
    Backward compatibility wrapper for extract_time_range_with_info
    """
    start_ts, end_ts, _ = extract_time_range_with_info(query, start_ts, end_ts)
    return start_ts, end_ts


def add_namespace_filter(promql: str, namespace: str) -> str:
    """
    Adds or enforces a `namespace="..."` filter in the PromQL query.
    """
    if f'namespace="{namespace}"' in promql:
        return promql  # Already included

    # If there's a label filter (e.g., `{job="vllm"}`), insert namespace
    if "{" in promql:
        return promql.replace("{", f'{{namespace="{namespace}", ', 1)
    else:
        # No label filter at all, add one
        return f'{promql}{{namespace="{namespace}"}}'


def fix_promql_syntax(promql: str, time_range_syntax: str = FALLBACK_RATE_SYNTAX) -> str:
    """
    Post-process PromQL to fix common syntax issues and ensure proper time range syntax
    """
    if not promql:
        return promql
    
    # Fix trailing commas in label selectors
    promql = re.sub(r',\s*}', '}', promql)
    promql = re.sub(r'{\s*,', '{', promql)
    
    # Fix double commas
    promql = re.sub(r',,+', ',', promql)
    
    # Fix incomplete time range brackets (like [15m without closing bracket)
    promql = re.sub(r'\[(\d+[smhd])\s*$', r'[\1]', promql)
    
    # Ensure proper time range syntax for specific metric types
    if 'latency' in promql.lower() and 'histogram_quantile' not in promql:
        # For latency metrics that should use histogram_quantile
        if 'vllm:e2e_request_latency_seconds_bucket' not in promql:
            if 'vllm:e2e_request_latency_seconds' in promql:
                promql = promql.replace(
                    'vllm:e2e_request_latency_seconds_sum',
                    f'histogram_quantile(0.95, sum(rate(vllm:e2e_request_latency_seconds_bucket[{time_range_syntax}])) by (le))'
                )
    
    # Add time range syntax to rate functions if missing
    if 'rate(' in promql and '[' not in promql:
        promql = re.sub(r'rate\(([^)]+)\)', f'rate(\\1[{time_range_syntax}])', promql)
    
    # For metrics that have time ranges but aren't in rate() functions, convert to rate()
    if '[' in promql and 'rate(' not in promql and 'histogram_quantile' not in promql:
        # Extract the metric and its labels
        pattern = r'([a-zA-Z_:][a-zA-Z0-9_:]*(?:{[^}]*})?)\[([^]]+)\]'
        match = re.search(pattern, promql)
        if match:
            metric_with_labels = match.group(1)
            time_range = match.group(2)
            # Convert to rate() function
            promql = re.sub(pattern, f'rate({metric_with_labels}[{time_range}])', promql)
    
    # Fix namespace label formatting issues
    promql = re.sub(r"namespace='([^']*)'", r'namespace="\1"', promql)
    
    # Ensure proper closing of metric queries
    if promql.endswith('[') or promql.endswith('{'):
        promql = promql.rstrip('[{')
    
    # Balance parentheses - count and add missing closing parentheses
    open_parens = promql.count('(')
    close_parens = promql.count(')')
    if open_parens > close_parens:
        promql += ')' * (open_parens - close_parens)
    
    return promql


def format_alerts_for_ui(
    promql_query: str,
    alerts_data: list,
    alert_definitions: dict = None,
    start_ts: Optional[datetime] = None,
    end_ts: Optional[datetime] = None,
) -> str:
    """
    Takes a list of alerts and formats them into a clean, structured
    markdown string suitable for the UI, including alert meanings if available.
    """
    # Format time range if available
    time_range_str = ""
    if start_ts and end_ts:
        try:
            start_str = datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M")
            end_str = datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M")
            time_range_str = f" between `{start_str}` and `{end_str}`"
        except Exception:
            time_range_str = ""

    summary_lines = [f"PromQL Query for Alerts: `{promql_query}`\n"]
    if not alerts_data:
        summary_lines.append(
            f"No relevant alerts were firing in the specified time range{time_range_str}."
        )
        return "\n".join(summary_lines)

    # Group alerts by name and count instances
    from collections import defaultdict
    alert_groups = defaultdict(lambda: {"count": 0, "severity": "unknown", "example": None})
    
    for alert in alerts_data:
        alert_name = alert.get("alertname", "UnknownAlert")
        alert_groups[alert_name]["count"] += 1
        
        # Keep the highest severity and first example
        if alert_groups[alert_name]["example"] is None:
            alert_groups[alert_name]["example"] = alert
            alert_groups[alert_name]["severity"] = alert.get("severity", "unknown")
        elif alert.get("severity") in ["critical", "warning"] and alert_groups[alert_name]["severity"] not in ["critical", "warning"]:
            alert_groups[alert_name]["severity"] = alert.get("severity", "unknown")

    # Sort by severity (critical first), then by count (highest first)
    def severity_priority(sev):
        return {"critical": 0, "warning": 1, "info": 2}.get(sev, 3)
    
    sorted_alert_items = sorted(
        alert_groups.items(), 
        key=lambda x: (severity_priority(x[1]["severity"]), -x[1]["count"])
    )

    # Limit to top 15 alert types to avoid overwhelming the LLM
    limited_alerts = sorted_alert_items[:15]
    
    summary_lines.append(f"Found {len(alerts_data)} total alerts of {len(alert_groups)} different types. Showing top {len(limited_alerts)} alert types:")

    # Create a concise summary
    for alert_name, alert_info in limited_alerts:
        count = alert_info["count"]
        severity = alert_info["severity"]
        example_alert = alert_info["example"]
        
        namespace = example_alert["labels"].get("namespace", "")
        timestamp = example_alert.get("timestamp", "")
        
        count_text = f"{count} instance{'s' if count > 1 else ''}"
        summary_lines.append(
            f"- **{alert_name}** ({count_text}): Severity **{severity}**"
            + (f", Example from: `{namespace}`" if namespace else "")
            + (f" at `{timestamp}`" if timestamp else "")
        )
    
    if len(alert_groups) > 15:
        summary_lines.append(f"... and {len(alert_groups) - 15} more alert types")

    return "\n".join(summary_lines) 
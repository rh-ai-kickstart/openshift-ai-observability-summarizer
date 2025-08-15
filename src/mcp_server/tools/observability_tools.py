"""Observability tools for OpenShift AI monitoring and analysis.

This module provides MCP tools for interacting with OpenShift AI observability data:
- list_models: Get available AI models
- list_namespaces: List monitored namespaces
"""

import json
import os
from typing import Dict, Any, List, Optional

# Import core observability services
from core.metrics import get_models_helper, get_namespaces_helper
from core.config import PROMETHEUS_URL, THANOS_TOKEN, VERIFY_SSL
import requests
from datetime import datetime


def _resp(content: str, is_error: bool = False) -> List[Dict[str, Any]]:
    """Helper to format MCP tool responses consistently."""
    return [{"type": "text", "text": content}]


def list_models() -> List[Dict[str, Any]]:
    """List all available AI models for analysis.
    
    Returns information about both local and external AI models available
    for generating observability analysis and summaries.
    
    Returns:
        List of available models with their configurations
    """
    try:
        # Use the same logic as the metrics API
        models = get_models_helper()
        
        if not models:
            return _resp("No models are currently available.")
        
        # Format the response for MCP
        model_list = [f"• {model}" for model in models]
        response = f"Available AI Models ({len(models)} total):\n\n" + "\n".join(model_list)
        return _resp(response)
        
    except Exception as e:
        return _resp(f"Error listing models: {str(e)}", is_error=True)


def list_namespaces() -> List[Dict[str, Any]]:
    """Get list of monitored Kubernetes namespaces.
    
    Retrieves all namespaces that have observability data available
    in the Prometheus/Thanos monitoring system.
    
    Returns:
        List of namespace names with monitoring status
    """
    try:
        namespaces = get_namespaces_helper()
        if not namespaces:
            return _resp("No monitored namespaces found.")
        namespace_list = "\n".join([f"• {ns}" for ns in namespaces])
        response_text = f"Monitored Namespaces ({len(namespaces)} total):\n\n{namespace_list}"
        return _resp(response_text)
    except Exception as e:
        return _resp(f"Error retrieving namespaces: {str(e)}", is_error=True)


def get_model_config() -> List[Dict[str, Any]]:
    """Get available LLM models for summarization and analysis.
    
    Uses the exact same logic as the metrics API's /model_config endpoint:
    - Reads MODEL_CONFIG from environment (JSON string)
    - Parses to dict and sorts with external:false models first
    - Returns a human-readable list formatted for MCP
    """
    try:
        model_config: Dict[str, Any] = {}
        try:
            model_config_str = os.getenv("MODEL_CONFIG", "{}")
            model_config = json.loads(model_config_str)
            # Sort to put external:false entries first (same as metrics API)
            model_config = dict(
                sorted(model_config.items(), key=lambda x: x[1].get("external", True))
            )
        except Exception as e:
            print(f"Warning: Could not parse MODEL_CONFIG: {e}")
            model_config = {}

        if not model_config:
            return _resp("No LLM models configured for summarization.")

        # Format dictionary for MCP display
        response = f"Available Model Config ({len(model_config)} total):\n\n"
        for model_name, config in model_config.items():
            response += f"• {model_name}\n"
            for key, value in config.items():
                response += f"  - {key}: {value}\n"
            response += "\n"

        return _resp(response.strip())
    except Exception as e:
        return _resp(f"Error retrieving model configuration: {str(e)}", is_error=True)

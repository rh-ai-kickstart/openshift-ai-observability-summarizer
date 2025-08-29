from unittest.mock import patch

import mcp_server.tools.observability_tools as tools


def _texts(result):
    return [part.get("text") for part in result]


@patch("mcp_server.tools.observability_tools.get_models_helper", return_value=["a", "b"])  # type: ignore[arg-type]
def test_list_models_success(_):
    out = tools.list_models()
    texts = _texts(out)
    assert any("Available AI Models" in t for t in texts)
    assert any("a" in t for t in texts)
    assert any("b" in t for t in texts)


@patch("mcp_server.tools.observability_tools.get_models_helper", return_value=[])  # type: ignore[arg-type]
def test_list_models_empty(_):
    out = tools.list_models()
    texts = _texts(out)
    assert any("No models are currently available" in t for t in texts)


@patch("mcp_server.tools.observability_tools.get_namespaces_helper", return_value=["ns1", "ns2"])  # type: ignore[arg-type]
def test_list_namespaces_success(_):
    out = tools.list_namespaces()
    texts = _texts(out)
    assert any("Monitored Namespaces" in t for t in texts)
    assert any("ns1" in t for t in texts)
    assert any("ns2" in t for t in texts)


@patch("mcp_server.tools.observability_tools.get_namespaces_helper", return_value=[])  # type: ignore[arg-type]
def test_list_namespaces_empty(_):
    out = tools.list_namespaces()
    texts = _texts(out)
    assert any("No monitored namespaces found" in t for t in texts)


@patch("os.getenv", return_value='{"m1": {"external": false}, "m2": {"external": true}}')
def test_get_model_config_success(_):
    out = tools.get_model_config()
    text = "\n".join(_texts(out))
    assert "Available Model Config" in text
    # external:false first means m1 should appear before m2
    assert text.find("m1") < text.find("m2")


@patch("os.getenv", return_value="{}")
def test_get_model_config_empty(_):
    out = tools.get_model_config()
    texts = _texts(out)
    assert any("No LLM models configured" in t for t in texts)


@patch("mcp_server.tools.observability_tools.get_vllm_metrics", return_value={"latency": "q1", "tps": "q2"})
@patch("mcp_server.tools.observability_tools.fetch_metrics", side_effect=[{"value": [1]}, {"value": [2]}])
@patch("mcp_server.tools.observability_tools.build_prompt", return_value="PROMPT")
@patch("mcp_server.tools.observability_tools.summarize_with_llm", return_value="SUMMARY")
@patch("mcp_server.tools.observability_tools.extract_time_range_with_info", return_value=(1, 2, {}))
def test_analyze_vllm_success(_, __, ___, ____, _____):
    out = tools.analyze_vllm("model", "summarizer", time_range="last 1h")
    text = "\n".join(_texts(out))
    assert "Model: model" in text
    assert "PROMPT" in text
    assert "SUMMARY" in text
    assert "Metrics Preview" in text

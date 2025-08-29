# AI Observability MCP Server

Model Context Protocol (MCP) server providing AI assistant integration for OpenShift AI observability data. Enables Claude Desktop and Cursor IDE to discover and analyze vLLM models and Kubernetes namespaces.

## 🎯 What It Does

The MCP server provides the following tools:

- **`list_models`** - Discover available vLLM models from Prometheus metrics
- **`list_namespaces`** - List monitored Kubernetes namespaces with observability data
- **`get_model_config`** - Get available LLM models for summarization and analysis
- **`analyze_vllm`** - Analyze vLLM metrics for a model and summarize with an LLM

## 📋 Prerequisites

- Development Environment: Running via `scripts/local-dev.sh`
- Prometheus Access: Port 9090 accessible (usually via port-forward)
- vLLM Models: Deployed in OpenShift with metrics enabled
- Python 3.11+: For MCP server execution

## 🚀 Quick Start

### 1. Installation

```bash
# Install the MCP server package
cd src/mcp_server
pip install -e .
```

### 2. Integration Setup (Recommended)

```bash
# Auto-configure Claude Desktop + Cursor IDE
python setup_integration.py
```

This automatically:
- ✅ Detects your project paths and virtual environment
- ✅ Configures Claude Desktop MCP integration  
- ✅ Configures Cursor IDE MCP integration
- ✅ Tests the MCP server functionality

### 3. Manual Testing

```bash
# Test the HTTP server CLI (optional)
obs-mcp-server --help

# Note: Cursor/Claude use the stdio entrypoint (obs-mcp-stdio) and do not require args
```

### MCP stdio quick run (Cursor/Claude)

Run the stdio server directly for Cursor/Claude development:

```bash

# Preferred: run the installed stdio entrypoint
uv run obs-mcp-stdio

# If the entrypoint is missing, install the package and retry
uv run python -m pip install -e src/mcp_server
uv run obs-mcp-stdio
```

## 🔧 Integration Guides

- Cursor IDE setup: `.cursor/CURSOR_INTEGRATION.md`
- Claude Desktop setup: `src/mcp_server/integrations/CLAUDE_INTEGRATION.md`

## 🎮 Usage Examples

After setup, use these queries in Claude Desktop or Cursor IDE:

### Model Discovery
- "What AI models are available?" 
- "List all vLLM models"
- "Show me the models in production namespace"

### Namespace Discovery  
- "What namespaces exist?"
- "List monitored namespaces"
- "Show me all Kubernetes namespaces with observability data"

### LLM Model Configuration
- "What models can I use for analysis?"
- "Show me the summarization models"
- "Which models are external vs internal?"

### Analyze vLLM Metrics
- "Analyze model 'test1 | llama-3.2-3b-instruct' for the last hour using summarize model 'local-llama'."
- "Analyze 'main | llama-3' between 2 hours ago and now with model 'local-llama'."

Explicit tool invocation (for MCP Inspector or advanced users):

```json
{
  "tool": "analyze_vllm",
  "args": {
    "model_name": "mynamespace | meta-llama/Llama-3.2-3B-Instruct",
    "time_range": "last 1h",
    "summarize_model_id": "meta-llama/Llama-3.2-3B-Instruct",
    "api_key": null
  }
}
```



#### Model identifiers quick guide (MCP)

- `summarize_model_id` (used to call LlamaStack or external provider):
  - Local LlamaStack: use the MODEL_CONFIG key (e.g., `"meta-llama/Llama-3.2-3B-Instruct"`).
  - External (OpenAI/Gemini): use the external key and pass `api_key`.

- `model_name` (used to select Prometheus series):
  - Use the exact `model_name` label seen in Prometheus. If metrics are namespaced, use: `"<namespace> | <model_label>"`.

Example:
```json
{
  "tool": "analyze_vllm",
  "args": {
    "model_name": "mynamespace | meta-llama/Llama-3.2-3B-Instruct",
    "time_range": "last 24h",
    "summarize_model_id": "meta-llama/Llama-3.2-3B-Instruct",
    "api_key": null
  }
}
```

Space-separated datetime example (explicit start/end):
```json
{
  "tool": "analyze_vllm",
  "args": {
    "model_name": "mynamespace | meta-llama/Llama-3.2-3B-Instruct",
    "start_datetime": "2025-08-25 14:00:00",
    "end_datetime": "2025-08-25 15:00:00",
    "summarize_model_id": "meta-llama/Llama-3.2-3B-Instruct",
    "api_key": null
  }
}
```

Date-only (full-day) via time_range:
```json
{
  "tool": "analyze_vllm",
  "args": {
    "model_name": "mynamespace | meta-llama/Llama-3.2-3B-Instruct",
    "time_range": "on 2025-08-25",
    "summarize_model_id": "meta-llama/Llama-3.2-3B-Instruct",
    "api_key": null
  }
}
```

Accepted datetime formats:
- `YYYY-MM-DDTHH:MM:SSZ` (ISO, UTC)
- `YYYY-MM-DD HH:MM:SS` (space separator, UTC assumed)

Troubleshooting tips:
- 400 from LlamaStack: ensure `LLAMA_STACK_URL` is set and use the LlamaStack model ID (the MODEL_CONFIG key above).
- "no data": use current timestamps, try without namespace, or confirm vLLM metrics are scraped and labeled as expected.

## 🔍 Available Tools

| Tool | Description | Returns |
|------|-------------|---------|
| `list_models` | Lists available vLLM models from metrics | Format: `"namespace | model_name"` |
| `list_namespaces` | Lists monitored Kubernetes namespaces | Sorted list of namespace names |
| `get_model_config` | Gets LLM models for summarization | Internal/external model configurations |
| `analyze_vllm` |  fetch metrics, build prompt, summarize | Text summary with prompt and metrics preview |

The vLLM discovery tools query Prometheus metrics using identical logic as the main metrics API. The model config tool reads environment configuration for LLM models. 

## 🔗 Integration Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│ Claude Desktop  │────│   MCP Server     │────│  Prometheus/    │
│ / Cursor IDE    │    │  (this service)  │    │  Thanos         │
└─────────────────┘    └──────────────────┘    └─────────────────┘
        │                        │                        │
        │                        │                        │
   Natural language      list_models           vLLM metrics
   queries              list_namespaces       /api/v1/series
```

## 🔧 Development

### Running Tests (mcp_server)

Use these commands from the project root to run the MCP Server unit tests:

```bash
# Run all mcp_server tests
PYTHONPATH=src pytest -q tests/mcp_server

# Run a specific test file
PYTHONPATH=src pytest -q tests/mcp_server/test_api.py

# Run a single test function
PYTHONPATH=src pytest -q tests/mcp_server/test_tools.py -k test_analyze_vllm_success
```

Notes:
- `PYTHONPATH=src` lets tests import `mcp_server` from the source tree.

Using uv (alternative):

```bash
# Install test dependencies defined in pyproject.toml
uv sync --group test

# Run all mcp_server tests
PYTHONPATH=src uv run pytest -q tests/mcp_server

# Run a specific test file
PYTHONPATH=src uv run pytest -q tests/mcp_server/test_api.py

# Run a single test function
PYTHONPATH=src uv run pytest -q tests/mcp_server/test_tools.py -k test_analyze_vllm_success
```

### Local Development with Port Forwarding

1. **Start development environment:**
   ```bash
   # In project root
   scripts/local-dev.sh
   ```

2. **In another terminal, run MCP server:**
   ```bash
   cd src/mcp_server
   obs-mcp-server --local
   ```

### Run the MCP Server locally (HTTP, SSE, or STDIO)

The MCP server can run over standard HTTP, Server-Sent Events (SSE), or STDIO for local development and integration testing.

#### 1) Prerequisites

```bash
# From project root
uv sync --group dev --group test

# If using the Python MCP client, install its async backend once
uv add trio
# or install full client extras
uv add 'mcp[client]'
```

#### 2) Start the server

- HTTP (default):
```bash
uv run -m mcp_server.main
# Health check
curl -s http://0.0.0.0:8085/health
```

- SSE transport:
```bash
MCP_TRANSPORT_PROTOCOL=sse uv run -m mcp_server.main
# Health check (transport_protocol should be sse)
curl -s http://0.0.0.0:8085/health
```

- Optional: sample model config for tools
```bash
MODEL_CONFIG='{"local-llama":{"external":false,"modelName":"llama"}}' uv run -m mcp_server.main
```

#### 3) Connect a client

- Python MCP client (SSE):
```bash
# Start the server with SSE as above, then in another shell
uv run -m mcp.client http://localhost:8085/sse
```

- Node MCP Inspector (UI):
```bash
# HTTP
npx @modelcontextprotocol/inspector http http://localhost:8085/mcp
# SSE
npx @modelcontextprotocol/inspector sse http://localhost:8085/sse
```

#### 4) STDIO mode (no network)

```bash
# Run server over stdio
uv run -m mcp_server.stdio_server

# Connect via Python client by spawning the server (in another shell)
python -m mcp.client python -m mcp_server.stdio_server
```

#### Common pitfalls

- Using `uv run -m mcp.client`: add `--` before extra args if needed, e.g. `uv run -m mcp.client -- http http://localhost:8085/mcp`.
- Calling `/mcp` with curl/httpx: requires an MCP-aware client; raw requests may return "Missing session ID".
- Python client Trio errors: install with `uv add trio` or `uv add 'mcp[client]'`.

### API Endpoints

When running as HTTP server:
- `/health` - Health check endpoint
- `/mcp` - MCP HTTP transport endpoint

## 🚀 OpenShift deployment and testing

### 1) Deploy with Make

```bash
# Basic deployment (recommended)
make install-mcp-server NAMESPACE=<namespace> LLM= LLAMA_STACK_URL=http://llamastack.<namespace>.svc.cluster.local:8321/v1/openai/v1

# Examples for different namespaces:
make install-mcp-server NAMESPACE=test1 LLM= LLAMA_STACK_URL=http://llamastack.test1.svc.cluster.local:8321/v1/openai/v1
make install-mcp-server NAMESPACE=main LLM= LLAMA_STACK_URL=http://llamastack.main.svc.cluster.local:8321/v1/openai/v1
```

**Required Parameters:**
- `NAMESPACE=<namespace>` - Target OpenShift namespace
- `LLAMA_STACK_URL=<url>` - URL to your LlamaStack service for local model inference

**Optional Parameters:**
- `MCP_SERVER_ROUTE_HOST=<host>` - Custom route hostname
- `REGISTRY`, `ORG`, `REPOSITORY`, `VERSION` - Override image location

#### Alternative: Helm

```bash
# Namespace must exist and you must be logged in with oc
helm upgrade --install mcp-server deploy/helm/mcp-server -n <namespace> \
  --set image.repository=quay.io/<org>/<repo>/mcp-server \
  --set image.tag=0.1.2 \
  --set env.PROMETHEUS_URL=https://thanos-querier.openshift-monitoring.svc.cluster.local:9091 \
  --set llm.url=http://llamastack.<namespace>.svc.cluster.local:8321/v1/openai/v1 \
  --set-json modelConfig='{"meta-llama/Llama-3.2-3B-Instruct":{"external":false,"serviceName":"llama-3-2-3b-instruct"}}'
```

Notes:
- ServiceAccount `mcp-analyzer` is created; it mounts a token as `THANOS_TOKEN` and sets `NAMESPACE` automatically.
- A release-scoped CA bundle ConfigMap `<release>-trusted-ca-bundle` is created and mounted to enable TLS verification for in-cluster services.
- RBAC ClusterRoleBindings are created to grant access to Thanos and cluster/user monitoring views. The cluster-scoped `grafana-prometheus-reader` role is NOT created by default to avoid ownership conflicts; enable with `--set rbac.createGrafanaRole=true` only in fresh clusters.

Optional settings:
- Route host: `--set route.host=<custom-host>` (otherwise OpenShift assigns one)
- SSE transport: `--set env.MCP_TRANSPORT_PROTOCOL=sse`
- LlamaStack URL: `--set llm.url=<llamastack-service-url>` (required for analyze tool)

### 2) Connect to the server

From the health route you will see the transport and endpoint, for example:

```text
{"status":"healthy","service":"observability-mcp-server","transport_protocol":"http","mcp_endpoint":"/mcp"}
```

- HTTP / Streamable HTTP:
```bash
npx @modelcontextprotocol/inspector http https://<route>/mcp
```
- SSE (only if enabled):
```bash
npx @modelcontextprotocol/inspector sse https://<route>/sse
```

### 3) Troubleshooting

#### Deployment Issues
- **Helm dependency errors** (404 from chart repository): Use `LLM=` to skip model config generation:
  ```bash
  make install-mcp-server NAMESPACE=test1 LLM=
  ```
- **Missing LLAMA_STACK_URL**: The `analyze` tool requires LlamaStack connection. Ensure it's set:
  ```bash
  make install-mcp-server NAMESPACE=test1 LLM= LLAMA_STACK_URL=http://llamastack.test1.svc.cluster.local:8321/v1/openai/v1
  ```

#### Connection Issues  
- **404 on connect**: ensure the URL includes the MCP path (`/mcp` for HTTP, `/sse` for SSE). The route root `/` returns 404.
- **403 from Thanos/Prometheus**:
  - Verify RBAC ClusterRoleBindings exist for `mcp-analyzer` and that the pod uses this ServiceAccount.
  - Confirm `THANOS_TOKEN` env is present and the CA bundle is mounted at `/etc/pki/ca-trust/extracted/pem/ca-bundle.crt`.
  - Test manually:
    ```bash
    TOKEN=$(oc -n <namespace> create token mcp-analyzer)
    curl -sS -H "Authorization: Bearer $TOKEN" \
      https://thanos-querier.openshift-monitoring.svc.cluster.local:9091/api/v1/query?query=up \
      --cacert /etc/pki/ca-trust/extracted/pem/ca-bundle.crt
    ```

#### Analyze Tool Issues
- **400 from LlamaStack**: Check that `LLAMA_STACK_URL` is correct and LlamaStack is running:
  ```bash
  oc get pods -n <namespace> | grep llamastack
  oc logs -n <namespace> deployment/llamastack
  ```
- **"no data" in metrics**: Use current timestamps, verify vLLM is generating metrics, check model_name format matches Prometheus labels.

#### General Issues
- **TLS issues**: ensure the CA bundle ConfigMap is injected (`trusted-ca-bundle`) and mounted; or set `VERIFY_SSL` to `true` (default) and keep the mount.

## 🛠️ Local Development Troubleshooting

### Server Not Starting
```bash
# Verify stdio entrypoint exists and is executable
test -x /path/to/.venv/bin/obs-mcp-stdio && echo OK || echo MISSING

# Check configuration (HTTP CLI)
obs-mcp-server --test-config

# Verify installation
pip list | grep obs-mcp-server
```

### No Data Returned
1. Ensure Prometheus is accessible: `curl http://localhost:9090/api/v1/query?query=up`
2. Check that vLLM models are deployed and generating metrics
3. Verify port forwarding is running: `scripts/local-dev.sh`

### Integration Issues
1. **Claude Desktop**: Restart the application after configuration changes
2. **Cursor IDE**: Restart Cursor IDE to load new MCP configuration  
3. **Path Issues**: Use `python setup_integration.py` to auto-detect correct paths


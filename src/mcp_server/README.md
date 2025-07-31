# AI Observability MCP Server

Model Context Protocol (MCP) server providing AI assistant integration for OpenShift AI observability data. Enables Claude Desktop and Cursor IDE to discover and analyze vLLM models and Kubernetes namespaces.

## 🎯 What It Does

The MCP server provides **3 core discovery tools** for AI assistants:

- **`list_models`** - Discover available vLLM models from Prometheus metrics
- **`list_namespaces`** - List monitored Kubernetes namespaces with observability data
- **`get_model_config`** - Get available LLM models for summarization and analysis

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

## 🔍 Available Tools

| Tool | Description | Returns |
|------|-------------|---------|
| `list_models` | Lists available vLLM models from metrics | Format: `"namespace | model_name"` |
| `list_namespaces` | Lists monitored Kubernetes namespaces | Sorted list of namespace names |
| `get_model_config` | Gets LLM models for summarization | Internal/external model configurations |

The first two tools query Prometheus metrics using identical logic as the main metrics API. The third tool reads environment configuration for LLM models.

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

## 🛠️ Troubleshooting

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


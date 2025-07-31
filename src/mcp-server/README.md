# AI Metrics MCP Server

A minimal Model Context Protocol server exposing vLLM metrics analysis tools to AI assistants.

## Features

- **get_available_models**: List all available vLLM models
- **analyze_model_performance**: Analyze model performance over time
- **detect_performance_issues**: Detect anomalies and performance issues

## Local Development

```bash
# Activate project virtual environment
source .venv/bin/activate

# Install MCP dependencies
cd src/mcp-server
uv add "mcp[cli]"
uv pip install -r requirements.txt

# Run with MCP CLI (recommended)
uv run mcp dev server.py

# Or run directly with script
python ./start_server.sh
```



**Manual AI Assistant Integration:**

Configure your AI assistant (Claude Desktop example):

```json
{
  "mcp": {
    "servers": {
      "metrics-analyzer": {
        "command": "python",
        "args": ["/path/to/src/mcp-server/server.py"],
        "env": {
          "PROMETHEUS_URL": "http://your-prometheus:9090"
        }
      }
    }
  }
}
```

Test with AI assistant queries:
- "What vLLM models are available?"
- "Analyze the performance of model X over the last 2 hours"  
- "Are there any performance issues with model Y?"

## Container Build

```bash
# From project root (context: src, dockerfile: src/mcp-server/Dockerfile)
podman build -f src/mcp-server/Dockerfile -t metrics-mcp-server:latest src/
```

## Running the Server


### Container Testing
```bash
# Build and test
podman build -f src/mcp-server/Dockerfile -t metrics-mcp-server:latest src/
podman run --rm metrics-mcp-server:latest
```

## Deployment

```bash
# Deploy to Kubernetes using Helm
helm install metrics-mcp-server deploy/helm/mcp-server/ --namespace <your-namespace>
```

## AI Assistant Integration

This MCP server can be integrated with:
- Claude Desktop
- Any MCP-compatible AI assistant

## Tools Available

### get_available_models
Returns list of vLLM models available for analysis.

### analyze_model_performance
Analyzes model performance metrics over specified time period.
- `model_name` (required): Name of vLLM model
- `hours` (optional): Time period in hours (default: 1)

### detect_performance_issues  
Detects anomalies and performance issues in model metrics.
- `model_name` (required): Name of vLLM model
- `hours` (optional): Time period in hours (default: 24) 
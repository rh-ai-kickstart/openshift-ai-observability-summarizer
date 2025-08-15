# Cursor MCP Integration Guide

## Quick Setup for Cursor IDE

### 1. Prerequisites
- **Cursor IDE** installed and running  
- **Dev environment** running (use `scripts/local-dev.sh`)
- **MCP server** installed: `cd src/mcp_server && pip install -e .`


### 3. Automatic Configuration
Run the existing setup script to auto-configure both Claude and Cursor:
```bash
cd src/mcp_server
python setup_integration.py
```

This creates :
- `.cursor/mcp.json` (for Cursor IDE with full LLM integration)

### 3. Enable MCP in Cursor
1. Open Cursor IDE Settings
2. Restart Cursor IDE
3. Enable **MCP Tools for AI Observability**


### 4. Manual Configuration (if needed)
Edit `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "ai-observability": {
      "command": "/full/path/to/.venv/bin/obs-mcp-server",
      "args": ["--local"],
      "env": {
        "PROMETHEUS_URL": "http://localhost:9090",
        "LLAMA_STACK_URL": "http://localhost:8321/v1/openai/v1",
        "MODEL_CONFIG": "{\"claude-3-sonnet\": {\"external\": false, \"modelName\": \"meta-llama/Llama-3.2-3B-Instruct\"}}"
      },
      "disabled": false
    }
  }
}
```

Now you can open new chat and ask for AI Observability and question accordingly. 
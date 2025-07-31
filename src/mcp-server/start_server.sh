#!/bin/bash

# MCP Server Startup Script
# Simple wrapper to start the AI Metrics MCP Server

set -e

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"

echo -e "${BLUE}🚀 Starting AI Metrics MCP Server${NC}"
echo "================================="
echo "Server location: $SCRIPT_DIR/server.py"
echo "Virtual env: $VENV_DIR"
echo ""

# Activate virtual environment
if [ -f "$VENV_DIR/bin/activate" ]; then
    echo -e "${BLUE}Activating virtual environment...${NC}"
    source "$VENV_DIR/bin/activate"
    echo -e "${GREEN}✅ Virtual environment activated${NC}"
else
    echo -e "${YELLOW}⚠️  Virtual environment not found at $VENV_DIR${NC}"
    echo "Please create it first: cd $PROJECT_ROOT && uv venv"
    exit 1
fi

# Change to MCP server directory
cd "$SCRIPT_DIR"

# Check if dependencies are installed
if ! python -c "from mcp.server.fastmcp import FastMCP" 2>/dev/null; then
    echo -e "${YELLOW}⚠️  Installing MCP dependencies...${NC}"
    uv add "mcp[cli]" || { echo "Failed to install mcp[cli]"; exit 1; }
    uv pip install -r requirements.txt || { echo "Failed to install requirements"; exit 1; }
fi

echo -e "${GREEN}✅ Dependencies ready${NC}"
echo -e "${BLUE}🎯 Starting MCP server...${NC}"
echo ""

# Start the server using MCP CLI
exec uv run mcp dev server.py
FROM registry.access.redhat.com/ubi9/python-311:latest

WORKDIR /app

# Install uv for faster dependency management
RUN pip install --no-cache-dir uv

# Copy requirements and install dependencies with uv
COPY mcp-server/requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# Copy core business logic and MCP server
COPY core/ core/
COPY mcp-server/ .

# Expose port for potential HTTP transport (future)
EXPOSE 8080

# Run MCP server with stdio transport
CMD ["python", "server.py"] 
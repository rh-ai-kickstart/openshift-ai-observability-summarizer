"""FastAPI application setup for Observability MCP Server (no auth)."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from mcp_server.mcp import ObservabilityMCPServer
from mcp_server.settings import settings

server = ObservabilityMCPServer()

# Select transport protocol
if settings.MCP_TRANSPORT_PROTOCOL == "sse":
    from fastmcp.server.http import create_sse_app  # type: ignore

    mcp_app = create_sse_app(server.mcp, message_path="/sse/message", sse_path="/sse")
else:
    mcp_app = server.mcp.http_app(path="/mcp")

# Initialize FastAPI with MCP lifespan
app = FastAPI(lifespan=mcp_app.lifespan)

# Optional CORS
if settings.CORS_ENABLED:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=settings.CORS_CREDENTIALS,
        allow_methods=settings.CORS_METHODS,
        allow_headers=settings.CORS_HEADERS,
    )


@app.get("/health")
async def health_check():
    return JSONResponse(
        status_code=200,
        content={
            "status": "healthy",
            "service": "observability-mcp-server",
            "transport_protocol": settings.MCP_TRANSPORT_PROTOCOL,
            "mcp_endpoint": "/mcp",
        },
    )


# Mount the MCP app at root level
app.mount("/", mcp_app)



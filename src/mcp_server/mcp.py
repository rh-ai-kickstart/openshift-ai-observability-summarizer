import logging

from .settings import settings
from mcp_server.utils.pylogger import get_python_logger, force_reconfigure_all_loggers


class ObservabilityMCPServer:
    def __init__(self) -> None:
        # Lazy import to avoid import-time circulars when fastmcp pulls in mcp.types
        from fastmcp import FastMCP  # type: ignore

        get_python_logger(settings.PYTHON_LOG_LEVEL)
        self.mcp = FastMCP("metrics-observability")
        # Ensure third-party loggers are reconfigured after FastMCP init
        force_reconfigure_all_loggers(settings.PYTHON_LOG_LEVEL)
        self._register_mcp_tools()
        logging.getLogger(__name__).info("Observability MCP Server initialized")

    def _register_mcp_tools(self) -> None:
        from .tools.observability_tools import (
            list_models,
            list_namespaces,
            get_model_config,
        )

        self.mcp.tool()(list_models)
        self.mcp.tool()(list_namespaces)
        self.mcp.tool()(get_model_config)


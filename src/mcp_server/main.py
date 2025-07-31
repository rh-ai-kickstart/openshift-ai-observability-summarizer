"""Main entrypoint for Observability MCP Server (no auth)."""

import sys
from typing import NoReturn

import uvicorn

from mcp_server.api import app
from mcp_server.settings import settings, validate_config
from mcp_server.utils.pylogger import get_python_logger, get_uvicorn_log_config

logger = get_python_logger()


def handle_startup_error(error: Exception, context: str = "server startup") -> NoReturn:
    if isinstance(error, ValueError):
        logger.critical(f"Configuration error during {context}: {error}")
        sys.exit(1)
    elif isinstance(error, KeyboardInterrupt):
        logger.info("Server startup interrupted by user")
        sys.exit(0)
    elif isinstance(error, PermissionError):
        logger.critical(f"Permission error during {context}: {error}")
        sys.exit(1)
    elif isinstance(error, ConnectionError):
        logger.critical(f"Connection error during {context}: {error}")
        sys.exit(1)
    else:
        logger.critical(f"Unexpected error during {context}: {error}", exc_info=True)
        sys.exit(1)


def main() -> None:
    try:
        validate_config(settings)

        logger.info(
            f"Server configured to use {settings.MCP_TRANSPORT_PROTOCOL} protocol"
        )

        uvicorn_config = {}
        if settings.MCP_SSL_KEYFILE and settings.MCP_SSL_CERTFILE:
            uvicorn_config["ssl_keyfile"] = settings.MCP_SSL_KEYFILE
            uvicorn_config["ssl_certfile"] = settings.MCP_SSL_CERTFILE
            logger.info(
                "Starting server with SSL",
                ssl_keyfile=settings.MCP_SSL_KEYFILE,
                ssl_certfile=settings.MCP_SSL_CERTFILE,
            )

        uvicorn.run(
            app,
            host=settings.MCP_HOST,
            port=settings.MCP_PORT,
            log_config=get_uvicorn_log_config(settings.PYTHON_LOG_LEVEL),
            **uvicorn_config,
        )

    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down")
    except Exception as e:
        handle_startup_error(e, "server startup")
    finally:
        logger.info("Observability MCP server shutting down")


def run() -> None:
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error("Server failed to start", error=str(e), exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    run()



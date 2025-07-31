#!/usr/bin/env python3
"""CLI interface for Observability MCP Server"""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

from mcp_server.settings import settings
from mcp_server.utils.pylogger import get_python_logger, get_uvicorn_log_config


def load_env_file(env_file: Optional[str] = None) -> None:
    if env_file:
        env_path = Path(env_file)
        if env_path.exists():
            print(f"Loading environment from {env_path}", file=sys.stderr)
            load_dotenv(env_path)
    elif Path(".env").exists():
        print("Loading environment from .env", file=sys.stderr)
        load_dotenv()


def validate_environment() -> bool:
    required_vars = ["PROMETHEUS_URL"]
    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        print(f"[ERROR] Missing required env vars: {', '.join(missing)}", file=sys.stderr)
        return False
    return True


def cmd_test_config() -> int:
    return 0 if validate_environment() else 1


def cmd_health(url: Optional[str]) -> int:
    target = url or f"http://{settings.MCP_HOST}:{settings.MCP_PORT}/health"
    try:
        resp = httpx.get(target, timeout=5.0)
        print({"status_code": resp.status_code, "body": resp.text})
        return 0 if resp.status_code == 200 else 2
    except Exception as exc:
        print(f"[ERROR] Health check failed: {exc}", file=sys.stderr)
        return 2


def cmd_serve(host: Optional[str], port: Optional[int]) -> int:
    # Lazy import uvicorn to keep CLI import light
    import uvicorn

    log = get_python_logger(settings.PYTHON_LOG_LEVEL)
    uvicorn.run(
        "mcp_server.api:app",
        host=host or settings.MCP_HOST,
        port=port or settings.MCP_PORT,
        log_config=get_uvicorn_log_config(settings.PYTHON_LOG_LEVEL),
        reload=False,
        factory=False,
    )
    log.info("Server stopped")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Observability MCP Server")
    parser.add_argument("--env-file")
    # Back-compat flag
    parser.add_argument("--test-config", action="store_true", help="Validate required env vars")

    subparsers = parser.add_subparsers(dest="command")

    p_serve = subparsers.add_parser("serve", help="Run the HTTP server (FastAPI/Uvicorn)")
    p_serve.add_argument("--host", help="Bind host (default from settings)")
    p_serve.add_argument("--port", type=int, help="Bind port (default from settings)")

    p_health = subparsers.add_parser("health", help="Call the /health endpoint")
    p_health.add_argument("--url", help="Health URL (default http://<host>:<port>/health)")

    args = parser.parse_args()

    if args.env_file or Path(".env").exists():
        load_env_file(args.env_file)

    if args.test_config and not args.command:
        return cmd_test_config()

    if args.command == "serve":
        return cmd_serve(args.host, args.port)
    if args.command == "health":
        return cmd_health(args.url)

    # Default action: verify API can import
    from mcp_server.api import app  # noqa: F401
    print("[OK] API import successful", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())



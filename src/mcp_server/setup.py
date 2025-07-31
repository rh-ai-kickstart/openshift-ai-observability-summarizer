#!/usr/bin/env python3
"""
Setup configuration for Observability MCP Server (mcp_server)
"""
from setuptools import setup, find_packages


def read_readme() -> str:
    try:
        with open("README.md", "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "Observability MCP Server - AI Metrics Analysis Tools"


def read_requirements() -> list[str]:
    try:
        with open("requirements.txt", "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip() and not line.startswith("#")]
    except FileNotFoundError:
        return []


setup(
    name="obs-mcp-server",
    version="1.0.0",
    description="MCP server for AI metrics analysis and observability tools",
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    packages=[
        "mcp_server",
        "mcp_server.tools",
        "mcp_server.utils",
    ],
    package_dir={
        "mcp_server": ".",
        "mcp_server.tools": "tools",
        "mcp_server.utils": "utils",
    },
    python_requires=">=3.10",
    install_requires=read_requirements(),
    entry_points={
        "console_scripts": [
            "obs-mcp-server=mcp_server.cli:main",
            "obs-mcp-stdio=mcp_server.stdio_server:main",
        ],
    },
)



from typing import List, Optional

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    MCP_HOST: str = Field(default="0.0.0.0")
    MCP_PORT: int = Field(default=8085)
    PYTHON_LOG_LEVEL: str = Field(default="INFO")

    # Transport and SSL
    MCP_TRANSPORT_PROTOCOL: str = Field(default="http")  # "http" | "sse" | "streamable-http"
    MCP_SSL_KEYFILE: Optional[str] = Field(default=None)
    MCP_SSL_CERTFILE: Optional[str] = Field(default=None)

    # CORS
    CORS_ENABLED: bool = Field(default=False)
    CORS_ORIGINS: List[str] = Field(default_factory=lambda: ["*"])
    CORS_CREDENTIALS: bool = Field(default=True)
    CORS_METHODS: List[str] = Field(default_factory=lambda: ["*"])
    CORS_HEADERS: List[str] = Field(default_factory=lambda: ["*"])


def validate_config(settings: "Settings") -> None:
    # Port range
    if not (1024 <= settings.MCP_PORT <= 65535):
        raise ValueError(f"MCP_PORT must be between 1024 and 65535, got {settings.MCP_PORT}")

    # Log level
    valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    if settings.PYTHON_LOG_LEVEL.upper() not in valid_log_levels:
        raise ValueError(
            f"PYTHON_LOG_LEVEL must be one of {valid_log_levels}, got {settings.PYTHON_LOG_LEVEL}"
        )

    # Transport protocol
    valid_transport_protocols = ["streamable-http", "sse", "http"]
    if settings.MCP_TRANSPORT_PROTOCOL not in valid_transport_protocols:
        raise ValueError(
            f"MCP_TRANSPORT_PROTOCOL must be one of {valid_transport_protocols}, got {settings.MCP_TRANSPORT_PROTOCOL}"
        )


settings = Settings()



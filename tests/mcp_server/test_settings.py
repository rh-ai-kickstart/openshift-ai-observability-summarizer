import os
from unittest.mock import patch

import pytest

from mcp_server.settings import Settings, validate_config


def test_settings_defaults():
    s = Settings()
    assert s.MCP_HOST == "0.0.0.0"
    assert s.MCP_PORT == 8085
    assert s.MCP_TRANSPORT_PROTOCOL == "http"
    assert s.PYTHON_LOG_LEVEL == "INFO"
    assert s.CORS_ENABLED is False
    assert isinstance(s.CORS_ORIGINS, list)


def test_settings_env_overrides():
    env = {
        "MCP_HOST": "localhost",
        "MCP_PORT": "9001",
        "MCP_TRANSPORT_PROTOCOL": "sse",
        "PYTHON_LOG_LEVEL": "DEBUG",
        "CORS_ENABLED": "true",
    }
    with patch.dict(os.environ, env, clear=False):
        s = Settings()
        assert s.MCP_HOST == "localhost"
        assert s.MCP_PORT == 9001
        assert s.MCP_TRANSPORT_PROTOCOL == "sse"
        assert s.PYTHON_LOG_LEVEL == "DEBUG"
        assert s.CORS_ENABLED is True


def test_validate_config_errors():
    s = Settings()
    s.MCP_PORT = 1023
    with pytest.raises(ValueError):
        validate_config(s)

    s.MCP_PORT = 65536
    with pytest.raises(ValueError):
        validate_config(s)

    s = Settings()
    s.PYTHON_LOG_LEVEL = "INVALID"
    with pytest.raises(ValueError):
        validate_config(s)

    s = Settings()
    s.MCP_TRANSPORT_PROTOCOL = "invalid"
    with pytest.raises(ValueError):
        validate_config(s)



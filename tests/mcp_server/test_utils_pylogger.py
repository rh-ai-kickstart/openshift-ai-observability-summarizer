from mcp_server.utils.pylogger import get_python_logger
from unittest.mock import Mock, patch
import mcp_server.utils.pylogger as pylog


def test_get_python_logger_returns_logger():
    logger = get_python_logger()
    assert logger is not None
    # Should support basic logging calls without raising
    logger.info("test message")


@patch("mcp_server.utils.pylogger.get_python_logger")
def test_force_reconfigure_calls_get_python_logger(mock_get_logger: Mock):
    from mcp_server.utils.pylogger import force_reconfigure_all_loggers

    mock_get_logger.return_value = Mock()

    force_reconfigure_all_loggers("DEBUG")

    mock_get_logger.assert_called_once_with("DEBUG")


def test_get_uvicorn_log_config_structure_and_error_levels():
    cfg = pylog.get_uvicorn_log_config("INFO")
    assert isinstance(cfg, dict)
    assert cfg.get("version") == 1
    assert "formatters" in cfg and "handlers" in cfg and "loggers" in cfg

    # Ensure ERROR_ONLY_LOGGERS are set to ERROR
    for name in pylog.ERROR_ONLY_LOGGERS:
        assert name in cfg["loggers"]
        assert cfg["loggers"][name]["level"] == "ERROR"



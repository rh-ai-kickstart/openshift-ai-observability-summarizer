from unittest.mock import Mock, patch

import pytest

import mcp_server.main as main_mod


@patch("mcp_server.main.validate_config")
@patch("mcp_server.main.uvicorn")
def test_main_success(mock_uvicorn, _):
    with patch("mcp_server.main.settings") as s:
        s.MCP_HOST = "0.0.0.0"
        s.MCP_PORT = 8085
        s.MCP_TRANSPORT_PROTOCOL = "http"
        s.PYTHON_LOG_LEVEL = "INFO"
        s.MCP_SSL_KEYFILE = None
        s.MCP_SSL_CERTFILE = None

        main_mod.main()

        mock_uvicorn.run.assert_called_once()
        kwargs = mock_uvicorn.run.call_args.kwargs
        assert kwargs["host"] == "0.0.0.0"
        assert kwargs["port"] == 8085
        assert "log_config" in kwargs


@patch("mcp_server.main.validate_config")
@patch("mcp_server.main.uvicorn")
def test_main_with_ssl(mock_uvicorn, _):
    with patch("mcp_server.main.settings") as s:
        s.MCP_HOST = "0.0.0.0"
        s.MCP_PORT = 8085
        s.MCP_TRANSPORT_PROTOCOL = "http"
        s.PYTHON_LOG_LEVEL = "INFO"
        s.MCP_SSL_KEYFILE = "/tmp/k.pem"
        s.MCP_SSL_CERTFILE = "/tmp/c.pem"

        main_mod.main()

        kwargs = mock_uvicorn.run.call_args.kwargs
        assert kwargs["ssl_keyfile"] == "/tmp/k.pem"
        assert kwargs["ssl_certfile"] == "/tmp/c.pem"


@patch("mcp_server.main.main", side_effect=KeyboardInterrupt())
@patch("mcp_server.main.sys")
def test_run_keyboard_interrupt(mock_sys, _):
    try:
        main_mod.run()
    except SystemExit:
        pass
    mock_sys.exit.assert_called_with(0)


def test_handle_startup_error_paths():
    with patch("mcp_server.main.logger") as lg, patch("mcp_server.main.sys") as sy:
        for err, code in [
            (ValueError("v"), 1),
            (PermissionError("p"), 1),
            (ConnectionError("c"), 1),
        ]:
            try:
                main_mod.handle_startup_error(err, "ctx")
            except SystemExit:
                pass
            sy.exit.assert_called_with(code)

        try:
            main_mod.handle_startup_error(Exception("g"), "ctx")
        except SystemExit:
            pass
        sy.exit.assert_called_with(1)


@patch("mcp_server.main.validate_config")
@patch("mcp_server.main.handle_startup_error")
def test_main_exception_handling(mock_handle_err, mock_validate):
    error = Exception("boom")
    mock_validate.side_effect = error
    main_mod.main()
    mock_handle_err.assert_called_with(error, "server startup")



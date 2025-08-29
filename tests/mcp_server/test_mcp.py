from unittest.mock import Mock, patch


@patch("mcp_server.mcp.get_python_logger")
@patch("mcp_server.mcp.force_reconfigure_all_loggers")
@patch("fastmcp.FastMCP", autospec=True)
def test_observability_mcp_server_registers_four_tools_and_reconfigures(
    MockFastMCP, mock_reconfigure, mock_get_logger
):
    # Arrange: create FastMCP instance mock and make tool() act like a decorator
    mcp_instance = Mock()
    call_counter = {"count": 0}

    def tool_decorator():
        def _wrap(fn):  # noqa: ARG001 - fn unused
            call_counter["count"] += 1
            return fn

        return _wrap

    mcp_instance.tool.side_effect = tool_decorator
    MockFastMCP.return_value = mcp_instance

    # Act
    from mcp_server.mcp import ObservabilityMCPServer

    server = ObservabilityMCPServer()

    # Assert
    assert server.mcp is mcp_instance
    assert call_counter["count"] == 4  # four tools registered
    mock_get_logger.assert_called_once()
    mock_reconfigure.assert_called_once()


@patch("fastmcp.FastMCP", side_effect=RuntimeError("boom"))
def test_observability_mcp_server_init_failure_propagates(_):
    from mcp_server.mcp import ObservabilityMCPServer
    import pytest

    with pytest.raises(RuntimeError, match="boom"):
        ObservabilityMCPServer()



from contextlib import asynccontextmanager
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient


def _lifespan_ctx():
    @asynccontextmanager
    async def _lifespan(app):  # noqa: ARG001 - app unused
        yield

    return _lifespan


def _reload_api_with_mocks(protocol: str | None = None):
    import importlib
    import mcp_server.settings as settings_mod

    # Build server mock with mcp.http_app().lifespan
    server_mock = Mock()
    mcp_mock = Mock()
    http_app_mock = Mock()
    http_app_mock.lifespan = _lifespan_ctx()
    mcp_mock.http_app.return_value = http_app_mock
    server_mock.mcp = mcp_mock

    patches = [patch("mcp_server.mcp.ObservabilityMCPServer", return_value=server_mock)]

    # If SSE, patch create_sse_app and set settings before reload
    if protocol == "sse":
        sse_app_mock = Mock()
        sse_app_mock.lifespan = _lifespan_ctx()
        patches.append(patch("fastmcp.server.http.create_sse_app", return_value=sse_app_mock))
        # Persistently set protocol for the duration of this import
        settings_mod.settings.MCP_TRANSPORT_PROTOCOL = "sse"

    with patches[0]:
        ctxs = []
        try:
            for p in patches[1:]:
                ctxs.append(p.start())

            import mcp_server.api as api_module

            importlib.reload(api_module)
            return api_module
        finally:
            for p in patches[1:]:
                p.stop()


def test_app_health_structure_and_defaults():
    api = _reload_api_with_mocks()
    client = TestClient(api.app)

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    for key in ["status", "service", "transport_protocol", "mcp_endpoint"]:
        assert key in data
    assert data["service"] == "observability-mcp-server"
    assert resp.headers["content-type"].startswith("application/json")


def test_health_protocol_switch_runtime():
    api = _reload_api_with_mocks()
    client = TestClient(api.app)

    for proto in ["http", "sse", "streamable-http"]:
        with patch.object(api.settings, "MCP_TRANSPORT_PROTOCOL", proto):
            resp = client.get("/health")
            assert resp.status_code == 200
            assert resp.json()["transport_protocol"] == proto


def test_sse_transport_wiring_on_import():
    # Reload module with protocol forced to sse and SSE app patched
    api = _reload_api_with_mocks(protocol="sse")
    client = TestClient(api.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["transport_protocol"] == "sse"



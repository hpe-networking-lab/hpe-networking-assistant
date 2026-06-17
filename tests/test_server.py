"""Tests for the zero-dependency MCP stdio server."""

import io
import json

from hpe_mist_mcp import server


def test_initialize_echoes_protocol():
    res = server.dispatch("initialize", {"protocolVersion": "2025-06-18"})
    assert res["protocolVersion"] == "2025-06-18"
    assert res["serverInfo"]["name"] == server.SERVER_NAME
    assert "tools" in res["capabilities"]


def test_initialized_notification_has_no_response():
    assert server.dispatch("notifications/initialized", {}) is None


def test_tools_list_has_read_tools_and_setup():
    res = server.dispatch("tools/list", {})
    names = {t["name"] for t in res["tools"]}
    assert names == {
        "start_setup",
        "get_status",
        "get_organizations",
        "get_sites",
        "get_access_points",
        "get_switches",
        "get_clients",
        "get_offline_access_points",
        "generate_health_report",
        "generate_inventory_report",
        "find_client",
        "trace_client",
        "get_nac_clients",
        "troubleshoot_authentication",
        "generate_nac_dashboard",
        "get_marvis_actions",
        "get_alarms",
        "get_wired_clients",
        "get_sle",
        "get_switch_ports",
        "export_org_config",
        "set_active_org",
        "diff_org_config",
    }
    for tool in res["tools"]:
        assert tool["inputSchema"]["type"] == "object"


def test_unknown_method_raises():
    import pytest
    with pytest.raises(server.RpcError) as exc:
        server.dispatch("does/not/exist", {})
    assert exc.value.code == -32601


def test_tool_call_routes_and_uses_mock(monkeypatch):
    class FakeClient:
        def get_organizations(self):
            return [{"org_id": "org-1", "name": "Acme", "role": "admin"}]

    monkeypatch.setattr(server, "_client", FakeClient())
    res = server.dispatch("tools/call", {"name": "get_organizations", "arguments": {}})
    assert res["isError"] is False
    payload = json.loads(res["content"][0]["text"])
    assert payload["count"] == 1
    assert res["structuredContent"]["organizations"][0]["org_id"] == "org-1"


def test_tool_call_unknown_tool():
    import pytest
    with pytest.raises(server.RpcError):
        server.dispatch("tools/call", {"name": "nope", "arguments": {}})


def test_serve_handshake_end_to_end(monkeypatch):
    class FakeClient:
        def get_organizations(self):
            return [{"org_id": "o1", "name": "Acme"}]

    monkeypatch.setattr(server, "_client", FakeClient())
    requests = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "get_organizations", "arguments": {}}}),
    ]) + "\n"
    out = io.StringIO()
    server.serve(io.StringIO(requests), out)
    lines = [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]
    # initialize, tools/list, tools/call -> 3 responses; the notification yields none.
    assert [m["id"] for m in lines] == [1, 2, 3]
    assert lines[0]["result"]["serverInfo"]["name"] == server.SERVER_NAME

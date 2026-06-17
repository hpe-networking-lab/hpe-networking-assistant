"""Tests for the NAC visualizer (aggregation + HTML rendering) and its tool."""

from hpe_mist_mcp import server
from hpe_mist_mcp.nac_visualizer import build_nac_overview, render_nac_dashboard_html


class FakeClient:
    def search_nac_clients(self, org_id, duration="1d", limit=1000, **_):
        return [
            {"mac": "a", "type": "wireless", "auth_type": "eap-tls", "last_status": "Permitted"},
            {"mac": "b", "type": "wireless", "auth_type": "peap", "last_status": "Permitted"},
            {"mac": "c", "type": "wired", "auth_type": "mab", "last_status": "Denied"},
        ]

    def search_nac_events(self, org_id, duration="1d", limit=1000, **_):
        return [
            {"type": "NAC_CLIENT_PERMIT", "username": "bob"},
            {"type": "NAC_CLIENT_PERMIT", "username": "amy"},
            {"type": "NAC_CLIENT_DENY", "username": "eve", "nacrule_name": "Default-Deny"},
            {"type": "NAC_CLIENT_DENY", "username": "eve", "nacrule_name": "Default-Deny"},
        ]


def test_build_nac_overview_aggregates():
    data = build_nac_overview(FakeClient(), "org-1", org_name="Acme")
    assert data["client_count"] == 3
    assert data["event_count"] == 4
    assert data["failure_count"] == 2
    assert dict(data["by_auth_type"])["eap-tls"] == 1
    assert dict(data["by_client_type"])["wireless"] == 2
    assert dict(data["top_failing_users"])["eve"] == 2
    assert dict(data["top_failing_rules"])["Default-Deny"] == 2


def test_render_dashboard_html():
    html_out = render_nac_dashboard_html(build_nac_overview(FakeClient(), "org-1", org_name="Acme"))
    assert html_out.lstrip().startswith("<!doctype html>")
    assert "NAC Dashboard — Acme" in html_out
    assert "Success rate" in html_out
    assert "eve" in html_out               # failing user shown
    assert "Default-Deny" in html_out      # failing rule shown
    assert "50%" in html_out               # 2 of 4 events succeeded


def test_html_escapes_org_name():
    data = build_nac_overview(FakeClient(), "org-1", org_name="<script>x</script>")
    html_out = render_nac_dashboard_html(data)
    assert "<script>x</script>" not in html_out
    assert "&lt;script&gt;" in html_out


def test_generate_nac_dashboard_tool(monkeypatch):
    monkeypatch.setattr(server._config, "token", "tok")
    monkeypatch.setattr(server._config, "region", "global01")
    monkeypatch.setattr(server._config, "org_id", "org-1")
    monkeypatch.setattr(server, "_client", FakeClient())
    monkeypatch.setattr(server, "_org_name", lambda oid: "Acme")
    out = server.tool_generate_nac_dashboard()
    assert out["format"] == "html"
    assert out["summary"] == {"clients": 3, "events": 4, "failures": 2}
    assert "NAC Dashboard" in out["html"]

"""Tests for the firmware compliance report."""

from hpe_mist_mcp import server
from hpe_mist_mcp.reports import build_firmware_report, render_firmware_markdown


class FakeClient:
    def get_access_points(self, org_id):
        return [
            {"mac": "a1", "name": "AP-1", "model": "AP45", "version": "0.14.1", "site_id": "s1"},
            {"mac": "a2", "name": "AP-2", "model": "AP45", "version": "0.14.1", "site_id": "s1"},
            {"mac": "a3", "name": "AP-3", "model": "AP45", "version": "0.12.0", "site_id": "s1"},  # behind
            {"mac": "a4", "name": "AP-4", "model": "AP12", "version": "0.14.1", "site_id": "s2"},
        ]

    def get_switches(self, org_id):
        return [{"mac": "s1m", "name": "SW-1", "model": "EX4100", "version": "23.4R2", "site_id": "s1"}]


def test_build_firmware_report():
    data = build_firmware_report(FakeClient(), "org-1", org_name="Acme")
    assert data["device_count"] == 5
    assert data["non_compliant_count"] == 1
    ap45 = next(m for m in data["models"] if m["model"] == "AP45")
    assert ap45["target_version"] == "0.14.1"      # majority
    assert ap45["on_target"] == 2
    assert ap45["behind"][0]["name"] == "AP-3" and ap45["behind"][0]["version"] == "0.12.0"


def test_render_firmware_markdown():
    md = render_firmware_markdown(build_firmware_report(FakeClient(), "org-1", org_name="Acme"))
    assert "# Firmware Compliance Report — Acme" in md
    assert "Devices behind the target version" in md
    assert "AP-3" in md and "0.12.0" in md


def test_firmware_tool(monkeypatch):
    monkeypatch.setattr(server._config, "token", "tok")
    monkeypatch.setattr(server._config, "region", "global01")
    monkeypatch.setattr(server._config, "org_id", "org-1")
    monkeypatch.setattr(server, "_client", FakeClient())
    monkeypatch.setattr(server, "_org_name", lambda oid: "Acme")
    out = server.tool_generate_firmware_report()
    assert out["format"] == "markdown"
    assert out["summary"]["device_count"] == 5 and out["summary"]["non_compliant_count"] == 1
    assert "Firmware Compliance Report" in out["markdown"]

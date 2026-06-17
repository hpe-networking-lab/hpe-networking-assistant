"""Tests for multi-org resolution (set_active_org / name resolution) and config diff."""

import json

from hpe_mist_mcp import server


class FakeClient:
    def __init__(self):
        self.exported = None

    def get_organizations(self):
        return [
            {"org_id": "0229fa19-6a25-4098-8232-7b6fdd20c4fa", "name": "Smitty_Lab"},
            {"org_id": "1111aaaa-2222-3333-4444-555566667777", "name": "Acme Corp"},
        ]

    def export_org_config(self, org_id, resources=None):
        return self.exported


def _prime(monkeypatch, fc=None):
    fc = fc or FakeClient()
    monkeypatch.setattr(server._config, "token", "tok")
    monkeypatch.setattr(server._config, "region", "global01")
    monkeypatch.setattr(server._config, "org_id", None)
    monkeypatch.setattr(server, "_client", fc)
    monkeypatch.setattr(server, "save_discovery", lambda **k: None)
    return fc


def test_resolve_org_by_name(monkeypatch):
    _prime(monkeypatch)
    assert server._resolve_org("Acme Corp") == "1111aaaa-2222-3333-4444-555566667777"
    # a real uuid passes through unchanged
    assert server._resolve_org("0229fa19-6a25-4098-8232-7b6fdd20c4fa") == "0229fa19-6a25-4098-8232-7b6fdd20c4fa"


def test_set_active_org_by_name(monkeypatch):
    _prime(monkeypatch)
    out = server.tool_set_active_org(org="smitty")
    assert out["active_org_id"] == "0229fa19-6a25-4098-8232-7b6fdd20c4fa"
    assert server._config.org_id == "0229fa19-6a25-4098-8232-7b6fdd20c4fa"


def test_set_active_org_unknown(monkeypatch):
    _prime(monkeypatch)
    out = server.tool_set_active_org(org="Nope")
    assert "error" in out and "organizations" in out


def test_diff_org_config(monkeypatch, tmp_path):
    fc = _prime(monkeypatch)
    monkeypatch.setattr(server._config, "org_id", "0229fa19-6a25-4098-8232-7b6fdd20c4fa")
    baseline = {
        "config": {
            "resources": {
                "wlans": [{"id": "w1", "name": "Corp", "vlan": 10}, {"id": "w2", "name": "Guest"}],
                "networks": [{"id": "n1", "name": "Data"}],
            }
        }
    }
    f = tmp_path / "baseline.json"
    f.write_text(json.dumps(baseline), encoding="utf-8")
    # current: w1 changed (vlan 10->20), w2 removed, w3 added; networks unchanged
    fc.exported = {
        "resources": {
            "wlans": [{"id": "w1", "name": "Corp", "vlan": 20}, {"id": "w3", "name": "IoT"}],
            "networks": [{"id": "n1", "name": "Data"}],
        }
    }
    out = server.tool_diff_org_config(baseline_file=str(f))
    assert out["total_changes"] == 3
    w = out["changes"]["wlans"]
    assert w["added"] == ["IoT"] and w["removed"] == ["Guest"] and w["changed"] == ["Corp"]
    assert "networks" not in out["changes"]   # unchanged type omitted


def test_diff_org_config_missing_file(monkeypatch):
    _prime(monkeypatch)
    monkeypatch.setattr(server._config, "org_id", "0229fa19-6a25-4098-8232-7b6fdd20c4fa")
    out = server.tool_diff_org_config(baseline_file="/no/such/file.json")
    assert "error" in out

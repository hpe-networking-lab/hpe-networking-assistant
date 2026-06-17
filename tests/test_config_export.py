"""Tests for export_org_config (org configuration backup)."""

import io
import json
import urllib.error

from hpe_mist_mcp import mist_client, server
from hpe_mist_mcp.mist_client import MistClient


class FakeResp:
    def __init__(self, payload):
        self._d = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._d

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def router(routes, errors=()):
    def _f(req, timeout=None):
        url = req.full_url
        for frag in errors:
            if frag in url:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
        for frag, payload in routes.items():
            if frag in url:
                return FakeResp(payload)
        return FakeResp([])  # default: empty list
    return _f


def test_export_bundle_assembles(monkeypatch):
    routes = {
        "/api/v1/orgs/o\"": {"id": "o", "name": "Acme"},   # never matches; org handled below
        "/orgs/o/sites": [{"id": "s1", "name": "HQ"}],
        "/orgs/o/wlans": [{"id": "w1", "ssid": "Corp"}],
    }
    # org object: the bare /api/v1/orgs/o (no trailing path)
    def fake(req, timeout=None):
        u = req.full_url
        if u.endswith("/api/v1/orgs/o"):
            return FakeResp({"id": "o", "name": "Acme"})
        if "/orgs/o/sites" in u:
            return FakeResp([{"id": "s1", "name": "HQ"}])
        if "/orgs/o/wlans" in u:
            return FakeResp([{"id": "w1", "ssid": "Corp"}])
        return FakeResp([])
    monkeypatch.setattr(mist_client.urllib.request, "urlopen", fake)
    bundle = MistClient(token="t", region="global01").export_org_config("o", resources=["sites", "wlans"])
    assert bundle["org"]["name"] == "Acme"
    assert bundle["resources"]["sites"][0]["name"] == "HQ"
    assert bundle["resources"]["wlans"][0]["ssid"] == "Corp"
    assert bundle["errors"] == {}


def test_export_records_errors(monkeypatch):
    monkeypatch.setattr(mist_client.urllib.request, "urlopen",
                        router({}, errors=["/orgs/o/vpns"]))
    bundle = MistClient(token="t", region="global01").export_org_config("o", resources=["networks", "vpns"])
    assert "vpns" in bundle["errors"]
    assert bundle["resources"]["networks"] == []


class FakeClient:
    def export_org_config(self, org_id, resources=None):
        return {
            "org": {"id": org_id, "name": "Acme"},
            "resources": {"sites": [{"id": "s1"}], "wlans": [{"id": "w1"}, {"id": "w2"}]},
            "errors": {},
        }


def test_export_tool_counts(monkeypatch):
    monkeypatch.setattr(server._config, "token", "tok")
    monkeypatch.setattr(server._config, "region", "global01")
    monkeypatch.setattr(server._config, "org_id", "org-1")
    monkeypatch.setattr(server, "_client", FakeClient())
    out = server.tool_export_org_config()
    assert out["org_name"] == "Acme"
    assert out["resource_counts"] == {"sites": 1, "wlans": 2}
    assert out["config"]["org"]["id"] == "org-1"

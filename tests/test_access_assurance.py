"""Tests for Access Assurance / NAC tools and client search methods."""

import json

from hpe_mist_mcp import mist_client, server
from hpe_mist_mcp.mist_client import MistClient


class FakeResp:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def router(routes):
    def _fake(req, timeout=None):
        for frag, payload in routes.items():
            if frag in req.full_url:
                return FakeResp(payload)
        raise AssertionError(f"Unexpected URL: {req.full_url}")
    return _fake


def test_search_nac_clients(monkeypatch):
    payload = {"results": [{"mac": "aa", "last_username": "bob", "type": "wireless",
                            "auth_type": "eap-tls", "last_status": "Permitted"}]}
    monkeypatch.setattr(mist_client.urllib.request, "urlopen",
                        router({"/nac_clients/search": payload}))
    c = MistClient(token="t", region="global01")
    out = c.search_nac_clients("org-1", auth_type="eap-tls")
    assert out[0]["last_username"] == "bob"


def test_search_nac_events(monkeypatch):
    payload = {"results": [{"type": "NAC_CLIENT_DENY", "timestamp": 1700000000, "mac": "aa"}]}
    monkeypatch.setattr(mist_client.urllib.request, "urlopen",
                        router({"/nac_clients/events/search": payload}))
    c = MistClient(token="t", region="global01")
    out = c.search_nac_events("org-1", mac="aa")
    assert out[0]["type"] == "NAC_CLIENT_DENY"


class FakeClient:
    def search_nac_clients(self, org_id, mac=None, type=None, auth_type=None, nacrule_id=None, duration="1d", limit=100):
        return [{"mac": "aa", "last_username": "bob", "type": "wireless", "auth_type": "eap-tls",
                 "last_ssid": "Corp", "last_vlan": 10, "last_nacrule_name": "Employees",
                 "last_status": "Permitted", "last_seen": 1700000000}]

    def search_nac_events(self, org_id, mac=None, type=None, nacrule_id=None, auth_type=None, duration="1d", limit=100):
        return [
            {"type": "NAC_CLIENT_PERMIT", "timestamp": 1700000000, "mac": "aa", "username": "bob"},
            {"type": "NAC_CLIENT_DENY", "timestamp": 1700000100, "mac": "cc", "username": "eve",
             "text": "no matching rule"},
        ]


def _prime(monkeypatch):
    monkeypatch.setattr(server._config, "token", "tok")
    monkeypatch.setattr(server._config, "region", "global01")
    monkeypatch.setattr(server._config, "org_id", "org-1")
    monkeypatch.setattr(server, "_client", FakeClient())


def test_get_nac_clients(monkeypatch):
    _prime(monkeypatch)
    out = server.tool_get_nac_clients(auth_type="eap-tls")
    assert out["count"] == 1
    c = out["nac_clients"][0]
    assert c["username"] == "bob" and c["nac_rule"] == "Employees" and c["status"] == "Permitted"
    assert c["last_seen"].startswith("20")


def test_troubleshoot_authentication_flags_failures(monkeypatch):
    _prime(monkeypatch)
    out = server.tool_troubleshoot_authentication()
    assert out["event_count"] == 2
    assert out["failure_count"] == 1
    assert out["failures"][0]["type"] == "NAC_CLIENT_DENY"
    assert out["failures"][0]["reason"] == "no matching rule"
    assert out["event_types"]["NAC_CLIENT_PERMIT"] == 1

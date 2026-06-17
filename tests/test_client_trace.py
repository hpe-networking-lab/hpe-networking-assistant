"""Tests for client search/trace (Mist client search + find_client/trace_client tools)."""

import json

import pytest

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


def test_search_clients_extracts_results(monkeypatch):
    payload = {"results": [{"mac": "aa", "hostname": "laptop", "site_id": "s1"}], "total": 1}
    monkeypatch.setattr(mist_client.urllib.request, "urlopen",
                        router({"/clients/search": payload}))
    c = MistClient(token="t", region="global01")
    out = c.search_clients("org-1", hostname="laptop")
    assert len(out) == 1 and out[0]["mac"] == "aa"


def test_search_client_events_extracts_results(monkeypatch):
    payload = {"results": [{"type": "MARVIS_EVENT_CLIENT_DHCP_FAILURE", "timestamp": 1700000000}]}
    monkeypatch.setattr(mist_client.urllib.request, "urlopen",
                        router({"/clients/events/search": payload}))
    c = MistClient(token="t", region="global01")
    out = c.search_client_events("org-1", "aa:bb:cc")
    assert out[0]["type"].endswith("DHCP_FAILURE")


# -- tool-level (mocked client) ----------------------------------------------

class FakeClient:
    def get_sites(self, org_id):
        return [{"id": "s1", "name": "HQ"}]

    def search_clients(self, org_id, mac=None, hostname=None, duration="1d", limit=100):
        return [{"mac": "aa", "hostname": "laptop", "ssid": "Corp", "ap": "ap1",
                 "ip": "10.0.0.5", "band": "5", "rssi": -55, "site_id": "s1",
                 "last_seen": 1700000000}]

    def search_client_events(self, org_id, mac, duration="1d", limit=100):
        return [
            {"type": "CLIENT_ASSOCIATED", "timestamp": 1700000000, "ssid": "Corp", "ap": "ap1"},
            {"type": "CLIENT_AUTH_FAILURE", "timestamp": 1700000100, "reason": "bad psk"},
        ]


def _prime(monkeypatch):
    monkeypatch.setattr(server._config, "token", "tok")
    monkeypatch.setattr(server._config, "region", "global01")
    monkeypatch.setattr(server._config, "org_id", "org-1")
    monkeypatch.setattr(server, "_client", FakeClient())


def test_find_client_requires_query(monkeypatch):
    _prime(monkeypatch)
    out = server.tool_find_client()
    assert "error" in out


def test_find_client_returns_location(monkeypatch):
    _prime(monkeypatch)
    out = server.tool_find_client(hostname="laptop")
    assert out["count"] == 1
    c = out["clients"][0]
    assert c["site"] == "HQ" and c["ssid"] == "Corp" and c["ap_mac"] == "ap1"
    assert c["last_seen"].startswith("20")  # epoch formatted to a date string


def test_trace_client_summarizes_events(monkeypatch):
    _prime(monkeypatch)
    out = server.tool_trace_client(mac="aa")
    assert out["event_count"] == 2
    assert out["event_types"]["CLIENT_AUTH_FAILURE"] == 1
    assert any("AUTH_FAILURE" in (f["type"] or "") for f in out["failures"])

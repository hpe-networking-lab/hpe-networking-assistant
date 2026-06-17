"""Tests for v1.10 tools: SLE summary and switch port stats."""

import json

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


def router(routes):
    def _f(req, timeout=None):
        for frag, payload in routes.items():
            if frag in req.full_url:
                return FakeResp(payload)
        raise AssertionError(req.full_url)
    return _f


def test_get_sites_sle_parse(monkeypatch):
    payload = {"results": [{"site_id": "s1", "ap-health": 0.5, "num_aps": 2}]}
    monkeypatch.setattr(mist_client.urllib.request, "urlopen", router({"/insights/sites-sle": payload}))
    out = MistClient(token="t", region="global01").get_sites_sle("o")
    assert out[0]["site_id"] == "s1"


def test_search_switch_ports_parse_data_key(monkeypatch):
    # ports search returns a "data" array (not "results") — must still parse
    payload = {"data": [{"mac": "sw1", "port_id": "ge-0/0/0", "up": True}], "total": 1}
    monkeypatch.setattr(mist_client.urllib.request, "urlopen", router({"/stats/ports/search": payload}))
    out = MistClient(token="t", region="global01").search_switch_ports("o")
    assert out[0]["port_id"] == "ge-0/0/0"


class FakeClient:
    def get_sites(self, org_id):
        return [{"id": "s1", "name": "HQ"}]

    def get_sites_sle(self, org_id):
        return [{"site_id": "s1", "ap-health": 0.4848, "ap-redundancy": 0, "num_aps": 1, "num_clients": 0}]

    def search_switch_ports(self, org_id, mac=None, site_id=None, limit=1000):
        return [
            {"mac": "sw1", "port_id": "ge-0/0/0", "up": True, "speed": 1000, "full_duplex": True,
             "poe_on": True, "neighbor_system_name": "AP-1", "neighbor_port_desc": "eth0",
             "rx_bps": 2776, "site_id": "s1"},
            {"mac": "sw1", "port_id": "ge-0/0/1", "up": False, "site_id": "s1"},
        ]


def _prime(monkeypatch):
    monkeypatch.setattr(server._config, "token", "tok")
    monkeypatch.setattr(server._config, "region", "global01")
    monkeypatch.setattr(server._config, "org_id", "org-1")
    monkeypatch.setattr(server, "_client", FakeClient())


def test_get_sle_tool(monkeypatch):
    _prime(monkeypatch)
    out = server.tool_get_sle()
    assert out["site_count"] == 1
    site = out["sites"][0]
    assert site["site"] == "HQ"
    assert site["sle"]["ap-health"] == "48%"        # 0.4848 -> 48%
    assert "num_aps" not in site["sle"]              # excluded from metric map


def test_switch_ports_tool(monkeypatch):
    _prime(monkeypatch)
    out = server.tool_get_switch_ports()
    assert out["count"] == 2 and out["up"] == 1
    p0 = out["ports"][0]
    assert p0["port"] == "ge-0/0/0" and p0["poe_on"] is True and p0["neighbor"] == "AP-1 eth0"


def test_switch_ports_up_filter(monkeypatch):
    _prime(monkeypatch)
    assert server.tool_get_switch_ports(up=False)["count"] == 1

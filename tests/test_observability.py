"""Tests for v1.9 observability tools: Marvis actions, alarms, wired clients."""

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


def test_search_wired_clients(monkeypatch):
    payload = {"results": [{"mac": "aa", "last_hostname": "PC1", "last_port_id": "ge-0/0/1"}]}
    monkeypatch.setattr(mist_client.urllib.request, "urlopen", router({"/wired_clients/search": payload}))
    out = MistClient(token="t", region="global01").search_wired_clients("o")
    assert out[0]["last_port_id"] == "ge-0/0/1"


def test_search_alarms(monkeypatch):
    payload = {"results": [{"type": "switch_offline", "severity": "critical"}]}
    monkeypatch.setattr(mist_client.urllib.request, "urlopen", router({"/alarms/search": payload}))
    out = MistClient(token="t", region="global01").search_alarms("o", severity="critical")
    assert out[0]["type"] == "switch_offline"


def test_get_marvis_actions_list_and_dict(monkeypatch):
    # bare list response
    monkeypatch.setattr(mist_client.urllib.request, "urlopen",
                        router({"/suggestion": [{"symptom": "sw_offline"}]}))
    assert MistClient(token="t", region="global01").get_marvis_actions("o")[0]["symptom"] == "sw_offline"
    # dict-with-results response
    monkeypatch.setattr(mist_client.urllib.request, "urlopen",
                        router({"/suggestion": {"results": [{"symptom": "non_compliant"}]}}))
    assert MistClient(token="t", region="global01").get_marvis_actions("o")[0]["symptom"] == "non_compliant"


class FakeClient:
    def get_marvis_actions(self, org_id):
        return [
            {"category": "switch", "symptom": "sw_offline", "suggestion": "check_switch_offline",
             "status": "open", "severity": 60, "display_entity_type": "switch", "site_id": "s1",
             "start_time": 1781658080000, "details": {"impacted_tuple": [{"entity_name": "LAB-SW-01"}]}},
            {"category": "gateway", "symptom": "non_compliant", "status": "validated",
             "details": {"marvis_action": "upgrade_gw_firmware"}},
        ]

    def search_alarms(self, org_id, severity=None, duration="1d", limit=100):
        return [{"type": "switch_offline", "severity": "critical", "timestamp": 1781658080},
                {"type": "device_restarted", "severity": "warn", "timestamp": 1781658090}]

    def search_wired_clients(self, org_id, mac=None, hostname=None, duration="1d", limit=100):
        return [{"mac": "aa", "last_hostname": "PC1", "last_ip": "10.0.0.5", "last_device_mac": "sw1",
                 "last_port_id": "ge-0/0/1", "last_vlan": 10, "last_vlan_name": "data",
                 "manufacture": "Dell", "site_id": "s1", "timestamp": 1781690938.4}]


def _prime(monkeypatch):
    monkeypatch.setattr(server._config, "token", "tok")
    monkeypatch.setattr(server._config, "region", "global01")
    monkeypatch.setattr(server._config, "org_id", "org-1")
    monkeypatch.setattr(server, "_client", FakeClient())


def test_marvis_actions_tool(monkeypatch):
    _prime(monkeypatch)
    out = server.tool_get_marvis_actions()
    assert out["count"] == 2 and out["open_count"] == 1
    assert out["by_category"]["switch"] == 1
    a0 = out["actions"][0]
    assert a0["entity"] == "LAB-SW-01" and a0["since"].startswith("20")  # ms epoch formatted


def test_marvis_actions_status_filter(monkeypatch):
    _prime(monkeypatch)
    out = server.tool_get_marvis_actions(status="open")
    assert out["count"] == 1 and out["actions"][0]["symptom"] == "sw_offline"


def test_alarms_tool(monkeypatch):
    _prime(monkeypatch)
    out = server.tool_get_alarms()
    assert out["count"] == 2
    assert out["by_severity"]["critical"] == 1 and out["by_type"]["device_restarted"] == 1


def test_wired_clients_tool(monkeypatch):
    _prime(monkeypatch)
    out = server.tool_get_wired_clients()
    assert out["count"] == 1
    c = out["wired_clients"][0]
    assert c["switch_mac"] == "sw1" and c["port"] == "ge-0/0/1" and c["vlan_name"] == "data"


def test_wired_clients_site_filter(monkeypatch):
    _prime(monkeypatch)
    assert server.tool_get_wired_clients(site_id="other")["count"] == 0

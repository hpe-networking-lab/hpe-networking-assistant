"""Tests for the network topology builder and generate_topology tool."""

from hpe_mist_mcp import server
from hpe_mist_mcp.topology import build_topology, render_topology_mermaid


class FakeClient:
    def get_access_points(self, org_id):
        return [{"mac": "ap1", "name": "AP-Lobby", "site_id": "s1"},
                {"mac": "ap2", "name": "AP-Floor2", "site_id": "s2"}]  # other site

    def get_switches(self, org_id):
        return [{"mac": "sw1", "name": "Core-SW", "site_id": "s1"}]

    def get_org_inventory(self, org_id, device_type=None):
        if device_type == "gateway":
            return [{"mac": "gw1", "name": "Edge-GW", "site_id": "s1"}]
        return []

    def get_sites(self, org_id):
        return [{"id": "s1", "name": "HQ"}, {"id": "s2", "name": "Branch"}]

    def search_switch_ports(self, org_id, mac=None, site_id=None, limit=1000):
        return [
            {"mac": "sw1", "port_id": "ge-0/0/0", "neighbor_system_name": "Edge-GW"},   # -> gw1
            {"mac": "sw1", "port_id": "ge-0/0/3", "neighbor_mac": "ap1"},                # -> ap1
            {"mac": "sw1", "port_id": "ge-0/0/9", "neighbor_system_name": "RT-AC68U"},   # external
        ]

    def get_site_clients(self, site_id):
        return [{"mac": "c1", "hostname": "Laptop", "ap_mac": "ap1", "ssid": "Corp"}]


def test_build_topology_nodes_edges():
    data = build_topology(FakeClient(), "org-1", "s1", site_name="HQ")
    types = sorted(n["type"] for n in data["nodes"])
    assert types == ["ap", "external", "gateway", "switch"]   # ap2 (other site) excluded
    assert data["edge_count"] == 3
    assert "graph TD" in data["mermaid"]
    assert "Edge-GW" in data["mermaid"] and "RT-AC68U" in data["mermaid"]


def test_topology_edge_orientation():
    data = build_topology(FakeClient(), "org-1", "s1", site_name="HQ")
    ids = {n["mac"]: n["id"] for n in data["nodes"]}
    # gateway -> switch should be directed from the gateway (higher tier)
    gw_sw = [e for e in data["edges"] if {e["from"], e["to"]} == {ids["gw1"], ids["sw1"]}][0]
    assert gw_sw["from"] == ids["gw1"] and gw_sw["directed"] is True


def test_topology_include_clients():
    data = build_topology(FakeClient(), "org-1", "s1", site_name="HQ", include_clients=True)
    assert any(n["type"] == "client" and n["label"] == "Laptop" for n in data["nodes"])


def test_topology_tool_site_by_name(monkeypatch):
    monkeypatch.setattr(server._config, "token", "tok")
    monkeypatch.setattr(server._config, "region", "global01")
    monkeypatch.setattr(server._config, "org_id", "org-1")
    monkeypatch.setattr(server, "_client", FakeClient())
    out = server.tool_generate_topology(site="HQ")
    assert out["format"] == "mermaid" and out["site"] == "HQ"
    assert out["node_count"] == 4


def test_topology_tool_needs_site(monkeypatch):
    monkeypatch.setattr(server._config, "token", "tok")
    monkeypatch.setattr(server._config, "region", "global01")
    monkeypatch.setattr(server._config, "org_id", "org-1")
    monkeypatch.setattr(server, "_client", FakeClient())
    out = server.tool_generate_topology()   # multiple sites, none given
    assert out["status"] == "NEEDS INPUT" and set(out["sites"]) == {"HQ", "Branch"}

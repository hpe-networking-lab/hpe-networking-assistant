"""Network topology assembly for the HPE Networking Assistant.

Builds a per-site layer-2 / uplink topology from device inventory and switch-port
LLDP neighbor data, and renders it as a Mermaid diagram. Read-only.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .mist_client import MistClient, MistError

# Tier ranking for orienting edges top-down (gateway -> switch -> ap -> ...).
_RANK = {"gateway": 0, "switch": 1, "ap": 2, "external": 3, "client": 4}


def _lbl(value: Any) -> str:
    s = str(value if value is not None else "")
    for a, b in (('"', "'"), ("[", "("), ("]", ")"), ("|", "/"), ("\n", " ")):
        s = s.replace(a, b)
    return s[:40] or "?"


def build_topology(
    client: MistClient, org_id: str, site_id: str, site_name: Optional[str] = None,
    include_clients: bool = False,
) -> Dict[str, Any]:
    """Assemble nodes and edges for one site's wired/uplink topology."""
    aps = [d for d in client.get_access_points(org_id) if d.get("site_id") == site_id]
    switches = [d for d in client.get_switches(org_id) if d.get("site_id") == site_id]
    try:
        gateways = [d for d in client.get_org_inventory(org_id, "gateway")
                    if d.get("site_id") == site_id]
    except MistError:
        gateways = []

    nodes: Dict[str, Dict[str, Any]] = {}

    def add(devs: List[Dict[str, Any]], typ: str) -> None:
        for d in devs:
            mac = d.get("mac")
            if mac and mac not in nodes:
                nodes[mac] = {"mac": mac, "type": typ, "label": d.get("name") or mac}

    add(gateways, "gateway")
    add(switches, "switch")
    add(aps, "ap")
    name_to_mac = {n["label"].lower(): m for m, n in nodes.items()}

    try:
        ports = client.search_switch_ports(org_id, site_id=site_id)
    except MistError:
        ports = []

    edges: Dict[Any, Dict[str, Any]] = {}
    for p in ports:
        sw = p.get("mac")
        if sw not in nodes:
            continue
        nb_mac = (p.get("neighbor_mac") or "").lower() or None
        nb_name = p.get("neighbor_system_name")
        if not (nb_mac or nb_name):
            continue
        if nb_mac and nb_mac in nodes:
            dst = nb_mac
        elif nb_name and nb_name.lower() in name_to_mac:
            dst = name_to_mac[nb_name.lower()]
        else:
            dst = "ext:" + (nb_name or nb_mac)
            if dst not in nodes:
                nodes[dst] = {"mac": dst, "type": "external", "label": nb_name or nb_mac}
        if sw == dst:
            continue
        key = tuple(sorted((sw, dst)))
        edges.setdefault(key, {"a": sw, "b": dst, "port": p.get("port_id")})

    if include_clients:
        try:
            clients = client.get_site_clients(site_id)
        except MistError:
            clients = []
        for c in clients[:200]:
            apm = c.get("ap_mac")
            if apm in nodes:
                cid = "cli:" + (c.get("mac") or c.get("hostname") or "")
                nodes.setdefault(cid, {"mac": cid, "type": "client",
                                       "label": c.get("hostname") or c.get("mac") or "client"})
                edges.setdefault(tuple(sorted((apm, cid))),
                                 {"a": apm, "b": cid, "port": c.get("ssid")})

    ids = {mac: f"n{i}" for i, mac in enumerate(nodes)}
    node_list = [
        {"id": ids[m], "mac": m, "type": n["type"], "label": n["label"]}
        for m, n in nodes.items()
    ]
    edge_list = []
    for e in edges.values():
        a, b = e["a"], e["b"]
        # orient from higher tier (lower rank) to lower tier for a clean top-down tree
        if _RANK.get(nodes[a]["type"], 9) <= _RANK.get(nodes[b]["type"], 9):
            src, dst = a, b
        else:
            src, dst = b, a
        directed = nodes[a]["type"] != nodes[b]["type"]
        edge_list.append({"from": ids[src], "to": ids[dst], "port": e["port"], "directed": directed})

    return {
        "org_id": org_id, "site_id": site_id, "site": site_name,
        "node_count": len(node_list), "edge_count": len(edge_list),
        "nodes": node_list, "edges": edge_list,
        "mermaid": render_topology_mermaid(node_list, edge_list),
    }


def render_topology_mermaid(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> str:
    out = [
        "graph TD",
        "  classDef gateway fill:#e7eefb,stroke:#1F4E79,color:#0c2f52;",
        "  classDef switch fill:#e3f6ec,stroke:#1b7a48,color:#0b3d24;",
        "  classDef ap fill:#fdf0df,stroke:#a5670f,color:#5a3608;",
        "  classDef external fill:#f1f1f1,stroke:#888888,color:#333333,stroke-dasharray:3 3;",
        "  classDef client fill:#ffffff,stroke:#bbbbbb,color:#555555;",
    ]
    tag = {"gateway": "GW", "switch": "SW", "ap": "AP", "external": "EXT", "client": "C"}
    for n in nodes:
        out.append(f'  {n["id"]}["{tag.get(n["type"], "?")}: {_lbl(n["label"])}"]:::{n["type"]}')
    for e in edges:
        arrow = "-->" if e["directed"] else "---"
        if e["port"]:
            out.append(f'  {e["from"]} {arrow}|{_lbl(e["port"])}| {e["to"]}')
        else:
            out.append(f'  {e["from"]} {arrow} {e["to"]}')
    return "\n".join(out)

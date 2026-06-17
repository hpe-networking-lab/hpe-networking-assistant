"""Read-only report generation for the HPE Networking Assistant.

Builds structured report data from the Mist client and renders it to Markdown.
No Mist configuration is changed; reports are assembled purely from GET data.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from .mist_client import MistClient, MistError


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """Render a GitHub-flavored Markdown table (or a placeholder if empty)."""
    if not rows:
        return "_None._"
    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        cells = ["" if c is None else str(c) for c in row]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Health report
# ---------------------------------------------------------------------------

def build_health_report(
    client: MistClient, org_id: str, org_name: Optional[str] = None,
    include_clients: bool = True,
) -> Dict[str, Any]:
    """Assemble an organization health/status report."""
    sites = client.get_sites(org_id)
    site_name = {s.get("id"): s.get("name") for s in sites}
    aps = client.get_access_points(org_id)
    switches = client.get_switches(org_id)

    ap_online = sum(1 for a in aps if a.get("connected") is True)
    sw_online = sum(1 for s in switches if s.get("connected") is True)
    offline = [a for a in aps if a.get("connected") is False]

    per_site: Dict[Any, Dict[str, int]] = {}

    def bucket(sid: Any) -> Dict[str, int]:
        return per_site.setdefault(
            sid, {"aps": 0, "aps_online": 0, "switches": 0, "switches_online": 0}
        )

    for a in aps:
        b = bucket(a.get("site_id"))
        b["aps"] += 1
        if a.get("connected") is True:
            b["aps_online"] += 1
    for s in switches:
        b = bucket(s.get("site_id"))
        b["switches"] += 1
        if s.get("connected") is True:
            b["switches_online"] += 1

    client_count: Optional[int] = None
    if include_clients:
        try:
            client_count = len(client.get_org_clients(org_id))
        except MistError:
            client_count = None

    return {
        "generated_at": _now(),
        "org_id": org_id,
        "org_name": org_name,
        "totals": {
            "sites": len(sites),
            "access_points": len(aps),
            "access_points_online": ap_online,
            "access_points_offline": len(aps) - ap_online,
            "switches": len(switches),
            "switches_online": sw_online,
            "switches_offline": len(switches) - sw_online,
            "wireless_clients": client_count,
        },
        "offline_access_points": [
            {
                "name": a.get("name"),
                "mac": a.get("mac"),
                "model": a.get("model"),
                "site": site_name.get(a.get("site_id"), a.get("site_id")),
            }
            for a in offline
        ],
        "per_site": [
            {"site": site_name.get(sid, sid), **vals} for sid, vals in per_site.items()
        ],
    }


def render_health_markdown(data: Dict[str, Any]) -> str:
    t = data["totals"]
    title = data.get("org_name") or data.get("org_id")
    clients = t["wireless_clients"]
    lines: List[str] = [
        f"# Network Health Report — {title}",
        "",
        f"_Generated {data['generated_at']}_",
        "",
        "## Summary",
        "",
        _table(
            ["Metric", "Value"],
            [
                ["Sites", t["sites"]],
                ["Access points",
                 f"{t['access_points']} ({t['access_points_online']} online, "
                 f"{t['access_points_offline']} offline)"],
                ["Switches",
                 f"{t['switches']} ({t['switches_online']} online, "
                 f"{t['switches_offline']} offline)"],
                ["Wireless clients", clients if clients is not None else "not collected"],
            ],
        ),
        "",
        "## Offline access points",
        "",
    ]
    if data["offline_access_points"]:
        rows = [
            [a["name"] or "—", a["mac"], a["model"] or "—", a["site"] or "—"]
            for a in data["offline_access_points"]
        ]
        lines.append(_table(["Name", "MAC", "Model", "Site"], rows))
    else:
        lines.append("All access points are online.")

    lines += ["", "## Per-site breakdown", ""]
    rows = [
        [s["site"] or "—", f"{s['aps_online']}/{s['aps']}",
         f"{s['switches_online']}/{s['switches']}"]
        for s in data["per_site"]
    ]
    lines.append(_table(["Site", "APs online/total", "Switches online/total"], rows))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Inventory report
# ---------------------------------------------------------------------------

def build_inventory_report(
    client: MistClient, org_id: str, org_name: Optional[str] = None
) -> Dict[str, Any]:
    """Assemble a full device inventory report (APs and switches)."""
    sites = client.get_sites(org_id)
    site_name = {s.get("id"): s.get("name") for s in sites}
    aps = client.get_access_points(org_id)
    switches = client.get_switches(org_id)

    def row(d: Dict[str, Any], device_type: str) -> Dict[str, Any]:
        return {
            "name": d.get("name"),
            "type": device_type,
            "model": d.get("model"),
            "serial": d.get("serial"),
            "mac": d.get("mac"),
            "site": site_name.get(d.get("site_id"), d.get("site_id")),
            "status": "online" if d.get("connected") is True else "offline",
            "version": d.get("version"),
        }

    devices = [row(a, "AP") for a in aps] + [row(s, "switch") for s in switches]
    return {
        "generated_at": _now(),
        "org_id": org_id,
        "org_name": org_name,
        "device_count": len(devices),
        "devices": devices,
    }


def build_firmware_report(
    client: MistClient, org_id: str, org_name: Optional[str] = None
) -> Dict[str, Any]:
    """Assess firmware consistency: per model, the version most of the fleet runs
    (the 'target') and which devices are behind it."""
    aps = client.get_access_points(org_id)
    switches = client.get_switches(org_id)

    groups: Dict[Any, List[Dict[str, Any]]] = {}
    for devs, typ in ((aps, "AP"), (switches, "switch")):
        for d in devs:
            groups.setdefault((typ, d.get("model") or "unknown"), []).append(d)

    models = []
    non_compliant = 0
    for (typ, model), devs in sorted(groups.items()):
        versions = [d.get("version") or "unknown" for d in devs]
        counts = Counter(versions)
        target = counts.most_common(1)[0][0]
        behind = [d for d in devs if (d.get("version") or "unknown") != target]
        non_compliant += len(behind)
        models.append({
            "type": typ,
            "model": model,
            "count": len(devs),
            "target_version": target,
            "versions": dict(counts),
            "on_target": len(devs) - len(behind),
            "behind": [
                {"name": d.get("name"), "mac": d.get("mac"),
                 "version": d.get("version") or "unknown", "site_id": d.get("site_id")}
                for d in behind
            ],
        })

    return {
        "generated_at": _now(),
        "org_id": org_id,
        "org_name": org_name,
        "device_count": sum(len(v) for v in groups.values()),
        "non_compliant_count": non_compliant,
        "models": models,
    }


def render_firmware_markdown(data: Dict[str, Any]) -> str:
    title = data.get("org_name") or data.get("org_id")
    lines = [
        f"# Firmware Compliance Report — {title}",
        "",
        f"_Generated {data['generated_at']} · {data['device_count']} device(s), "
        f"{data['non_compliant_count']} behind the fleet target_",
        "",
        "## By model",
        "",
        _table(
            ["Type", "Model", "Devices", "Target version", "On target", "Behind"],
            [
                [m["type"], m["model"], m["count"], m["target_version"], m["on_target"],
                 len(m["behind"])]
                for m in data["models"]
            ],
        ),
    ]
    behind_rows = [
        [d["name"] or "—", d["mac"], m["model"], d["version"], m["target_version"]]
        for m in data["models"] for d in m["behind"]
    ]
    lines += ["", "## Devices behind the target version", ""]
    if behind_rows:
        lines.append(_table(["Name", "MAC", "Model", "Current", "Target"], behind_rows))
    else:
        lines.append("All devices match their model's fleet version.")
    return "\n".join(lines)


def render_inventory_markdown(data: Dict[str, Any]) -> str:
    title = data.get("org_name") or data.get("org_id")
    lines = [
        f"# Inventory Report — {title}",
        "",
        f"_Generated {data['generated_at']} · {data['device_count']} device(s)_",
        "",
        _table(
            ["Name", "Type", "Model", "Serial", "MAC", "Site", "Status", "Version"],
            [
                [d["name"] or "—", d["type"], d["model"] or "—", d["serial"] or "—",
                 d["mac"], d["site"] or "—", d["status"], d["version"] or "—"]
                for d in data["devices"]
            ],
        ),
    ]
    return "\n".join(lines)

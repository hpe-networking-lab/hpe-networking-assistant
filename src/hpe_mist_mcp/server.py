"""HPE Networking Assistant MCP server (zero-dependency).

Implements the Model Context Protocol stdio transport (JSON-RPC 2.0, one JSON
object per line) using only the Python standard library. This keeps the
packaged Claude Desktop extension small and fully cross-platform — there are
no compiled dependencies to bundle per operating system.

The server is read-only by default. Write tools are registered only when
write mode is explicitly enabled, and each one requires confirm=true.
"""

from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from . import __version__
from .config import load_config, save_discovery
from .discovery import discover_region, region_label
from .mist_client import MistClient, MistError
from .nac_visualizer import build_nac_overview, render_nac_dashboard_html
from .reports import (
    build_firmware_report,
    build_health_report,
    build_inventory_report,
    render_firmware_markdown,
    render_health_markdown,
    render_inventory_markdown,
)
from .topology import build_topology
from .validation import is_write_role, run_validation

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,  # stdout is reserved for the MCP transport
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("hpe-mist-mcp")

SERVER_NAME = "HPE Networking Assistant"
SUPPORTED_PROTOCOL = "2025-06-18"

INSTRUCTIONS = (
    "This server connects Claude to a customer's Juniper Mist network.\n"
    "FIRST-RUN ONBOARDING: If the user is new or asks to set up / get started, "
    "call the `start_setup` tool. It automatically detects the Mist region from "
    "the API token, discovers organizations and sites, runs validation tests, "
    "and returns a READY FOR USE report.\n"
    "The user never needs to know their Org ID, Site ID, or API endpoint — always "
    "resolve these via the tools, and refer to organizations and sites by name. "
    "If `start_setup` reports multiple organizations, ask the user which one to use "
    "BY NAME, then call start_setup(organization=\"<name>\").\n"
    "Use `get_status` to report the current mode (read-only vs read-write), region, "
    "and organization. If write mode is enabled but the token lacks write access, "
    "relay the returned guidance: the customer must create a token under an account "
    "with a write role and update it in the extension Settings (the token cannot be "
    "elevated via the API)."
)

# Shown when write mode is enabled but the token cannot write.
WRITE_TOKEN_GUIDANCE = [
    "A Mist API token cannot be upgraded to read-write — it keeps the role of the "
    "account that created it. To make changes:",
    "1. In Mist, make sure you have (or ask an admin for) an account role that allows "
    "configuration changes, such as Network Admin / Super User.",
    "2. Under that account, create a new token: My Account > API Token > Create Token.",
    "3. In Claude Desktop: Settings > Extensions > HPE Networking Assistant. Replace the "
    "API Token with the new one and keep 'Enable write operations' turned on.",
    "4. Ask me to 'check my setup' again to confirm READY FOR USE.",
]

_client: Optional[MistClient] = None
_config = load_config()


def _ensure_region(token: Optional[str] = None) -> str:
    """Resolve the Mist region, auto-detecting from the token when unknown."""
    if not _config.region:
        token = token or _config.require_token()
        region = discover_region(token)
        _config.region = region
        try:
            save_discovery(region=region)
        except Exception:  # persistence is best-effort
            log.warning("Could not persist detected region", exc_info=True)
        log.info("Auto-detected Mist region: %s", region)
    return _config.region


def client() -> MistClient:
    """Lazily build (and cache) the Mist client from configuration."""
    global _client
    if _client is None:
        token = _config.require_token()
        region = _ensure_region(token)
        _client = MistClient(
            token=token,
            region=region,
            read_only=not _config.write_enabled,
        )
        log.info(
            "Initialized Mist client for region %s (mode=%s)",
            region,
            "read-write" if _config.write_enabled else "read-only",
        )
    return _client


def _require_confirm(confirm: Any) -> None:
    """Guard write tools: nothing happens unless confirm is explicitly true."""
    if confirm is not True:
        raise ValueError(
            "This action changes your Mist configuration and was not performed. "
            "Re-run with confirm=true to proceed."
        )


def _org_name(org_id: str) -> Optional[str]:
    """Best-effort lookup of an organization's name by id."""
    try:
        return next(
            (o.get("name") for o in client().get_organizations() if o.get("org_id") == org_id),
            None,
        )
    except MistError:
        return None


def _looks_like_uuid(value: str) -> bool:
    return isinstance(value, str) and len(value) == 36 and value.count("-") == 4


def _find_org_by_name(value: str) -> Optional[str]:
    """Resolve an organization name (exact, else unique substring) to its id."""
    try:
        orgs = client().get_organizations()
    except MistError:
        return None
    v = value.strip().lower()
    exact = [o for o in orgs if (o.get("name") or "").lower() == v]
    if exact:
        return exact[0]["org_id"]
    partial = [o for o in orgs if v in (o.get("name") or "").lower()]
    return partial[0]["org_id"] if len(partial) == 1 else None


def _resolve_org(org_id: Optional[str]) -> str:
    """Resolve an org id from the argument, configuration, or single-org access.

    The argument may be an org id or an organization name (resolved by name).
    """
    if org_id:
        if _looks_like_uuid(org_id):
            return org_id
        return _find_org_by_name(org_id) or org_id
    if _config.org_id:
        return _config.org_id
    orgs = client().get_organizations()
    if len(orgs) == 1:
        return orgs[0]["org_id"]
    names = ", ".join(f"{o.get('name')} ({o['org_id']})" for o in orgs) or "none found"
    raise ValueError(
        "Multiple organizations are accessible and no org_id was provided. "
        f"Specify one of: {names}."
    )


# ---------------------------------------------------------------------------
# Field projections — keep responses compact and useful for the model.
# ---------------------------------------------------------------------------

def _ap_summary(d: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "mac": d.get("mac"),
        "model": d.get("model"),
        "serial": d.get("serial"),
        "name": d.get("name"),
        "site_id": d.get("site_id"),
        "connected": d.get("connected"),
        "version": d.get("version"),
    }


def _switch_summary(d: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "mac": d.get("mac"),
        "model": d.get("model"),
        "serial": d.get("serial"),
        "name": d.get("name"),
        "site_id": d.get("site_id"),
        "connected": d.get("connected"),
        "version": d.get("version"),
    }


def _site_summary(d: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": d.get("id"),
        "name": d.get("name"),
        "country_code": d.get("country_code"),
        "timezone": d.get("timezone"),
        "address": d.get("address"),
    }


def _client_summary(d: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "mac": d.get("mac"),
        "hostname": d.get("hostname"),
        "username": d.get("username"),
        "ip": d.get("ip"),
        "ssid": d.get("ssid"),
        "ap_mac": d.get("ap_mac"),
        "site_id": d.get("site_id"),
        "site_name": d.get("site_name"),
        "os": d.get("os"),
        "manufacture": d.get("manufacture"),
        "rssi": d.get("rssi"),
    }


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def tool_get_organizations(**_: Any) -> Dict[str, Any]:
    orgs = client().get_organizations()
    return {"count": len(orgs), "organizations": orgs}


def tool_get_sites(org_id: Optional[str] = None, **_: Any) -> Dict[str, Any]:
    oid = _resolve_org(org_id)
    sites = client().get_sites(oid)
    return {"org_id": oid, "count": len(sites), "sites": [_site_summary(s) for s in sites]}


def tool_get_access_points(
    org_id: Optional[str] = None, site_id: Optional[str] = None, **_: Any
) -> Dict[str, Any]:
    oid = _resolve_org(org_id)
    aps = client().get_access_points(oid)
    if site_id:
        aps = [a for a in aps if a.get("site_id") == site_id]
    connected = sum(1 for a in aps if a.get("connected") is True)
    return {
        "org_id": oid,
        "site_id": site_id,
        "count": len(aps),
        "connected": connected,
        "offline": len(aps) - connected,
        "access_points": [_ap_summary(a) for a in aps],
    }


def tool_get_switches(
    org_id: Optional[str] = None, site_id: Optional[str] = None, **_: Any
) -> Dict[str, Any]:
    oid = _resolve_org(org_id)
    switches = client().get_switches(oid)
    if site_id:
        switches = [s for s in switches if s.get("site_id") == site_id]
    connected = sum(1 for s in switches if s.get("connected") is True)
    return {
        "org_id": oid,
        "site_id": site_id,
        "count": len(switches),
        "connected": connected,
        "offline": len(switches) - connected,
        "switches": [_switch_summary(s) for s in switches],
    }


def tool_get_clients(
    org_id: Optional[str] = None, site_id: Optional[str] = None, **_: Any
) -> Dict[str, Any]:
    oid = _resolve_org(org_id)
    c = client()
    if site_id:
        raw = c.get_site_clients(site_id)
        for item in raw:
            item.setdefault("site_id", site_id)
    else:
        raw = c.get_org_clients(oid)
    return {
        "org_id": oid,
        "site_id": site_id,
        "count": len(raw),
        "clients": [_client_summary(x) for x in raw],
    }


def tool_get_offline_access_points(org_id: Optional[str] = None, **_: Any) -> Dict[str, Any]:
    oid = _resolve_org(org_id)
    offline = client().get_offline_access_points(oid)
    return {
        "org_id": oid,
        "offline_count": len(offline),
        "offline_access_points": [_ap_summary(a) for a in offline],
    }


def tool_generate_health_report(
    org_id: Optional[str] = None, include_clients: bool = True, **_: Any
) -> Dict[str, Any]:
    """Generate a network health report (Markdown) for an organization."""
    try:
        oid = _resolve_org(org_id)
        data = build_health_report(
            client(), oid, org_name=_org_name(oid), include_clients=include_clients
        )
        return {
            "format": "markdown",
            "generated_at": data["generated_at"],
            "summary": data["totals"],
            "markdown": render_health_markdown(data),
        }
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def tool_generate_inventory_report(org_id: Optional[str] = None, **_: Any) -> Dict[str, Any]:
    """Generate a full device inventory report (Markdown) for an organization."""
    try:
        oid = _resolve_org(org_id)
        data = build_inventory_report(client(), oid, org_name=_org_name(oid))
        return {
            "format": "markdown",
            "generated_at": data["generated_at"],
            "device_count": data["device_count"],
            "markdown": render_inventory_markdown(data),
        }
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def _fmt_ts(ts: Any) -> Any:
    """Format an epoch timestamp as readable UTC; pass through if not numeric."""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    except (TypeError, ValueError):
        return ts


def _client_search_summary(r: Dict[str, Any], sites: Dict[Any, Any]) -> Dict[str, Any]:
    return {
        "mac": r.get("mac"),
        "hostname": r.get("hostname"),
        "username": r.get("username"),
        "ssid": r.get("ssid"),
        "ap_mac": r.get("ap") or r.get("last_ap"),
        "ip": r.get("ip"),
        "band": r.get("band"),
        "rssi": r.get("rssi"),
        "site": sites.get(r.get("site_id"), r.get("site_id")),
        "last_seen": _fmt_ts(r.get("last_seen") or r.get("timestamp")),
    }


def _event_summary(e: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "time": _fmt_ts(e.get("timestamp")),
        "type": e.get("type"),
        "ssid": e.get("ssid"),
        "ap": e.get("ap"),
        "band": e.get("band"),
        "rssi": e.get("rssi"),
        "wlan_id": e.get("wlan_id"),
        "reason": e.get("reason") if e.get("reason") is not None else e.get("text"),
    }


def tool_find_client(
    mac: Optional[str] = None, hostname: Optional[str] = None,
    org_id: Optional[str] = None, duration: str = "1d", **_: Any,
) -> Dict[str, Any]:
    """Locate a wireless client by MAC or hostname (which site/AP/SSID, IP, signal)."""
    try:
        if not mac and not hostname:
            return {"error": "Provide a mac or hostname to search for."}
        oid = _resolve_org(org_id)
        c = client()
        results = c.search_clients(oid, mac=mac, hostname=hostname, duration=duration)
        sites = {s.get("id"): s.get("name") for s in c.get_sites(oid)}
        return {
            "org_id": oid,
            "query": {"mac": mac, "hostname": hostname, "duration": duration},
            "count": len(results),
            "clients": [_client_search_summary(r, sites) for r in results],
        }
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def tool_trace_client(
    mac: str, org_id: Optional[str] = None, duration: str = "1d", **_: Any,
) -> Dict[str, Any]:
    """Trace a client's recent connection events to troubleshoot connectivity/roaming."""
    try:
        oid = _resolve_org(org_id)
        events = client().search_client_events(oid, mac, duration=duration)
        type_counts = Counter(e.get("type") for e in events if e.get("type"))
        failures = [
            _event_summary(e) for e in events
            if "fail" in str(e.get("type", "")).lower()
            or e.get("reason") not in (None, "", 0)
        ]
        return {
            "org_id": oid,
            "mac": mac,
            "duration": duration,
            "event_count": len(events),
            "event_types": dict(type_counts),
            "failures": failures[:50],
            "events": [_event_summary(e) for e in events[:100]],
        }
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def _nac_client_summary(d: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "mac": d.get("mac"),
        "username": d.get("last_username") or d.get("username"),
        "type": d.get("type"),
        "auth_type": d.get("auth_type"),
        "ssid": d.get("last_ssid") or d.get("ssid"),
        "vlan": d.get("last_vlan") or d.get("vlan"),
        "nac_rule": d.get("last_nacrule_name") or d.get("nacrule_name"),
        "status": d.get("last_status") or d.get("status"),
        "mfa": d.get("mfa"),
        "last_seen": _fmt_ts(d.get("last_seen") or d.get("timestamp")),
    }


def _nac_event_summary(e: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "time": _fmt_ts(e.get("timestamp")),
        "type": e.get("type"),
        "mac": e.get("mac"),
        "username": e.get("username"),
        "auth_type": e.get("auth_type"),
        "nac_rule": e.get("nacrule_name") or e.get("last_nacrule_name"),
        "ssid": e.get("ssid"),
        "vlan": e.get("vlan"),
        "reason": e.get("text") if e.get("text") is not None else e.get("reason"),
    }


def _is_auth_failure(e: Dict[str, Any]) -> bool:
    t = str(e.get("type", "")).upper()
    return any(k in t for k in ("DENY", "DENIED", "FAIL", "REJECT", "ERROR"))


def tool_get_nac_clients(
    org_id: Optional[str] = None, mac: Optional[str] = None, auth_type: Optional[str] = None,
    type: Optional[str] = None, duration: str = "1d", **_: Any,
) -> Dict[str, Any]:
    """List Access Assurance (NAC) clients authenticated to the network."""
    try:
        oid = _resolve_org(org_id)
        rows = client().search_nac_clients(
            oid, mac=mac, type=type, auth_type=auth_type, duration=duration
        )
        return {
            "org_id": oid,
            "duration": duration,
            "count": len(rows),
            "nac_clients": [_nac_client_summary(r) for r in rows],
        }
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def tool_troubleshoot_authentication(
    org_id: Optional[str] = None, mac: Optional[str] = None, user: Optional[str] = None,
    duration: str = "1d", **_: Any,
) -> Dict[str, Any]:
    """Diagnose 802.1X/MAB authentication via Access Assurance (NAC) events.

    Focus on one identity with ``mac`` (client MAC) and/or ``user`` (free-text
    match on username / certificate CN). Returns the auth-event timeline,
    per-type counts, and highlighted failures (denied/rejected/errored) so you
    can see why a specific user or device failed authentication.
    """
    try:
        oid = _resolve_org(org_id)
        events = client().search_nac_events(oid, mac=mac, text=user, duration=duration)
        type_counts = Counter(e.get("type") for e in events if e.get("type"))
        failures = [_nac_event_summary(e) for e in events if _is_auth_failure(e)]
        return {
            "org_id": oid,
            "mac": mac,
            "user": user,
            "duration": duration,
            "event_count": len(events),
            "event_types": dict(type_counts),
            "failure_count": len(failures),
            "failures": failures[:50],
            "events": [_nac_event_summary(e) for e in events[:100]],
        }
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def tool_generate_topology(
    site: Optional[str] = None, org_id: Optional[str] = None,
    include_clients: bool = False, **_: Any,
) -> Dict[str, Any]:
    """Build a per-site network topology (Mermaid + structured nodes/edges).

    Gateways, switches and APs are linked using switch-port LLDP neighbors.
    Pass a site name or id; set include_clients=true to add connected clients as leaves.
    """
    try:
        oid = _resolve_org(org_id)
        sites = client().get_sites(oid)
        chosen = None
        if site:
            if _looks_like_uuid(site):
                chosen = next((s for s in sites if s.get("id") == site), None)
            if chosen is None:
                q = site.strip().lower()
                matches = [s for s in sites if q in (s.get("name") or "").lower()]
                if len(matches) == 1:
                    chosen = matches[0]
                elif len(matches) > 1:
                    return {"status": "NEEDS INPUT",
                            "message": f"More than one site matches '{site}'.",
                            "sites": [s.get("name") for s in matches]}
        elif len(sites) == 1:
            chosen = sites[0]
        if chosen is None:
            return {"status": "NEEDS INPUT",
                    "message": "Which site? Provide a site name.",
                    "sites": [s.get("name") for s in sites]}
        data = build_topology(client(), oid, chosen["id"], site_name=chosen.get("name"),
                              include_clients=include_clients)
        return {
            "format": "mermaid",
            "org_id": oid,
            "site": data["site"],
            "node_count": data["node_count"],
            "edge_count": data["edge_count"],
            "mermaid": data["mermaid"],
            "nodes": data["nodes"],
            "edges": data["edges"],
        }
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def tool_generate_firmware_report(org_id: Optional[str] = None, **_: Any) -> Dict[str, Any]:
    """Firmware compliance report: per model, the fleet's majority ('target') version
    and which APs/switches are behind it. Returns Markdown plus a structured summary."""
    try:
        oid = _resolve_org(org_id)
        data = build_firmware_report(client(), oid, org_name=_org_name(oid))
        return {
            "format": "markdown",
            "generated_at": data["generated_at"],
            "summary": {
                "device_count": data["device_count"],
                "non_compliant_count": data["non_compliant_count"],
                "models": [
                    {"type": m["type"], "model": m["model"], "target_version": m["target_version"],
                     "behind": len(m["behind"])}
                    for m in data["models"]
                ],
            },
            "markdown": render_firmware_markdown(data),
        }
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def tool_generate_nac_dashboard(
    org_id: Optional[str] = None, duration: str = "1d", **_: Any,
) -> Dict[str, Any]:
    """Build a self-contained HTML NAC (Access Assurance) dashboard for an org.

    Aggregates NAC clients and auth events into charts (auth types, client types,
    status, event types, top failing users/rules). Returns HTML the user can save
    as a .html file and open in a browser.
    """
    try:
        oid = _resolve_org(org_id)
        data = build_nac_overview(client(), oid, org_name=_org_name(oid), duration=duration)
        return {
            "format": "html",
            "generated_at": data["generated_at"],
            "summary": {
                "clients": data["client_count"],
                "events": data["event_count"],
                "failures": data["failure_count"],
            },
            "html": render_nac_dashboard_html(data),
        }
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def _fmt_ms(ts: Any) -> Any:
    """Format a timestamp that may be epoch milliseconds or seconds."""
    try:
        n = float(ts)
    except (TypeError, ValueError):
        return ts
    if n > 1e12:  # milliseconds
        n /= 1000.0
    return datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def _marvis_summary(a: Dict[str, Any]) -> Dict[str, Any]:
    details = a.get("details") or {}
    tuples = details.get("impacted_tuple") or []
    entity = None
    if tuples and isinstance(tuples[0], dict):
        entity = tuples[0].get("entity_name") or tuples[0].get("entity_id")
    return {
        "symptom": a.get("symptom"),
        "category": a.get("category"),
        "suggestion": a.get("suggestion") or details.get("marvis_action"),
        "status": a.get("status"),
        "severity": a.get("severity"),
        "entity_type": a.get("entity_type") or a.get("display_entity_type"),
        "entity": entity or a.get("display_entity_id"),
        "site_id": a.get("site_id"),
        "since": _fmt_ms(a.get("start_time")),
    }


def _alarm_summary(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "time": _fmt_ms(a.get("timestamp") or a.get("last_seen")),
        "type": a.get("type"),
        "severity": a.get("severity"),
        "group": a.get("group"),
        "count": a.get("count"),
        "site_id": a.get("site_id"),
        "acked": a.get("acked"),
    }


def _wired_summary(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "mac": r.get("mac"),
        "hostname": r.get("last_hostname"),
        "ip": r.get("last_ip"),
        "switch_mac": r.get("last_device_mac"),
        "port": r.get("last_port_id"),
        "vlan": r.get("last_vlan"),
        "vlan_name": r.get("last_vlan_name"),
        "manufacture": r.get("manufacture"),
        "site_id": r.get("site_id"),
        "last_seen": _fmt_ms(r.get("timestamp")),
    }


def tool_get_marvis_actions(
    org_id: Optional[str] = None, status: Optional[str] = None, **_: Any,
) -> Dict[str, Any]:
    """List Marvis (AI) suggested actions — what Mist thinks is wrong and how to fix it.

    Pass status="open" to show only active items (default: all).
    """
    try:
        oid = _resolve_org(org_id)
        actions = client().get_marvis_actions(oid)
        if status:
            actions = [a for a in actions if str(a.get("status", "")).lower() == status.lower()]
        return {
            "org_id": oid,
            "count": len(actions),
            "open_count": sum(1 for a in actions if a.get("status") == "open"),
            "by_category": dict(Counter(a.get("category") for a in actions if a.get("category"))),
            "actions": [_marvis_summary(a) for a in actions[:100]],
        }
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def tool_get_alarms(
    org_id: Optional[str] = None, severity: Optional[str] = None, duration: str = "1d", **_: Any,
) -> Dict[str, Any]:
    """List organization alarms over a window, with per-severity and per-type counts."""
    try:
        oid = _resolve_org(org_id)
        alarms = client().search_alarms(oid, severity=severity, duration=duration)
        return {
            "org_id": oid,
            "duration": duration,
            "count": len(alarms),
            "by_severity": dict(Counter(a.get("severity") for a in alarms if a.get("severity"))),
            "by_type": dict(Counter(a.get("type") for a in alarms if a.get("type"))),
            "alarms": [_alarm_summary(a) for a in alarms[:100]],
        }
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def tool_get_wired_clients(
    org_id: Optional[str] = None, site_id: Optional[str] = None, mac: Optional[str] = None,
    hostname: Optional[str] = None, duration: str = "1d", **_: Any,
) -> Dict[str, Any]:
    """List wired clients (devices on switch ports): switch, port, VLAN, IP, vendor."""
    try:
        oid = _resolve_org(org_id)
        rows = client().search_wired_clients(oid, mac=mac, hostname=hostname, duration=duration)
        if site_id:
            rows = [r for r in rows if r.get("site_id") == site_id]
        return {
            "org_id": oid,
            "site_id": site_id,
            "count": len(rows),
            "wired_clients": [_wired_summary(r) for r in rows],
        }
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def _sle_site_summary(s: Dict[str, Any]) -> Dict[str, Any]:
    skip = {"site_id", "num_aps", "num_clients", "org_id"}
    metrics = {
        k: f"{round(100 * v)}%"
        for k, v in s.items()
        if k not in skip and isinstance(v, (int, float))
    }
    return {
        "site_id": s.get("site_id"),
        "num_aps": s.get("num_aps"),
        "num_clients": s.get("num_clients"),
        "sle": metrics,
    }


def _port_summary(p: Dict[str, Any]) -> Dict[str, Any]:
    neighbor = " ".join(
        x for x in [p.get("neighbor_system_name"), p.get("neighbor_port_desc")] if x
    ) or None
    return {
        "switch_mac": p.get("mac"),
        "port": p.get("port_id"),
        "up": p.get("up"),
        "speed": p.get("speed"),
        "full_duplex": p.get("full_duplex"),
        "poe_on": p.get("poe_on"),
        "poe_power": p.get("poe_power"),
        "neighbor": neighbor,
        "rx_bps": p.get("rx_bps"),
        "tx_bps": p.get("tx_bps"),
        "site_id": p.get("site_id"),
    }


def tool_get_sle(org_id: Optional[str] = None, **_: Any) -> Dict[str, Any]:
    """Service Level Expectations (SLE) per site — Mist's user-experience scores.

    Returns each site's SLE metrics (e.g. ap-health, coverage, capacity,
    successful-connects, wan/switch/gateway health) as percentages.
    """
    try:
        oid = _resolve_org(org_id)
        c = client()
        rows = c.get_sites_sle(oid)
        names = {s.get("id"): s.get("name") for s in c.get_sites(oid)}
        sites = []
        for s in rows:
            d = _sle_site_summary(s)
            d["site"] = names.get(s.get("site_id"), s.get("site_id"))
            sites.append(d)
        return {"org_id": oid, "site_count": len(sites), "sites": sites}
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def tool_get_switch_ports(
    org_id: Optional[str] = None, switch_mac: Optional[str] = None,
    site_id: Optional[str] = None, up: Optional[bool] = None, **_: Any,
) -> Dict[str, Any]:
    """List switch/device port stats: link state, speed/duplex, PoE, neighbor, traffic.

    Filter by switch_mac (the switch), site_id, or up=true/false.
    """
    try:
        oid = _resolve_org(org_id)
        norm = switch_mac.lower().replace(":", "").replace("-", "") if switch_mac else None
        ports = client().search_switch_ports(oid, mac=norm, site_id=site_id)
        if up is not None:
            ports = [p for p in ports if p.get("up") is up]
        return {
            "org_id": oid,
            "switch_mac": switch_mac,
            "site_id": site_id,
            "count": len(ports),
            "up": sum(1 for p in ports if p.get("up") is True),
            "ports": [_port_summary(p) for p in ports[:200]],
        }
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def tool_set_active_org(org: str, **_: Any) -> Dict[str, Any]:
    """Set the default organization for this session by name or id (multi-org tokens)."""
    try:
        if not org:
            return {"error": "Provide an organization name or id."}
        orgs = client().get_organizations()
        if _looks_like_uuid(org):
            oid = org
            name = next((o.get("name") for o in orgs if o.get("org_id") == org), None)
        else:
            v = org.strip().lower()
            matches = [o for o in orgs if (o.get("name") or "").lower() == v] \
                or [o for o in orgs if v in (o.get("name") or "").lower()]
            if not matches:
                return {"error": f"No organization matches '{org}'.",
                        "organizations": [o.get("name") for o in orgs]}
            if len(matches) > 1:
                return {"error": f"Multiple organizations match '{org}'; be more specific.",
                        "organizations": [o.get("name") for o in matches]}
            oid, name = matches[0]["org_id"], matches[0].get("name")
        _config.org_id = oid
        try:
            save_discovery(org_id=oid)
        except Exception:
            log.warning("Could not persist active org", exc_info=True)
        return {"active_org_id": oid, "active_org": name,
                "message": f"Active organization set to {name or oid}."}
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def _index_by_id(items: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and it.get("id"):
                out[it["id"]] = it
    return out


def tool_diff_org_config(baseline_file: str, org_id: Optional[str] = None, **_: Any) -> Dict[str, Any]:
    """Compare the current org configuration to a saved backup (config drift).

    baseline_file is a path to a JSON file previously produced by
    export_org_config. Reports added / removed / changed objects per resource type.
    """
    try:
        try:
            with open(baseline_file, encoding="utf-8") as f:
                loaded = json.load(f)
        except OSError as exc:
            return {"error": f"Could not read baseline file: {exc}"}
        except json.JSONDecodeError as exc:
            return {"error": f"Baseline is not valid JSON: {exc}"}

        base = loaded.get("config") if isinstance(loaded, dict) and "config" in loaded else loaded
        base_res = (base or {}).get("resources", {}) if isinstance(base, dict) else {}

        oid = _resolve_org(org_id)
        current = client().export_org_config(oid)
        cur_res = current.get("resources", {})

        changes: Dict[str, Any] = {}
        for rtype in sorted(set(base_res) | set(cur_res)):
            b = _index_by_id(base_res.get(rtype))
            c = _index_by_id(cur_res.get(rtype))
            disp = lambda d, i: (d[i].get("name") or i)
            added = [disp(c, i) for i in c if i not in b]
            removed = [disp(b, i) for i in b if i not in c]
            changed = [
                disp(c, i) for i in c if i in b
                and json.dumps(b[i], sort_keys=True, default=str)
                != json.dumps(c[i], sort_keys=True, default=str)
            ]
            if added or removed or changed:
                changes[rtype] = {"added": added, "removed": removed, "changed": changed}

        total = sum(len(v["added"]) + len(v["removed"]) + len(v["changed"]) for v in changes.values())
        return {
            "org_id": oid,
            "baseline_file": baseline_file,
            "total_changes": total,
            "changes": changes,
            "message": "No configuration drift." if total == 0
            else f"{total} configuration change(s) since the baseline.",
        }
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def tool_export_org_config(org_id: Optional[str] = None, **_: Any) -> Dict[str, Any]:
    """Export a read-only backup of the organization's configuration as JSON.

    Returns the org settings plus structural config (sites, networks, VPNs,
    templates, WLANs, NAC rules, webhooks, etc.) so the user can save it to a
    .json file. Secret-bearing config (e.g. pre-shared keys) is excluded, and
    any secrets Mist returns are typically already masked.
    """
    try:
        oid = _resolve_org(org_id)
        bundle = client().export_org_config(oid)
        org = bundle.get("org") or {}
        counts = {k: (len(v) if isinstance(v, list) else 1) for k, v in bundle["resources"].items()}
        return {
            "org_id": oid,
            "org_name": org.get("name"),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "resource_counts": counts,
            "errors": bundle.get("errors") or None,
            "config": bundle,
        }
    except (MistError, ValueError) as exc:
        return {"error": str(exc)}


def tool_start_setup(organization: Optional[str] = None, **_: Any) -> Dict[str, Any]:
    """First-run onboarding: detect region, discover orgs/sites, validate.

    The customer only needs to have entered an API token. Everything else —
    the API endpoint/region, organization, and sites — is discovered here.
    """
    # 1) Token present?
    try:
        token = _config.require_token()
    except ValueError as exc:
        return {
            "status": "REQUIRES ATTENTION",
            "message": "No Mist API token found. Open the extension settings and paste "
                       "an API token (Mist portal > My Account > API Token), then try again.",
            "detail": str(exc),
        }

    # 2) Auto-detect region from the token.
    try:
        region = _ensure_region(token)
    except MistError as exc:
        return {"status": "REQUIRES ATTENTION", "message": str(exc)}

    c = client()

    # 3) Identity check.
    try:
        me = c.get_self()
    except MistError as exc:
        return {"status": "REQUIRES ATTENTION", "message": str(exc)}

    # 4) Discover organizations and pick one (by name, never by id).
    try:
        orgs = c.get_organizations()
    except MistError as exc:
        return {"status": "REQUIRES ATTENTION", "message": str(exc)}

    if not orgs:
        return {
            "status": "REQUIRES ATTENTION",
            "message": "This token has no organization access. Ask your Mist admin to grant "
                       "at least read access, then run setup again.",
        }

    chosen = None
    if organization:
        q = organization.strip().lower()
        matches = [o for o in orgs if q in (o.get("name") or "").lower()]
        if len(matches) == 1:
            chosen = matches[0]
        elif len(matches) > 1:
            return {
                "status": "NEEDS INPUT",
                "message": f"More than one organization matches '{organization}'. "
                           "Please tell me the exact name.",
                "organizations": [o.get("name") for o in orgs],
            }
        else:
            return {
                "status": "NEEDS INPUT",
                "message": f"No organization named '{organization}'. Pick one of these by name.",
                "organizations": [o.get("name") for o in orgs],
            }
    elif len(orgs) == 1:
        chosen = orgs[0]
    elif _config.org_id:
        chosen = next((o for o in orgs if o.get("org_id") == _config.org_id), None)

    if chosen is None:
        return {
            "status": "NEEDS INPUT",
            "message": "You have access to multiple organizations. Tell me which one to use "
                       "by name and I'll finish setup.",
            "organizations": [o.get("name") for o in orgs],
        }

    org_id = chosen["org_id"]
    _config.org_id = org_id
    try:
        save_discovery(region=region, org_id=org_id)
    except Exception:
        log.warning("Could not persist discovered configuration", exc_info=True)

    # 5) Discover sites + 6) run validation tests.
    try:
        sites = c.get_sites(org_id)
    except MistError as exc:
        return {"status": "REQUIRES ATTENTION", "message": str(exc)}

    report = run_validation(c, org_id)

    return {
        "status": report.verdict,  # READY FOR USE / REQUIRES ATTENTION
        "report": report.to_text(),
        "summary": {
            "account": me.get("email") or me.get("name"),
            "region": region_label(region),
            "organization": chosen.get("name"),
            "site_count": len(sites),
            "mode": "read-write" if _config.write_enabled else "read-only",
        },
        "message": (
            f"Setup complete for organization '{chosen.get('name')}' "
            f"({len(sites)} site(s)) in {region_label(region)}. "
            f"Result: {report.verdict}."
        ),
        "write_access": _write_access_status(chosen),
    }


def _write_access_status(org: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Describe write readiness for the current mode and chosen org."""
    if not _config.write_enabled:
        return {
            "mode": "read-only",
            "ready": True,
            "message": "Read-only mode. To make changes, enable 'Enable write operations' "
                       "in the extension Settings.",
        }
    role = (org or {}).get("role")
    if is_write_role(role):
        return {"mode": "read-write", "ready": True,
                "message": f"Write mode is enabled and your token role '{role}' permits changes. "
                           "Each change still requires confirmation."}
    return {
        "mode": "read-write",
        "ready": False,
        "token_role": role,
        "message": f"Write mode is enabled, but your token role '{role or 'unknown'}' is read-only.",
        "how_to_fix": WRITE_TOKEN_GUIDANCE,
    }


def tool_get_status(**_: Any) -> Dict[str, Any]:
    """Report current mode, region, organization, and write readiness.

    Use this to answer 'am I read-only or read-write?' and to check whether the
    token can actually make changes when write mode is enabled.
    """
    if not _config.token:
        return {
            "configured": False,
            "mode": "read-write" if _config.write_enabled else "read-only",
            "message": "No API token is configured. Paste a Mist API token in the extension "
                       "Settings, then ask me to set you up.",
        }
    try:
        region = _ensure_region()
        c = client()
        me = c.get_self()
        orgs = c.get_organizations()
    except MistError as exc:
        return {"configured": True, "mode": "read-write" if _config.write_enabled else "read-only",
                "message": str(exc)}

    chosen = None
    if _config.org_id:
        chosen = next((o for o in orgs if o.get("org_id") == _config.org_id), None)
    elif len(orgs) == 1:
        chosen = orgs[0]

    return {
        "configured": True,
        "mode": "read-write" if _config.write_enabled else "read-only",
        "region": region_label(region),
        "account": me.get("email") or me.get("name"),
        "organization": (chosen or {}).get("name"),
        "organizations_available": [o.get("name") for o in orgs],
        "write_access": _write_access_status(chosen),
    }


# ---------------------------------------------------------------------------
# Tool registry / JSON Schemas
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Write tool implementations (only registered when write mode is enabled).
# Each requires confirm=true so nothing changes by accident.
# ---------------------------------------------------------------------------

def tool_rename_device(
    site_id: str, device_id: str, name: str, confirm: bool = False, **_: Any
) -> Dict[str, Any]:
    _require_confirm(confirm)
    result = client().rename_device(site_id, device_id, name)
    return {"action": "rename_device", "site_id": site_id, "device_id": device_id,
            "name": name, "result": result}


def tool_reboot_device(
    site_id: str, device_id: str, confirm: bool = False, **_: Any
) -> Dict[str, Any]:
    _require_confirm(confirm)
    result = client().restart_device(site_id, device_id)
    return {"action": "reboot_device", "site_id": site_id, "device_id": device_id,
            "result": result if result is not None else "restart requested"}


def tool_locate_device(
    site_id: str, device_id: str, enabled: bool = True, confirm: bool = False, **_: Any
) -> Dict[str, Any]:
    _require_confirm(confirm)
    result = client().set_device_led(site_id, device_id, enabled)
    return {"action": "locate_device", "site_id": site_id, "device_id": device_id,
            "led_enabled": bool(enabled), "result": result}


def tool_claim_devices(
    claim_codes: list, org_id: Optional[str] = None, confirm: bool = False, **_: Any
) -> Dict[str, Any]:
    _require_confirm(confirm)
    oid = _resolve_org(org_id)
    result = client().claim_devices(oid, claim_codes)
    return {"action": "claim_devices", "org_id": oid, "claim_codes": claim_codes,
            "result": result}


def tool_assign_devices_to_site(
    site_id: str, macs: list, org_id: Optional[str] = None,
    no_reassign: bool = False, confirm: bool = False, **_: Any
) -> Dict[str, Any]:
    _require_confirm(confirm)
    oid = _resolve_org(org_id)
    result = client().assign_devices_to_site(oid, site_id, macs, no_reassign=no_reassign)
    return {"action": "assign_devices_to_site", "org_id": oid, "site_id": site_id,
            "macs": macs, "result": result}


_ORG = {"org_id": {"type": "string", "description": "Mist organization id (optional)."}}
_ORG_SITE = {
    **_ORG,
    "site_id": {"type": "string", "description": "Restrict to a single site id (optional)."},
}


def _schema(props: Dict[str, Any], required: Optional[list] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {
        "type": "object",
        "properties": props,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


_CONFIRM = {
    "confirm": {
        "type": "boolean",
        "description": "Must be true to actually perform this change. Defaults to false.",
    }
}
_DEVICE = {
    "site_id": {"type": "string", "description": "Site id the device belongs to."},
    "device_id": {"type": "string", "description": "Device id (Mist device object id)."},
}


TOOLS: Dict[str, Dict[str, Any]] = {
    "start_setup": {
        "fn": tool_start_setup,
        "description": (
            "Run first-run onboarding: auto-detect the Mist region from the token, "
            "discover organizations and sites, run validation, and return a READY FOR USE "
            "report. Call this when the user is setting up or asks to get started. "
            "Optionally pass organization=\"<name>\" to choose among multiple orgs."
        ),
        "schema": _schema({
            "organization": {
                "type": "string",
                "description": "Organization NAME to use when several are accessible (optional).",
            }
        }),
    },
    "get_status": {
        "fn": tool_get_status,
        "description": (
            "Report the current mode (read-only vs read-write), region, organization, and "
            "whether the token can make changes. Use when the user asks about their mode or "
            "write access."
        ),
        "schema": _schema({}),
    },
    "get_organizations": {
        "fn": tool_get_organizations,
        "description": "List the Juniper Mist organizations the API token can access.",
        "schema": _schema({}),
    },
    "get_sites": {
        "fn": tool_get_sites,
        "description": "List the sites in an organization.",
        "schema": _schema(_ORG),
    },
    "get_access_points": {
        "fn": tool_get_access_points,
        "description": "List access points (APs) in an organization, optionally filtered to a site.",
        "schema": _schema(_ORG_SITE),
    },
    "get_switches": {
        "fn": tool_get_switches,
        "description": "List switches in an organization, optionally filtered to a site.",
        "schema": _schema(_ORG_SITE),
    },
    "get_clients": {
        "fn": tool_get_clients,
        "description": "List currently connected wireless clients (org-wide or for one site).",
        "schema": _schema(_ORG_SITE),
    },
    "get_offline_access_points": {
        "fn": tool_get_offline_access_points,
        "description": "Report access points that are currently offline (disconnected).",
        "schema": _schema(_ORG),
    },
    "generate_health_report": {
        "fn": tool_generate_health_report,
        "description": (
            "Generate a network health report as Markdown: device totals, online/offline "
            "counts, offline AP list, and per-site breakdown. The user can then save it as a "
            "file. Set include_clients=false to skip the (slower) org-wide client count."
        ),
        "schema": _schema({
            **_ORG,
            "include_clients": {
                "type": "boolean",
                "description": "Include the org-wide wireless client count (default true).",
            },
        }),
    },
    "generate_inventory_report": {
        "fn": tool_generate_inventory_report,
        "description": (
            "Generate a full device inventory report as Markdown (every AP and switch with "
            "model, serial, MAC, site, status, and firmware version)."
        ),
        "schema": _schema(_ORG),
    },
    "generate_firmware_report": {
        "fn": tool_generate_firmware_report,
        "description": (
            "Firmware compliance report (Markdown): for each AP/switch model, the version most "
            "of the fleet runs and which devices are behind it. Use to find firmware drift."
        ),
        "schema": _schema(_ORG),
    },
    "generate_topology": {
        "fn": tool_generate_topology,
        "description": (
            "Build a per-site network topology as a Mermaid diagram (plus structured nodes/edges): "
            "gateways, switches, and APs linked via switch-port LLDP neighbors. Provide a site name; "
            "set include_clients=true to add connected clients. Render the Mermaid or save it."
        ),
        "schema": _schema({
            "site": {"type": "string", "description": "Site name or id (optional if the org has one site)."},
            **_ORG,
            "include_clients": {"type": "boolean", "description": "Add connected clients as leaf nodes (optional)."},
        }),
    },
    "find_client": {
        "fn": tool_find_client,
        "description": (
            "Locate a wireless client by MAC address or hostname: which site, AP, and SSID it is "
            "on, plus IP, band, and signal. Provide mac or hostname."
        ),
        "schema": _schema({
            "mac": {"type": "string", "description": "Client MAC address."},
            "hostname": {"type": "string", "description": "Client hostname."},
            **_ORG,
            "duration": {"type": "string", "description": "Lookback window, e.g. 1d, 1h, 7d (default 1d)."},
        }),
    },
    "trace_client": {
        "fn": tool_trace_client,
        "description": (
            "Trace a wireless client's recent connection events (association, auth, DHCP, roam, "
            "disconnect) to troubleshoot why it can't connect or roams poorly. Returns an event "
            "timeline, type counts, and highlighted failures."
        ),
        "schema": _schema({
            "mac": {"type": "string", "description": "Client MAC address."},
            **_ORG,
            "duration": {"type": "string", "description": "Lookback window, e.g. 1d, 1h, 7d (default 1d)."},
        }, required=["mac"]),
    },
    "get_nac_clients": {
        "fn": tool_get_nac_clients,
        "description": (
            "List Access Assurance (NAC) clients authenticated to the network — user, client type "
            "(wired/wireless), auth type (EAP-TLS/PEAP/MAB), SSID, VLAN, matched auth rule, and status. "
            "Optional filters: mac, auth_type, type."
        ),
        "schema": _schema({
            **_ORG,
            "mac": {"type": "string", "description": "Filter by client MAC (optional)."},
            "auth_type": {"type": "string", "description": "Filter by auth type, e.g. eap-tls, peap, mab (optional)."},
            "type": {"type": "string", "description": "Filter by client type: wired or wireless (optional)."},
            "duration": {"type": "string", "description": "Lookback window, e.g. 1d, 1h, 7d (default 1d)."},
        }),
    },
    "troubleshoot_authentication": {
        "fn": tool_troubleshoot_authentication,
        "description": (
            "Troubleshoot 802.1X/MAB authentication using Access Assurance (NAC) events: returns the "
            "auth-event timeline, per-type counts, and highlighted failures (denied/rejected/errored). "
            "Focus on one identity with mac (client MAC) and/or user (username or certificate CN)."
        ),
        "schema": _schema({
            **_ORG,
            "mac": {"type": "string", "description": "Client MAC to focus on (optional)."},
            "user": {"type": "string", "description": "Username or certificate CN to focus on, free-text match (optional)."},
            "duration": {"type": "string", "description": "Lookback window, e.g. 1d, 1h, 7d (default 1d)."},
        }),
    },
    "generate_nac_dashboard": {
        "fn": tool_generate_nac_dashboard,
        "description": (
            "Build a self-contained HTML Access Assurance (NAC) dashboard: summary cards and bar "
            "charts for auth types, client types, status, event types, and top failing users/rules. "
            "Returns HTML the user can save as a .html file and open in a browser."
        ),
        "schema": _schema({
            **_ORG,
            "duration": {"type": "string", "description": "Lookback window, e.g. 1d, 1h, 7d (default 1d)."},
        }),
    },
    "get_marvis_actions": {
        "fn": tool_get_marvis_actions,
        "description": (
            "List Marvis (AI) suggested actions — Mist's prioritized view of what's wrong "
            "(offline switches/APs, non-compliant firmware, misconfigured ports, RF/DFS issues) "
            "and the recommended fix. Use for 'what's wrong with my network?'. "
            "Pass status=\"open\" for active items only."
        ),
        "schema": _schema({
            **_ORG,
            "status": {"type": "string", "description": "Filter by status, e.g. open (optional)."},
        }),
    },
    "get_alarms": {
        "fn": tool_get_alarms,
        "description": (
            "List organization alarms over a window with per-severity and per-type counts "
            "(device/switch/gateway offline, restarts, security, Marvis). Optional severity filter."
        ),
        "schema": _schema({
            **_ORG,
            "severity": {"type": "string", "description": "Filter by severity, e.g. critical/warn/info (optional)."},
            "duration": {"type": "string", "description": "Lookback window, e.g. 1d, 1h, 7d (default 1d)."},
        }),
    },
    "get_wired_clients": {
        "fn": tool_get_wired_clients,
        "description": (
            "List wired clients (devices seen on switch ports): switch MAC, port, VLAN, IP, and "
            "vendor. Optional mac / hostname / site_id filters. Complements get_clients (wireless)."
        ),
        "schema": _schema({
            **_ORG,
            "site_id": {"type": "string", "description": "Restrict to a site (optional)."},
            "mac": {"type": "string", "description": "Filter by client MAC (optional)."},
            "hostname": {"type": "string", "description": "Filter by hostname (optional)."},
            "duration": {"type": "string", "description": "Lookback window, e.g. 1d, 7d (default 1d)."},
        }),
    },
    "get_sle": {
        "fn": tool_get_sle,
        "description": (
            "Service Level Expectations (SLE) per site — Mist's user-experience scores as "
            "percentages (e.g. ap-health, coverage, capacity, successful-connects, switch/gateway/"
            "wan health). Use to gauge experience quality by site."
        ),
        "schema": _schema(_ORG),
    },
    "get_switch_ports": {
        "fn": tool_get_switch_ports,
        "description": (
            "Switch/device port statistics: link state, speed/duplex, PoE on/off and draw, LLDP "
            "neighbor, and traffic. Filter by switch_mac, site_id, or up (true/false)."
        ),
        "schema": _schema({
            **_ORG,
            "switch_mac": {"type": "string", "description": "Filter to one switch by MAC (optional)."},
            "site_id": {"type": "string", "description": "Restrict to a site (optional)."},
            "up": {"type": "boolean", "description": "Filter to up (true) or down (false) ports (optional)."},
        }),
    },
    "export_org_config": {
        "fn": tool_export_org_config,
        "description": (
            "Export a read-only backup of the org configuration as JSON (org settings, sites, "
            "networks, VPNs, templates, WLANs, NAC rules, webhooks, etc.) for the user to save to "
            "a .json file. Secrets are excluded/masked."
        ),
        "schema": _schema(_ORG),
    },
    "set_active_org": {
        "fn": tool_set_active_org,
        "description": (
            "Set the default organization for this session by name or id. Useful when the token "
            "can access several organizations — subsequent tools then target the chosen org."
        ),
        "schema": _schema(
            {"org": {"type": "string", "description": "Organization name or id."}},
            required=["org"],
        ),
    },
    "diff_org_config": {
        "fn": tool_diff_org_config,
        "description": (
            "Compare the current org configuration against a saved backup file (config drift): "
            "reports added, removed, and changed objects per resource type. baseline_file is a "
            "path to a JSON file previously produced by export_org_config."
        ),
        "schema": _schema(
            {
                "baseline_file": {"type": "string", "description": "Path to a saved export_org_config JSON file."},
                **_ORG,
            },
            required=["baseline_file"],
        ),
    },
}


# Write tools — registered only when write mode is enabled. Every write
# requires confirm=true. These change Mist configuration / device state.
WRITE_TOOLS: Dict[str, Dict[str, Any]] = {
    "rename_device": {
        "fn": tool_rename_device,
        "description": "[WRITE] Rename an access point or switch. Requires confirm=true.",
        "schema": _schema(
            {**_DEVICE, "name": {"type": "string", "description": "New device name."}, **_CONFIRM},
            required=["site_id", "device_id", "name"],
        ),
    },
    "reboot_device": {
        "fn": tool_reboot_device,
        "description": "[WRITE] Reboot a device. Disruptive. Requires confirm=true.",
        "schema": _schema({**_DEVICE, **_CONFIRM}, required=["site_id", "device_id"]),
    },
    "locate_device": {
        "fn": tool_locate_device,
        "description": "[WRITE] Turn a device's locate LED on/off. Requires confirm=true.",
        "schema": _schema(
            {**_DEVICE,
             "enabled": {"type": "boolean", "description": "LED on (true) or off (false)."},
             **_CONFIRM},
            required=["site_id", "device_id"],
        ),
    },
    "claim_devices": {
        "fn": tool_claim_devices,
        "description": "[WRITE] Claim devices into an org by activation/claim code. Requires confirm=true.",
        "schema": _schema(
            {"claim_codes": {"type": "array", "items": {"type": "string"},
                             "description": "Activation/claim codes."},
             **_ORG, **_CONFIRM},
            required=["claim_codes"],
        ),
    },
    "assign_devices_to_site": {
        "fn": tool_assign_devices_to_site,
        "description": "[WRITE] Assign inventory devices (by MAC) to a site. Requires confirm=true.",
        "schema": _schema(
            {"site_id": {"type": "string", "description": "Target site id."},
             "macs": {"type": "array", "items": {"type": "string"},
                      "description": "Device MAC addresses."},
             "no_reassign": {"type": "boolean",
                             "description": "Fail if a device is already assigned (default false)."},
             **_ORG, **_CONFIRM},
            required=["site_id", "macs"],
        ),
    },
}

if _config.write_enabled:
    TOOLS.update(WRITE_TOOLS)
    log.info("Write mode ENABLED: %d write tool(s) registered.", len(WRITE_TOOLS))


# ---------------------------------------------------------------------------
# JSON-RPC / MCP dispatch
# ---------------------------------------------------------------------------

class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _tool_list() -> Dict[str, Any]:
    return {
        "tools": [
            {"name": name, "description": meta["description"], "inputSchema": meta["schema"]}
            for name, meta in TOOLS.items()
        ]
    }


def _tool_call(params: Dict[str, Any]) -> Dict[str, Any]:
    name = params.get("name")
    args = params.get("arguments") or {}
    meta = TOOLS.get(name)
    if meta is None:
        raise RpcError(-32602, f"Unknown tool: {name}")
    try:
        result = meta["fn"](**args)
        is_error = isinstance(result, dict) and "error" in result
    except (MistError, ValueError) as exc:
        result = {"error": str(exc)}
        is_error = True
    text = json.dumps(result, indent=2, default=str)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": result,
        "isError": is_error,
    }


def dispatch(method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return a JSON-RPC result for a request method (None for notifications)."""
    if method == "initialize":
        requested = params.get("protocolVersion") or SUPPORTED_PROTOCOL
        return {
            "protocolVersion": requested,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": __version__},
            "instructions": INSTRUCTIONS,
        }
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None  # notification, no response
    if method == "ping":
        return {}
    if method == "tools/list":
        return _tool_list()
    if method == "tools/call":
        return _tool_call(params)
    raise RpcError(-32601, f"Method not found: {method}")


def _write(message: Dict[str, Any], out) -> None:
    out.write(json.dumps(message) + "\n")
    out.flush()


def serve(stdin=None, stdout=None) -> None:
    """Run the stdio JSON-RPC loop until EOF."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _write(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": "Parse error"}},
                stdout,
            )
            continue

        msg_id = msg.get("id")
        method = msg.get("method", "")
        params = msg.get("params") or {}
        is_notification = "id" not in msg

        try:
            result = dispatch(method, params)
            if is_notification or result is None:
                continue
            _write({"jsonrpc": "2.0", "id": msg_id, "result": result}, stdout)
        except RpcError as exc:
            if not is_notification:
                _write(
                    {"jsonrpc": "2.0", "id": msg_id,
                     "error": {"code": exc.code, "message": exc.message}},
                    stdout,
                )
        except Exception as exc:  # never crash the transport
            log.exception("Unhandled error handling %s", method)
            if not is_notification:
                _write(
                    {"jsonrpc": "2.0", "id": msg_id,
                     "error": {"code": -32603, "message": f"Internal error: {exc}"}},
                    stdout,
                )


def main() -> None:
    log.info("Starting %s v%s (region=%s)", SERVER_NAME, __version__, _config.region)
    serve()


if __name__ == "__main__":
    main()

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
from typing import Any, Dict, Optional

from . import __version__
from .config import load_config, save_discovery
from .discovery import discover_region, region_label
from .mist_client import MistClient, MistError
from .reports import (
    build_health_report,
    build_inventory_report,
    render_health_markdown,
    render_inventory_markdown,
)
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


def _resolve_org(org_id: Optional[str]) -> str:
    """Resolve an org id from the argument, configuration, or single-org access."""
    if org_id:
        return org_id
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

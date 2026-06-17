"""Minimal, dependency-free Juniper Mist REST API client (read-only).

Uses only the Python standard library so the client, setup wizard, and
validation framework can run without installing third-party packages.
All requests are GET requests; this client intentionally exposes no
methods that mutate Mist configuration.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from . import __version__
from .regions import base_url

USER_AGENT = f"hpe-networking-assistant/{__version__}"
DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 2
PAGE_LIMIT = 1000


class MistError(Exception):
    """Base class for Mist client errors."""


class MistAuthError(MistError):
    """Raised when the API token is missing, invalid, or unauthorized (401/403)."""


class MistReadOnlyError(MistError):
    """Raised when a write is attempted while the client is in read-only mode."""


class MistRateLimitError(MistError):
    """Raised when the Mist API rate limit is exceeded (429)."""


class MistAPIError(MistError):
    """Raised for other non-success HTTP responses."""

    def __init__(self, status: int, message: str, body: Any = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.body = body


class MistClient:
    """Read-only client for the Juniper Mist API.

    Parameters
    ----------
    token:
        A Mist API token. Sent as ``Authorization: Token <token>``.
    region:
        Region code (e.g. ``global01``), API host, or base URL. See
        :mod:`hpe_mist_mcp.regions`.
    timeout:
        Per-request timeout in seconds.
    """

    def __init__(
        self,
        token: str,
        region: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        read_only: bool = True,
    ) -> None:
        if not token or not token.strip():
            raise MistAuthError("A Mist API token is required.")
        self.token = token.strip()
        self.base_url = base_url(region)
        self.timeout = timeout
        self.retries = retries
        self.read_only = read_only

    # -- low level ---------------------------------------------------------

    def _request(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        method: str = "GET",
        body: Any = None,
    ) -> Any:
        if method != "GET" and self.read_only:
            raise MistReadOnlyError(
                f"Refusing {method} {path}: client is in read-only mode. "
                "Enable write mode to perform configuration changes."
            )

        url = self.base_url + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)

        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Token {self.token}")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", USER_AGENT)
        if data is not None:
            req.add_header("Content-Type", "application/json")

        # Do not auto-retry non-idempotent writes (POST/PUT/DELETE) to avoid
        # accidentally applying a change twice.
        max_retries = self.retries if method == "GET" else 0

        last_exc: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:
                err_body = _safe_read(exc)
                if exc.code in (401, 403):
                    raise MistAuthError(
                        f"Authentication failed (HTTP {exc.code}). "
                        "Check that your API token is valid and authorized for this organization."
                    ) from exc
                if exc.code == 429:
                    # Honor Retry-After when present, otherwise back off.
                    if attempt < max_retries:
                        time.sleep(_retry_after(exc, attempt))
                        last_exc = exc
                        continue
                    raise MistRateLimitError("Mist API rate limit exceeded (HTTP 429).") from exc
                if exc.code >= 500 and attempt < max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    last_exc = exc
                    continue
                raise MistAPIError(exc.code, exc.reason or "request failed", err_body) from exc
            except urllib.error.URLError as exc:
                last_exc = exc
                if attempt < max_retries:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise MistError(f"Network error contacting {self.base_url}: {exc.reason}") from exc

        # Should not reach here, but guard against silent None.
        raise MistError(f"Request to {path} failed: {last_exc}")

    def _paginate(self, path: str, params: Optional[Dict[str, Any]] = None) -> List[Any]:
        """Fetch all pages for list endpoints that return a JSON array."""
        params = dict(params or {})
        params.setdefault("limit", PAGE_LIMIT)
        page = 1
        out: List[Any] = []
        while True:
            params["page"] = page
            data = self._request(path, params)
            if not isinstance(data, list):
                # Endpoint is not a paginated array; return as-is.
                return data if isinstance(data, list) else [data]
            out.extend(data)
            if len(data) < params["limit"]:
                break
            page += 1
            if page > 100:  # hard safety stop
                break
        return out

    # -- identity / discovery ---------------------------------------------

    def get_self(self) -> Dict[str, Any]:
        """Return the authenticated identity, including org privileges."""
        return self._request("/api/v1/self")

    def get_organizations(self) -> List[Dict[str, Any]]:
        """Derive the list of accessible organizations from ``/self`` privileges."""
        me = self.get_self()
        orgs: Dict[str, Dict[str, Any]] = {}
        for priv in me.get("privileges", []):
            if priv.get("scope") == "org" and priv.get("org_id"):
                oid = priv["org_id"]
                if oid not in orgs:
                    orgs[oid] = {
                        "org_id": oid,
                        "name": priv.get("name"),
                        "role": priv.get("role"),
                    }
        return list(orgs.values())

    def get_org(self, org_id: str) -> Dict[str, Any]:
        return self._request(f"/api/v1/orgs/{org_id}")

    def get_sites(self, org_id: str) -> List[Dict[str, Any]]:
        return self._paginate(f"/api/v1/orgs/{org_id}/sites")

    # -- inventory ---------------------------------------------------------

    def get_org_inventory(
        self, org_id: str, device_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return org-wide device inventory.

        ``device_type`` may be one of ``ap``, ``switch``, ``gateway``.
        Each item includes a ``connected`` boolean used for offline detection.
        """
        params: Dict[str, Any] = {}
        if device_type:
            params["type"] = device_type
        return self._paginate(f"/api/v1/orgs/{org_id}/inventory", params)

    def get_access_points(self, org_id: str) -> List[Dict[str, Any]]:
        return self.get_org_inventory(org_id, "ap")

    def get_switches(self, org_id: str) -> List[Dict[str, Any]]:
        return self.get_org_inventory(org_id, "switch")

    def get_offline_access_points(self, org_id: str) -> List[Dict[str, Any]]:
        """Return APs whose inventory ``connected`` flag is false."""
        aps = self.get_access_points(org_id)
        return [ap for ap in aps if ap.get("connected") is False]

    # -- clients -----------------------------------------------------------

    def get_site_clients(self, site_id: str) -> List[Dict[str, Any]]:
        """Return currently connected wireless clients for a site."""
        return self._paginate(f"/api/v1/sites/{site_id}/stats/clients")

    def get_org_clients(self, org_id: str) -> List[Dict[str, Any]]:
        """Return connected wireless clients across every site in an org."""
        clients: List[Dict[str, Any]] = []
        for site in self.get_sites(org_id):
            site_id = site.get("id")
            if not site_id:
                continue
            for client in self.get_site_clients(site_id):
                client.setdefault("site_id", site_id)
                client.setdefault("site_name", site.get("name"))
                clients.append(client)
        return clients


    # -- search / troubleshooting -----------------------------------------

    def _search(self, path: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Call a Mist search endpoint and return its ``results`` list."""
        data = self._request(path, params)
        if isinstance(data, dict):
            results = data.get("results")
            return results if isinstance(results, list) else []
        return data if isinstance(data, list) else []

    def search_clients(
        self, org_id: str, mac: Optional[str] = None, hostname: Optional[str] = None,
        duration: str = "1d", limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Search wireless clients across an org by MAC and/or hostname."""
        params: Dict[str, Any] = {"duration": duration, "limit": limit}
        if mac:
            params["mac"] = mac
        if hostname:
            params["hostname"] = hostname
        return self._search(f"/api/v1/orgs/{org_id}/clients/search", params)

    def search_client_events(
        self, org_id: str, mac: str, duration: str = "1d", limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Return recent wireless client events (assoc/auth/dhcp/roam/etc.) for a MAC."""
        params = {"mac": mac, "duration": duration, "limit": limit}
        return self._search(f"/api/v1/orgs/{org_id}/clients/events/search", params)

    # -- Access Assurance (NAC) -------------------------------------------

    def search_nac_clients(
        self, org_id: str, mac: Optional[str] = None, type: Optional[str] = None,
        auth_type: Optional[str] = None, nacrule_id: Optional[str] = None,
        duration: str = "1d", limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Search Access Assurance (NAC) clients authenticated to the network."""
        params = {
            "duration": duration, "limit": limit, "mac": mac, "type": type,
            "auth_type": auth_type, "nacrule_id": nacrule_id,
        }
        return self._search(f"/api/v1/orgs/{org_id}/nac_clients/search", params)

    def search_nac_events(
        self, org_id: str, mac: Optional[str] = None, text: Optional[str] = None,
        type: Optional[str] = None, nacrule_id: Optional[str] = None,
        auth_type: Optional[str] = None, duration: str = "1d", limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Search Access Assurance (NAC) authentication events (permits/denies/etc.).

        ``text`` is a free-text identity match (username, certificate CN, etc.);
        ``mac`` filters by client MAC.
        """
        params = {
            "duration": duration, "limit": limit, "mac": mac, "text": text,
            "type": type, "nacrule_id": nacrule_id, "auth_type": auth_type,
        }
        return self._search(f"/api/v1/orgs/{org_id}/nac_clients/events/search", params)

    def search_wired_clients(
        self, org_id: str, mac: Optional[str] = None, hostname: Optional[str] = None,
        duration: str = "1d", limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Search wired clients (devices seen on switch ports) across an org."""
        params = {"duration": duration, "limit": limit, "mac": mac, "hostname": hostname}
        return self._search(f"/api/v1/orgs/{org_id}/wired_clients/search", params)

    def search_alarms(
        self, org_id: str, severity: Optional[str] = None, duration: str = "1d", limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Search org alarms over a time window (optionally filtered by severity)."""
        params = {"duration": duration, "limit": limit, "severity": severity}
        return self._search(f"/api/v1/orgs/{org_id}/alarms/search", params)

    def _list_from(self, data: Any) -> List[Dict[str, Any]]:
        """Extract a result list from a Mist response (``results`` or ``data`` key)."""
        if isinstance(data, dict):
            for key in ("results", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
            return []
        return data if isinstance(data, list) else []

    def get_sites_sle(self, org_id: str) -> List[Dict[str, Any]]:
        """Return per-site Service Level Expectation (SLE) scores for the org.

        NOTE: best-known path /orgs/:org_id/insights/sites-sle; confirm against
        your tenant if your account exposes SLE under a different path.
        """
        return self._list_from(self._request(f"/api/v1/orgs/{org_id}/insights/sites-sle"))

    def search_switch_ports(
        self, org_id: str, mac: Optional[str] = None, site_id: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Return switch/device port statistics across the org (single page)."""
        params = {"limit": limit, "mac": mac, "site_id": site_id}
        return self._list_from(self._request(f"/api/v1/orgs/{org_id}/stats/ports/search", params))

    # Org config resource types included in a backup (structural config only;
    # secret-bearing types like psks are intentionally excluded).
    CONFIG_RESOURCES = [
        "sites", "sitegroups", "sitetemplates", "networks", "vpns", "services",
        "servicepolicies", "networktemplates", "gatewaytemplates", "rftemplates",
        "deviceprofiles", "wlantemplates", "wlans", "nactags", "nacrules",
        "alarmtemplates", "webhooks",
    ]

    def export_org_config(
        self, org_id: str, resources: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Read-only backup of an org's configuration (best-effort per resource)."""
        bundle: Dict[str, Any] = {"org": None, "resources": {}, "errors": {}}
        try:
            bundle["org"] = self._request(f"/api/v1/orgs/{org_id}")
        except MistError as exc:
            bundle["errors"]["org"] = str(exc)
        for res in (resources or self.CONFIG_RESOURCES):
            try:
                bundle["resources"][res] = self._paginate(f"/api/v1/orgs/{org_id}/{res}")
            except MistError as exc:
                bundle["errors"][res] = str(exc)
        return bundle

    def get_marvis_actions(self, org_id: str) -> List[Dict[str, Any]]:
        """Return Marvis (AI) suggested actions for the org.

        NOTE: the Marvis Actions endpoint path is /orgs/:org_id/suggestion; confirm
        against your tenant if your account exposes it under a different path.
        """
        data = self._request(f"/api/v1/orgs/{org_id}/suggestion")
        if isinstance(data, dict):
            for key in ("results", "suggestions", "actions", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
            return []
        return data if isinstance(data, list) else []

    # -- writes (only permitted when read_only is False) -------------------

    def rename_device(self, site_id: str, device_id: str, name: str) -> Dict[str, Any]:
        """Set a device's name (PUT device config). Returns the updated device."""
        return self._request(
            f"/api/v1/sites/{site_id}/devices/{device_id}",
            method="PUT",
            body={"name": name},
        )

    def restart_device(self, site_id: str, device_id: str) -> Any:
        """Reboot a device (POST .../restart)."""
        return self._request(
            f"/api/v1/sites/{site_id}/devices/{device_id}/restart",
            method="POST",
        )

    def set_device_led(self, site_id: str, device_id: str, enabled: bool) -> Dict[str, Any]:
        """Turn a device's locate/status LED on or off (PUT device config)."""
        return self._request(
            f"/api/v1/sites/{site_id}/devices/{device_id}",
            method="PUT",
            body={"led": {"enabled": bool(enabled)}},
        )

    def claim_devices(self, org_id: str, claim_codes: List[str]) -> Any:
        """Claim one or more devices into an org by activation/claim code."""
        return self._request(
            f"/api/v1/orgs/{org_id}/inventory",
            method="POST",
            body=list(claim_codes),
        )

    def assign_devices_to_site(
        self, org_id: str, site_id: str, macs: List[str], no_reassign: bool = False
    ) -> Any:
        """Assign inventory devices (by MAC) to a site."""
        return self._request(
            f"/api/v1/orgs/{org_id}/inventory",
            method="PUT",
            body={
                "op": "assign",
                "site_id": site_id,
                "macs": [m.lower().replace(":", "") for m in macs],
                "no_reassign": bool(no_reassign),
            },
        )


def _safe_read(exc: urllib.error.HTTPError) -> Any:
    try:
        raw = exc.read().decode("utf-8")
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _retry_after(exc: urllib.error.HTTPError, attempt: int) -> float:
    header = exc.headers.get("Retry-After") if exc.headers else None
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    return 2.0 * (attempt + 1)

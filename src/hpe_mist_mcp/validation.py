"""Validation framework for the HPE Networking Assistant.

Runs a sequence of read-only checks against Juniper Mist and produces an
overall verdict of ``READY FOR USE`` or ``REQUIRES ATTENTION``.

Can be used programmatically (``run_validation``) or as a CLI
(``python -m hpe_mist_mcp.validation``).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import List, Optional

from .config import Config, load_config
from .mist_client import MistClient, MistError

READY = "READY FOR USE"
ATTENTION = "REQUIRES ATTENTION"

# Mist account roles that permit configuration changes. A token inherits the
# role of the account that created it and cannot be elevated via the API.
WRITE_ROLES = {"admin", "write", "superuser", "super_admin", "network_admin"}


def is_write_role(role) -> bool:
    """True if a Mist role string permits write/configuration changes."""
    return bool(role) and str(role).strip().lower() in WRITE_ROLES


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class ValidationReport:
    results: List[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def verdict(self) -> str:
        return READY if self.passed else ATTENTION

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.results.append(CheckResult(name, passed, detail))

    def to_text(self) -> str:
        lines = ["HPE Networking Assistant — Validation Report", "=" * 44]
        for r in self.results:
            mark = "PASS" if r.passed else "FAIL"
            lines.append(f"[{mark}] {r.name}: {r.detail}")
        lines.append("-" * 44)
        lines.append(f"RESULT: {self.verdict}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "passed": self.passed,
            "checks": [
                {"name": r.name, "passed": r.passed, "detail": r.detail}
                for r in self.results
            ],
        }


def run_validation(client: MistClient, org_id: Optional[str] = None) -> ValidationReport:
    """Run authentication, org, site, and inventory checks."""
    report = ValidationReport()

    # 1. Authentication
    try:
        me = client.get_self()
        who = me.get("email") or me.get("name") or "authenticated user"
        report.add("Authentication", True, f"Token valid for {who}.")
    except MistError as exc:
        report.add("Authentication", False, str(exc))
        return report  # nothing else can succeed without auth

    # 2. Organization access
    try:
        orgs = client.get_organizations()
        if not orgs:
            report.add("Organization access", False, "Token has no organization privileges.")
            return report
        report.add("Organization access", True, f"{len(orgs)} organization(s) accessible.")
        if org_id is None:
            org_id = orgs[0]["org_id"]
    except MistError as exc:
        report.add("Organization access", False, str(exc))
        return report

    # 3. Site access
    try:
        sites = client.get_sites(org_id)
        report.add("Site access", True, f"{len(sites)} site(s) in org {org_id}.")
    except MistError as exc:
        report.add("Site access", False, str(exc))

    # 4. Device inventory access
    try:
        aps = client.get_access_points(org_id)
        switches = client.get_switches(org_id)
        report.add(
            "Device inventory access",
            True,
            f"{len(aps)} AP(s), {len(switches)} switch(es) visible.",
        )
    except MistError as exc:
        report.add("Device inventory access", False, str(exc))

    # 5. Write access — only checked when write mode is enabled. A read-only
    # token here means the customer must supply a write-capable token.
    if not getattr(client, "read_only", True):
        try:
            orgs = client.get_organizations()
            role = next((o.get("role") for o in orgs if o.get("org_id") == org_id), None)
            if is_write_role(role):
                report.add("Write access", True, f"Token role '{role}' permits configuration changes.")
            else:
                report.add(
                    "Write access", False,
                    f"Token role '{role or 'unknown'}' is read-only. Create a token under an "
                    "account with a write role (e.g. Network Admin) and update it in the "
                    "extension Settings to make changes.",
                )
        except MistError as exc:
            report.add("Write access", False, str(exc))

    # 6. Operating mode (informational; never fails the verdict)
    mode = "read-only" if getattr(client, "read_only", True) else "read-write (writes require confirmation)"
    report.add("Operating mode", True, mode)

    return report


def validate_from_config(cfg: Optional[Config] = None) -> ValidationReport:
    cfg = cfg or load_config()
    report = ValidationReport()
    if not cfg.token:
        report.add("Configuration", False, "No API token configured.")
        return report
    region = cfg.region
    if not region:
        from .discovery import discover_region
        region = discover_region(cfg.token)
    client = MistClient(token=cfg.token, region=region, read_only=not cfg.write_enabled)
    return run_validation(client, cfg.org_id)


def main(argv: Optional[List[str]] = None) -> int:
    report = validate_from_config()
    print(report.to_text())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())

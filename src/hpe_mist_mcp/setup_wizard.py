"""Interactive setup wizard for the HPE Networking Assistant.

Steps:
  1. Request an API token.
  2. Choose / confirm the Mist region.
  3. Validate the token.
  4. Discover organizations.
  5. Discover sites (and pick a default org).
  6. Save configuration.
  7. Execute validation tests and print the verdict.

Run with: ``python -m hpe_mist_mcp.setup_wizard``

Non-interactive use is supported via environment variables
(``MIST_API_TOKEN``, ``MIST_REGION``, ``MIST_ORG_ID``) together with the
``--non-interactive`` flag, which is handy for CI smoke tests.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from typing import List, Optional

from .config import Config, _as_bool, save_config
from .discovery import discover_region
from .mist_client import MistClient, MistError
from .regions import REGION_LABELS, normalize_region
from .validation import run_validation


def _prompt(msg: str) -> str:
    try:
        return input(msg).strip()
    except EOFError:
        return ""


def _choose_region(default: str = DEFAULT_REGION) -> str:
    print("\nAvailable Mist regions:")
    codes = list(REGION_LABELS.keys())
    for i, code in enumerate(codes, 1):
        print(f"  {i:>2}. {REGION_LABELS[code]}")
    print(
        "\nTip: find your region from the Mist portal URL — replace 'manage' "
        "with 'api' (e.g. manage.eu.mist.com -> api.eu.mist.com)."
    )
    raw = _prompt(f"Select region [1-{len(codes)}] (default {REGION_LABELS[default]}): ")
    if not raw:
        return default
    if raw.isdigit() and 1 <= int(raw) <= len(codes):
        return codes[int(raw) - 1]
    try:
        return normalize_region(raw)
    except ValueError as exc:
        print(f"  ! {exc}\n  Using default region.")
        return default


def _choose_org(orgs: List[dict]) -> Optional[str]:
    if not orgs:
        return None
    if len(orgs) == 1:
        print(f"\nSingle organization detected: {orgs[0].get('name')} ({orgs[0]['org_id']})")
        return orgs[0]["org_id"]
    print("\nAccessible organizations:")
    for i, o in enumerate(orgs, 1):
        print(f"  {i:>2}. {o.get('name')}  [{o['org_id']}]  role={o.get('role')}")
    raw = _prompt(f"Select a default organization [1-{len(orgs)}] (optional): ")
    if raw.isdigit() and 1 <= int(raw) <= len(orgs):
        return orgs[int(raw) - 1]["org_id"]
    return None


def run(non_interactive: bool = False) -> int:
    print("=" * 52)
    print(" HPE Networking Assistant — Setup Wizard")
    print("=" * 52)

    # Step 1 + 2: token and region
    if non_interactive:
        token = os.environ.get("MIST_API_TOKEN", "")
        region_override = os.environ.get("MIST_REGION")
        org_id = os.environ.get("MIST_ORG_ID") or None
        write_enabled = _as_bool(os.environ.get("MIST_WRITE_ENABLED"))
        if not token:
            print("ERROR: MIST_API_TOKEN is required in non-interactive mode.", file=sys.stderr)
            return 2
    else:
        token = getpass.getpass("\nStep 1/7 — Enter your Mist API token (input hidden): ").strip()
        if not token:
            print("ERROR: an API token is required.", file=sys.stderr)
            return 2
        region_override = None
        org_id = None
        print(
            "\nWrite mode lets the assistant CHANGE your Mist environment "
            "(rename/reboot devices, locate LEDs, claim/assign inventory)."
        )
        ans = _prompt("Enable write operations? Leave blank for safe read-only [y/N]: ")
        write_enabled = ans.strip().lower() in ("y", "yes")

    # Step 2: auto-detect the region from the token (no API endpoint needed).
    if region_override and region_override.strip().lower() != "auto":
        region = normalize_region(region_override)
        print(f"\nStep 2/7 — Using region {REGION_LABELS.get(region, region)} (from MIST_REGION).")
    else:
        print("\nStep 2/7 — Detecting your Mist region from the token...")
        try:
            region = discover_region(token)
            print(f"  ✓ Detected region: {REGION_LABELS.get(region, region)}")
        except MistError as exc:
            print(f"  ✗ {exc}", file=sys.stderr)
            return 1

    # Step 3: validate token
    print("\nStep 3/7 — Validating token...")
    client = MistClient(token=token, region=region)
    try:
        me = client.get_self()
        print(f"  ✓ Token valid for {me.get('email') or me.get('name') or 'user'}.")
    except MistError as exc:
        print(f"  ✗ Token validation failed: {exc}", file=sys.stderr)
        return 1

    # Step 4: discover organizations
    print("\nStep 4/7 — Discovering organizations...")
    try:
        orgs = client.get_organizations()
        print(f"  ✓ Found {len(orgs)} organization(s).")
    except MistError as exc:
        print(f"  ✗ Could not list organizations: {exc}", file=sys.stderr)
        return 1

    if not non_interactive:
        org_id = _choose_org(orgs)
    elif org_id is None and len(orgs) == 1:
        org_id = orgs[0]["org_id"]

    # Step 5: discover sites
    print("\nStep 5/7 — Discovering sites...")
    target_org = org_id or (orgs[0]["org_id"] if orgs else None)
    if target_org:
        try:
            sites = client.get_sites(target_org)
            print(f"  ✓ Found {len(sites)} site(s) in org {target_org}.")
        except MistError as exc:
            print(f"  ! Could not list sites: {exc}")

    # Step 6: save configuration
    print("\nStep 6/7 — Saving configuration...")
    cfg = Config(token=token, region=region, org_id=org_id, write_enabled=write_enabled)
    path = save_config(cfg)
    print(f"  ✓ Configuration written to {path}")
    print(f"    Mode: {'READ-WRITE (writes require confirmation)' if write_enabled else 'READ-ONLY'}")
    print("    (The packaged Claude Desktop extension stores the token in the OS keychain instead.)")

    # Step 7: validation tests
    print("\nStep 7/7 — Running validation tests...\n")
    report = run_validation(client, org_id)
    print(report.to_text())

    return 0 if report.passed else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="HPE Networking Assistant setup wizard.")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Read token/region/org from environment variables (for CI).",
    )
    args = parser.parse_args(argv)
    return run(non_interactive=args.non_interactive)


if __name__ == "__main__":
    sys.exit(main())

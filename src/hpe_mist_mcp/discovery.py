"""Automatic Juniper Mist region detection.

A Mist API token is issued by a specific cloud (region). Rather than asking the
customer to know their API endpoint, we probe every known cloud in parallel
with the token and keep the one that authenticates. This lets the onboarding
flow configure the assistant from a token alone.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from .mist_client import MistAuthError, MistClient, MistError
from .regions import REGION_LABELS, REGIONS

PROBE_TIMEOUT = 8


def discover_region(token: str, timeout: int = PROBE_TIMEOUT) -> str:
    """Return the region code whose cloud authenticates ``token``.

    Raises ``MistAuthError`` if no cloud accepts the token (bad/expired token)
    or the network is unreachable.
    """
    if not token or not token.strip():
        raise MistAuthError("A Mist API token is required to detect the region.")

    def probe(code: str) -> Optional[str]:
        try:
            MistClient(token, region=code, timeout=timeout, retries=0).get_self()
            return code
        except MistError:
            return None

    found: Optional[str] = None
    executor = ThreadPoolExecutor(max_workers=min(12, len(REGIONS)))
    try:
        futures = {executor.submit(probe, code): code for code in REGIONS}
        for future in as_completed(futures):
            try:
                result = future.result()
            except Exception:
                result = None
            if result:
                found = result
                break
    finally:
        # Don't block on the remaining probes once we have an answer.
        executor.shutdown(wait=False, cancel_futures=True)

    if not found:
        raise MistAuthError(
            "Could not validate this token against any Mist cloud. "
            "Check that the token is correct and still active."
        )
    return found


def region_label(code: Optional[str]) -> str:
    """Human-friendly label for a region code."""
    if not code:
        return "unknown"
    return REGION_LABELS.get(code, code)

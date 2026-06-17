"""Juniper Mist global cloud regions and their API endpoints.

Source: Juniper Mist documentation, "API Endpoints and Global Regions".
A customer can determine their region from the Mist portal URL: replace the
leading ``manage`` with ``api`` (e.g. ``manage.eu.mist.com`` -> ``api.eu.mist.com``).
"""

from __future__ import annotations

from typing import Dict, List, Optional

# Mapping of human-friendly region code -> API host.
REGIONS: Dict[str, str] = {
    "global01": "api.mist.com",
    "global02": "api.gc1.mist.com",
    "global03": "api.ac2.mist.com",
    "global04": "api.gc2.mist.com",
    "global05": "api.gc4.mist.com",
    "emea01": "api.eu.mist.com",
    "emea02": "api.gc3.mist.com",
    "emea03": "api.ac6.mist.com",
    "emea04": "api.gc6.mist.com",
    "apac01": "api.ac5.mist.com",
    "apac02": "api.gc5.mist.com",
    "apac03": "api.gc7.mist.com",
}

# Friendly labels for documentation and the setup wizard.
REGION_LABELS: Dict[str, str] = {
    "global01": "Global 01 (api.mist.com)",
    "global02": "Global 02 (api.gc1.mist.com)",
    "global03": "Global 03 (api.ac2.mist.com)",
    "global04": "Global 04 (api.gc2.mist.com)",
    "global05": "Global 05 (api.gc4.mist.com)",
    "emea01": "EMEA 01 (api.eu.mist.com)",
    "emea02": "EMEA 02 (api.gc3.mist.com)",
    "emea03": "EMEA 03 (api.ac6.mist.com)",
    "emea04": "EMEA 04 (api.gc6.mist.com)",
    "apac01": "APAC 01 (api.ac5.mist.com)",
    "apac02": "APAC 02 (api.gc5.mist.com)",
    "apac03": "APAC 03 (api.gc7.mist.com)",
}

DEFAULT_REGION = "global01"


def normalize_region(value: Optional[str]) -> str:
    """Return a canonical region code from a user-supplied value.

    Accepts a region code (``emea01``), an API host (``api.eu.mist.com``),
    or a full base URL (``https://api.eu.mist.com``). Falls back to the
    default region when ``value`` is empty.
    """
    if not value:
        return DEFAULT_REGION

    candidate = value.strip().lower()
    candidate = candidate.removeprefix("https://").removeprefix("http://")
    candidate = candidate.rstrip("/")

    # Direct region-code match.
    if candidate in REGIONS:
        return candidate

    # Match against an API host (with or without a trailing path).
    host = candidate.split("/")[0]
    for code, api_host in REGIONS.items():
        if host == api_host:
            return code

    # Accept a "manage.*" portal host by swapping the prefix.
    if host.startswith("manage."):
        api_host = "api." + host[len("manage."):]
        for code, known in REGIONS.items():
            if api_host == known:
                return code

    raise ValueError(
        f"Unknown Mist region or endpoint: {value!r}. "
        f"Valid region codes: {', '.join(REGIONS)}."
    )


def base_url(region: Optional[str]) -> str:
    """Return the HTTPS base URL for the given region."""
    code = normalize_region(region)
    return f"https://{REGIONS[code]}"


def region_choices() -> List[str]:
    """Return region codes in display order."""
    return list(REGIONS.keys())

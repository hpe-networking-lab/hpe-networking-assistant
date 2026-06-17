import io
import json
import urllib.error

import pytest

from hpe_mist_mcp import mist_client
from hpe_mist_mcp.mist_client import MistAuthError, MistClient


class FakeResp:
    """Context-manager stand-in for urllib's response object."""

    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_router(routes):
    """Return a urlopen replacement that dispatches on URL substring."""

    def _fake_urlopen(req, timeout=None):
        url = req.full_url
        for fragment, payload in routes.items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                return FakeResp(payload)
        raise AssertionError(f"Unexpected URL: {url}")

    return _fake_urlopen


SELF = {
    "email": "eng@example.com",
    "privileges": [
        {"scope": "org", "org_id": "org-1", "name": "Acme", "role": "admin"},
        {"scope": "org", "org_id": "org-1", "name": "Acme", "role": "admin"},
        {"scope": "site", "site_id": "site-x"},
    ],
}

INVENTORY_AP = [
    {"mac": "aa", "type": "ap", "connected": True, "site_id": "s1", "model": "AP45"},
    {"mac": "bb", "type": "ap", "connected": False, "site_id": "s1", "model": "AP45"},
]


def test_token_required():
    with pytest.raises(MistAuthError):
        MistClient(token="", region="global01")


def test_get_organizations_dedupes(monkeypatch):
    monkeypatch.setattr(mist_client.urllib.request, "urlopen", make_router({"/self": SELF}))
    c = MistClient(token="t", region="global01")
    orgs = c.get_organizations()
    assert orgs == [{"org_id": "org-1", "name": "Acme", "role": "admin"}]


def test_offline_access_points(monkeypatch):
    routes = {"/inventory": INVENTORY_AP}
    monkeypatch.setattr(mist_client.urllib.request, "urlopen", make_router(routes))
    c = MistClient(token="t", region="global01")
    offline = c.get_offline_access_points("org-1")
    assert len(offline) == 1
    assert offline[0]["mac"] == "bb"


def test_auth_error_maps_401(monkeypatch):
    err = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b""))
    monkeypatch.setattr(mist_client.urllib.request, "urlopen", make_router({"/self": err}))
    c = MistClient(token="t", region="global01")
    with pytest.raises(MistAuthError):
        c.get_self()


def test_base_url_region(monkeypatch):
    c = MistClient(token="t", region="api.eu.mist.com")
    assert c.base_url == "https://api.eu.mist.com"

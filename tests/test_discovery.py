"""Tests for automatic Mist region detection."""

import pytest

from hpe_mist_mcp import discovery
from hpe_mist_mcp.mist_client import MistAuthError, MistError


def _fake_client_factory(good_region):
    class FakeClient:
        def __init__(self, token, region=None, timeout=None, retries=0):
            self.region = region

        def get_self(self):
            if self.region == good_region:
                return {"email": "eng@example.com"}
            raise MistAuthError("wrong cloud")

    return FakeClient


def test_discover_region_finds_authenticating_cloud(monkeypatch):
    monkeypatch.setattr(discovery, "MistClient", _fake_client_factory("emea01"))
    assert discovery.discover_region("token") == "emea01"


def test_discover_region_raises_when_none_match(monkeypatch):
    monkeypatch.setattr(discovery, "MistClient", _fake_client_factory("does-not-exist"))
    with pytest.raises(MistError):
        discovery.discover_region("token")


def test_discover_region_requires_token():
    with pytest.raises(MistAuthError):
        discovery.discover_region("")


def test_region_label():
    assert "EMEA" in discovery.region_label("emea01")
    assert discovery.region_label(None) == "unknown"

"""Tests for opt-in write mode: gating, confirmation, and request construction."""

import json

import pytest

from hpe_mist_mcp import mist_client, server
from hpe_mist_mcp.config import Config, _as_bool
from hpe_mist_mcp.mist_client import MistClient, MistReadOnlyError


class CaptureResp:
    def __init__(self, payload=None):
        self._data = json.dumps(payload or {}).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def capturing_urlopen(captured):
    def _fake(req, timeout=None):
        captured.append({
            "method": req.get_method(),
            "url": req.full_url,
            "body": json.loads(req.data.decode()) if req.data else None,
        })
        return CaptureResp({"ok": True})
    return _fake


# -- config -------------------------------------------------------------------

def test_as_bool():
    assert _as_bool("true") and _as_bool("1") and _as_bool("YES") and _as_bool(True)
    assert not _as_bool("false") and not _as_bool("") and not _as_bool(None)


def test_config_default_read_only():
    assert Config().write_enabled is False


# -- client gating ------------------------------------------------------------

def test_read_only_client_blocks_writes():
    c = MistClient(token="t", region="global01", read_only=True)
    with pytest.raises(MistReadOnlyError):
        c.rename_device("s1", "d1", "AP-Lobby")


def test_write_request_construction(monkeypatch):
    captured = []
    monkeypatch.setattr(mist_client.urllib.request, "urlopen", capturing_urlopen(captured))
    c = MistClient(token="t", region="global01", read_only=False)

    c.rename_device("s1", "d1", "AP-Lobby")
    c.restart_device("s1", "d1")
    c.set_device_led("s1", "d1", True)
    c.claim_devices("org-1", ["ABC123"])
    c.assign_devices_to_site("org-1", "s1", ["AA:BB:CC:DD:EE:FF"])

    assert captured[0]["method"] == "PUT" and captured[0]["body"] == {"name": "AP-Lobby"}
    assert captured[1]["method"] == "POST" and captured[1]["url"].endswith("/devices/d1/restart")
    assert captured[2]["body"] == {"led": {"enabled": True}}
    assert captured[3]["method"] == "POST" and captured[3]["body"] == ["ABC123"]
    assign = captured[4]
    assert assign["method"] == "PUT" and assign["body"]["op"] == "assign"
    assert assign["body"]["macs"] == ["aabbccddeeff"]  # normalized


def test_writes_not_retried(monkeypatch):
    c = MistClient(token="t", region="global01", read_only=False, retries=3)
    # method-level retry cap is 0 for non-GET — verified indirectly: a single
    # POST that raises a network error should not loop.
    calls = {"n": 0}

    def boom(req, timeout=None):
        calls["n"] += 1
        raise mist_client.urllib.error.URLError("down")

    monkeypatch.setattr(mist_client.urllib.request, "urlopen", boom)
    with pytest.raises(mist_client.MistError):
        c.restart_device("s1", "d1")
    assert calls["n"] == 1


# -- server confirmation + registry ------------------------------------------

def test_write_tools_registry_has_five():
    assert set(server.WRITE_TOOLS) == {
        "rename_device", "reboot_device", "locate_device",
        "claim_devices", "assign_devices_to_site",
    }
    for meta in server.WRITE_TOOLS.values():
        assert "confirm" in meta["schema"]["properties"]


def test_write_tool_requires_confirm():
    with pytest.raises(ValueError):
        server.tool_rename_device(site_id="s1", device_id="d1", name="X", confirm=False)


def test_write_tool_runs_with_confirm(monkeypatch):
    class FakeClient:
        read_only = False

        def rename_device(self, site_id, device_id, name):
            return {"id": device_id, "name": name}

    monkeypatch.setattr(server, "_client", FakeClient())
    out = server.tool_rename_device(site_id="s1", device_id="d1", name="AP-Lobby", confirm=True)
    assert out["action"] == "rename_device"
    assert out["result"]["name"] == "AP-Lobby"

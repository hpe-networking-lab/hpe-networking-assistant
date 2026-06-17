"""Tests for the start_setup onboarding tool."""

from hpe_mist_mcp import server


class FakeClient:
    read_only = True

    def __init__(self, orgs=None):
        self._orgs = orgs if orgs is not None else [
            {"org_id": "o1", "name": "Acme", "role": "admin"}
        ]

    def get_self(self):
        return {"email": "eng@example.com"}

    def get_organizations(self):
        return self._orgs

    def get_sites(self, org_id):
        return [{"id": "s1", "name": "HQ"}, {"id": "s2", "name": "Branch"}]

    def get_access_points(self, org_id):
        return [{"mac": "aa", "connected": True}]

    def get_switches(self, org_id):
        return [{"mac": "bb", "connected": True}]


def _prime(monkeypatch, orgs=None, token="tok", region="global01"):
    monkeypatch.setattr(server._config, "token", token)
    monkeypatch.setattr(server._config, "region", region)
    monkeypatch.setattr(server._config, "org_id", None)
    monkeypatch.setattr(server, "save_discovery", lambda **k: None)
    monkeypatch.setattr(server, "_client", FakeClient(orgs))


def test_start_setup_single_org_ready(monkeypatch):
    _prime(monkeypatch)
    out = server.tool_start_setup()
    assert out["status"] == "READY FOR USE"
    assert out["summary"]["organization"] == "Acme"
    assert out["summary"]["site_count"] == 2
    assert "READY FOR USE" in out["report"]


def test_start_setup_multi_org_needs_input(monkeypatch):
    _prime(monkeypatch, orgs=[
        {"org_id": "o1", "name": "Acme"},
        {"org_id": "o2", "name": "Beta Corp"},
    ])
    out = server.tool_start_setup()
    assert out["status"] == "NEEDS INPUT"
    assert set(out["organizations"]) == {"Acme", "Beta Corp"}


def test_start_setup_pick_org_by_name(monkeypatch):
    _prime(monkeypatch, orgs=[
        {"org_id": "o1", "name": "Acme"},
        {"org_id": "o2", "name": "Beta Corp"},
    ])
    out = server.tool_start_setup(organization="beta")
    assert out["status"] == "READY FOR USE"
    assert out["summary"]["organization"] == "Beta Corp"


def test_start_setup_unknown_org_name(monkeypatch):
    _prime(monkeypatch, orgs=[
        {"org_id": "o1", "name": "Acme"},
        {"org_id": "o2", "name": "Beta Corp"},
    ])
    out = server.tool_start_setup(organization="Zeta")
    assert out["status"] == "NEEDS INPUT"


def test_start_setup_no_token(monkeypatch):
    monkeypatch.setattr(server._config, "token", None)
    monkeypatch.setattr(server, "_client", None)
    out = server.tool_start_setup()
    assert out["status"] == "REQUIRES ATTENTION"


def test_start_setup_registered_as_tool():
    assert "start_setup" in server.TOOLS
    res = server.dispatch("initialize", {})
    assert "instructions" in res and "start_setup" in res["instructions"]

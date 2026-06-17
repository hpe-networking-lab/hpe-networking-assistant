"""Tests for get_status and write-access readiness detection."""

from hpe_mist_mcp import server
from hpe_mist_mcp.validation import is_write_role, run_validation


def test_is_write_role():
    assert is_write_role("admin")
    assert is_write_role("write")
    assert is_write_role("Network_Admin")
    assert not is_write_role("read")
    assert not is_write_role("helpdesk")
    assert not is_write_role(None)


class FakeClient:
    def __init__(self, role="admin", read_only=False):
        self.read_only = read_only
        self._role = role

    def get_self(self):
        return {"email": "eng@example.com"}

    def get_organizations(self):
        return [{"org_id": "o1", "name": "Acme", "role": self._role}]

    def get_sites(self, org_id):
        return [{"id": "s1", "name": "HQ"}]

    def get_access_points(self, org_id):
        return [{"mac": "aa", "connected": True}]

    def get_switches(self, org_id):
        return [{"mac": "bb", "connected": True}]


def test_validation_write_access_pass():
    report = run_validation(FakeClient(role="admin", read_only=False), "o1")
    by_name = {r.name: r.passed for r in report.results}
    assert by_name["Write access"] is True
    assert report.verdict == "READY FOR USE"


def test_validation_write_access_fail_downgrades_verdict():
    report = run_validation(FakeClient(role="read", read_only=False), "o1")
    by_name = {r.name: r.passed for r in report.results}
    assert by_name["Write access"] is False
    assert report.verdict == "REQUIRES ATTENTION"


def test_validation_skips_write_check_in_read_only():
    report = run_validation(FakeClient(role="read", read_only=True), "o1")
    assert "Write access" not in {r.name for r in report.results}


def _prime(monkeypatch, role="admin", write=True, org_id="o1"):
    monkeypatch.setattr(server._config, "token", "tok")
    monkeypatch.setattr(server._config, "region", "global01")
    monkeypatch.setattr(server._config, "org_id", org_id)
    monkeypatch.setattr(server._config, "write_enabled", write)
    monkeypatch.setattr(server, "_client", FakeClient(role=role, read_only=not write))


def test_get_status_read_only(monkeypatch):
    _prime(monkeypatch, write=False)
    out = server.tool_get_status()
    assert out["mode"] == "read-only"
    assert out["write_access"]["ready"] is True


def test_get_status_write_ready(monkeypatch):
    _prime(monkeypatch, role="admin", write=True)
    out = server.tool_get_status()
    assert out["mode"] == "read-write"
    assert out["write_access"]["ready"] is True


def test_get_status_write_not_ready_gives_guidance(monkeypatch):
    _prime(monkeypatch, role="read", write=True)
    out = server.tool_get_status()
    assert out["write_access"]["ready"] is False
    assert "how_to_fix" in out["write_access"]


def test_get_status_no_token(monkeypatch):
    monkeypatch.setattr(server._config, "token", None)
    monkeypatch.setattr(server, "_client", None)
    out = server.tool_get_status()
    assert out["configured"] is False

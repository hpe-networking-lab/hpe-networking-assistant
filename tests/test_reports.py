"""Tests for report generation (builders + Markdown renderers)."""

from hpe_mist_mcp.reports import (
    build_health_report,
    build_inventory_report,
    render_health_markdown,
    render_inventory_markdown,
)


class FakeClient:
    def get_sites(self, org_id):
        return [{"id": "s1", "name": "HQ"}, {"id": "s2", "name": "Branch"}]

    def get_access_points(self, org_id):
        return [
            {"mac": "aa", "name": "AP-1", "model": "AP45", "site_id": "s1", "connected": True},
            {"mac": "bb", "name": "AP-2", "model": "AP45", "site_id": "s1", "connected": False},
            {"mac": "cc", "name": "AP-3", "model": "AP12", "site_id": "s2", "connected": True},
        ]

    def get_switches(self, org_id):
        return [{"mac": "dd", "name": "SW-1", "model": "EX4100", "site_id": "s1", "connected": True}]

    def get_org_clients(self, org_id):
        return [{"mac": "c1"}, {"mac": "c2"}]


def test_health_report_totals():
    data = build_health_report(FakeClient(), "org-1", org_name="Acme")
    t = data["totals"]
    assert t["sites"] == 2
    assert t["access_points"] == 3 and t["access_points_online"] == 2 and t["access_points_offline"] == 1
    assert t["switches"] == 1 and t["switches_online"] == 1
    assert t["wireless_clients"] == 2
    assert len(data["offline_access_points"]) == 1
    assert data["offline_access_points"][0]["site"] == "HQ"


def test_health_report_skip_clients():
    data = build_health_report(FakeClient(), "org-1", include_clients=False)
    assert data["totals"]["wireless_clients"] is None


def test_health_markdown_renders():
    md = render_health_markdown(build_health_report(FakeClient(), "org-1", org_name="Acme"))
    assert "# Network Health Report — Acme" in md
    assert "## Offline access points" in md
    assert "AP-2" in md          # the offline AP is listed
    assert "Per-site breakdown" in md


def test_health_markdown_all_online():
    class AllOnline(FakeClient):
        def get_access_points(self, org_id):
            return [{"mac": "aa", "name": "AP-1", "site_id": "s1", "connected": True}]

    md = render_health_markdown(build_health_report(AllOnline(), "org-1"))
    assert "All access points are online." in md


def test_inventory_report():
    data = build_inventory_report(FakeClient(), "org-1", org_name="Acme")
    assert data["device_count"] == 4
    md = render_inventory_markdown(data)
    assert "# Inventory Report — Acme" in md
    assert "SW-1" in md and "AP-1" in md
    assert "offline" in md  # AP-2 is offline

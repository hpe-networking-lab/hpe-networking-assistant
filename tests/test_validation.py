from hpe_mist_mcp.validation import ATTENTION, READY, run_validation


class FakeClient:
    def __init__(self, fail=None):
        self.fail = fail or set()

    def get_self(self):
        if "self" in self.fail:
            from hpe_mist_mcp.mist_client import MistAuthError
            raise MistAuthError("bad token")
        return {"email": "eng@example.com"}

    def get_organizations(self):
        return [{"org_id": "org-1", "name": "Acme", "role": "admin"}]

    def get_sites(self, org_id):
        return [{"id": "s1", "name": "HQ"}]

    def get_access_points(self, org_id):
        return [{"mac": "aa", "connected": True}]

    def get_switches(self, org_id):
        return [{"mac": "bb", "connected": True}]


def test_all_pass():
    report = run_validation(FakeClient())
    assert report.passed is True
    assert report.verdict == READY
    # auth, org, site, inventory, + informational operating-mode check
    assert len(report.results) == 5
    assert report.results[-1].name == "Operating mode"


def test_auth_failure_short_circuits():
    report = run_validation(FakeClient(fail={"self"}))
    assert report.passed is False
    assert report.verdict == ATTENTION
    assert report.results[0].name == "Authentication"
    assert len(report.results) == 1

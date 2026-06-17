import pytest

from hpe_mist_mcp.regions import base_url, normalize_region, REGIONS


def test_default_region():
    assert normalize_region(None) == "global01"
    assert normalize_region("") == "global01"


def test_region_code_passthrough():
    assert normalize_region("emea01") == "emea01"
    assert normalize_region("EMEA01") == "emea01"


def test_region_from_api_host():
    assert normalize_region("api.eu.mist.com") == "emea01"
    assert normalize_region("https://api.ac5.mist.com") == "apac01"


def test_region_from_portal_host():
    assert normalize_region("manage.eu.mist.com") == "emea01"


def test_base_url():
    assert base_url("global01") == "https://api.mist.com"
    assert base_url("emea01") == "https://api.eu.mist.com"


def test_invalid_region():
    with pytest.raises(ValueError):
        normalize_region("nope.example.com")


def test_all_regions_have_https_base():
    for code in REGIONS:
        assert base_url(code).startswith("https://api.")

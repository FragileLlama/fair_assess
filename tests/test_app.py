"""Web app tests with the county data layer stubbed out (no network)."""

import os
import tempfile
from datetime import date

import pytest

_tmp = tempfile.mkdtemp()
os.environ["APP_DB"] = f"{_tmp}/app.db"
os.environ["CACHE_DB"] = f"{_tmp}/cache.db"
os.environ["ADMIN_KEY"] = "secret"

from fastapi.testclient import TestClient  # noqa: E402

from app import analysis as A  # noqa: E402
from app import main, service  # noqa: E402
from tests.test_analysis import bldg, neighbors, val  # noqa: E402

PIN = "31364150090000"


def fake_build(pin, today=None):
    if pin == "99999999999999":
        raise service.NotSupported("This property is class 299, a condominium.")
    subj = bldg(1800)
    comps = A.pick_comps(pin, subj, neighbors(rate=8.0))
    for c in comps:
        c.address = f"{c.pin} TEST ST"
    rep = A.analyze(pin, subj, val(10 * 1800), comps, [], [], date(2026, 11, 9))
    return service.FullReport(
        pin=pin, address="347 OSAGE ST", city="PARK FOREST", zip="60466", township="Hyde Park",
        township_code="70", nbhd="70010", cls="204", municipality="Chicago", report=rep,
        history=[{"year": "2026", "mailed_tot": "21000"}], own_appeals=[],
        assessor_stats={"year": 2025, "total": 100, "won": 12},
        bor_stats={"year": 2025, "total": 200, "won": 76},
    )


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(service, "build", fake_build)
    monkeypatch.setattr(service, "warm_caches", lambda *a, **k: None)
    with TestClient(main.app) as c:
        yield c


def test_home(client):
    r = client.get("/")
    assert r.status_code == 200 and "Check my home" in r.text


def test_pin_search_redirects(client):
    r = client.get("/search", params={"q": "31-36-415-009-0000"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/p/{PIN}"


def test_report_shows_verdict_and_stats(client):
    r = client.get(f"/p/{PIN}")
    assert r.status_code == 200
    assert "Good case for an appeal" in r.text
    assert "38%" in r.text  # 76 / 200 Board of Review wins
    assert "Get my packet" in r.text


def test_unsupported_property(client):
    r = client.get("/p/99999999999999")
    assert "condominium" in r.text


def test_packet_flow_records_lead(client):
    r = client.post(f"/p/{PIN}/packet", data={"email": "Owner@Example.com"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/p/{PIN}/packet"
    r = client.get(f"/p/{PIN}/packet")
    assert "How to file" in r.text and "Requested total assessed value" in r.text
    csv = client.get("/admin/leads.csv", params={"key": "secret"}).text
    assert "owner@example.com" in csv and "strong" in csv


def test_bad_email_rejected(client):
    assert client.post(f"/p/{PIN}/packet", data={"email": "nope"}).status_code == 400


def test_admin_requires_key(client):
    assert client.get("/admin/leads.csv").status_code == 403

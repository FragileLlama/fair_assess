from datetime import date

import pytest

from app import analysis as A
from app import cookdata as C
from app import windows as W


def bldg(sqft, yb=1955, style="1 Story", bsmt="Full", gar="2 cars", ext="Frame"):
    return A.Building(sqft, yb, 2026 - yb, style, ext, bsmt, gar, 3, 2, "Central A/C", 1)


def val(bldg_av, land=3000):
    return A.Assessment(2026, "Assessor mailed", bldg_av, land, bldg_av + land)


def neighbors(rate, n=8, base=1800):
    return [(f"p{i}", bldg(base + i * 10), val(rate * (base + i * 10))) for i in range(n)]


def test_strong_uniformity_case():
    subj = bldg(1800)
    comps = A.pick_comps("me", subj, neighbors(rate=8.0))
    r = A.analyze("me", subj, val(10.0 * 1800), comps, [], [], date(2026, 11, 9))
    assert r.verdict == "strong"
    assert r.median_rate == pytest.approx(8.0)
    assert len(r.lower_comps) == len(comps)
    assert r.av_reduction == pytest.approx(2.0 * 1800)
    assert r.savings_low == pytest.approx(3600 * A.EQUALIZER * A.RATE_LOW)
    assert r.target_av == pytest.approx(3000 + 8.0 * 1800)


def test_fairly_assessed_is_unlikely_and_no_savings():
    subj = bldg(1800)
    comps = A.pick_comps("me", subj, neighbors(rate=8.0))
    r = A.analyze("me", subj, val(7.9 * 1800), comps, [], [], date(2026, 11, 9))
    assert r.verdict == "unlikely"
    assert r.av_reduction == 0 and r.savings_high == 0


def test_modest_gap_is_possible():
    subj = bldg(1800)
    comps = A.pick_comps("me", subj, neighbors(rate=8.0))
    r = A.analyze("me", subj, val(8.6 * 1800), comps, [], [], date(2026, 11, 9))
    assert r.verdict == "possible"


def test_recent_purchase_below_implied_value_is_strong():
    subj = bldg(1800)
    comps = A.pick_comps("me", subj, neighbors(rate=8.0))
    value = val(8.0 * 1800, land=3000)  # implied MV = 174,000
    own = [A.Sale("me", date(2025, 6, 1), 140_000)]
    r = A.analyze("me", subj, value, comps, [], own, date(2026, 11, 9))
    assert r.verdict == "strong"
    assert r.own_sale is not None
    assert r.av_reduction == pytest.approx(value.total - 14_000)


def test_old_purchase_ignored():
    subj = bldg(1800)
    comps = A.pick_comps("me", subj, neighbors(rate=8.0))
    own = [A.Sale("me", date(2019, 6, 1), 100_000)]
    r = A.analyze("me", subj, val(8.0 * 1800), comps, [], own, date(2026, 11, 9))
    assert r.own_sale is None


def test_sales_evidence_makes_possible():
    subj = bldg(1800)
    comps = A.pick_comps("me", subj, neighbors(rate=8.0))
    sales = [A.Sale(f"s{i}", date(2026, 3, 1), 150_000, 1800) for i in range(6)]
    value = val(8.0 * 1800, land=6000)  # implied MV 204,000 vs ~150,000 from sales
    r = A.analyze("me", subj, value, comps, sales, [], date(2026, 11, 9))
    assert r.verdict == "possible"
    assert r.sales_estimate == pytest.approx(150_000)
    # 150,000 is 26% below the implied 204,000 (not "36% lower")
    assert any("(26% lower)" in reason for reason in r.reasons)


def test_too_few_comps_is_insufficient():
    subj = bldg(1800)
    r = A.analyze("me", subj, val(9 * 1800), [], [], [], date(2026, 11, 9))
    assert r.verdict == "insufficient"


def test_pick_comps_filters_dissimilar_and_subject():
    subj = bldg(1800, yb=1955)
    nb = [
        ("me", bldg(1800), val(1)),
        ("huge", bldg(3500), val(1)),
        ("new", bldg(1800, yb=2010), val(1)),
        ("ok", bldg(1850, yb=1958), val(1)),
    ]
    assert [c.pin for c in A.pick_comps("me", subj, nb)] == ["ok"]


def test_building_sums_multiple_cards():
    cards = [
        {"card": "1.0", "char_bldg_sf": "1200.0", "char_yrblt": "1950.0", "char_type_resd": "1 Story"},
        {"card": "2.0", "char_bldg_sf": "600.0", "char_yrblt": "1980.0"},
    ]
    b = A.building(cards, 2026)
    assert b.sqft == 1800 and b.year_built == 1950 and b.cards == 2


@pytest.mark.parametrize("text,pin", [
    ("31-36-415-009-0000", "31364150090000"),
    ("31364150090000", "31364150090000"),
    ("31-36-415-009", "31364150090000"),
    ("347 Osage St", None),
])
def test_normalize_pin(text, pin):
    assert C.normalize_pin(text) == pin


def test_normalize_street():
    assert C.normalize_street("1234 West 103rd Street, Chicago, IL") == ("1234 W 103RD ST", "CHICAGO")
    assert C.normalize_street("347 osage st") == ("347 OSAGE ST", "")


def test_window_status():
    s = W.status("Hyde Park", date(2026, 11, 20))
    assert s["state"] == "open" and s["days_left"] == 18
    assert W.status("Hyde Park", date(2026, 12, 9))["state"] == "closed"
    assert W.status("Rich", date(2026, 11, 20))["state"] == "closed"

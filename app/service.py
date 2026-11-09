"""Fetch everything for one PIN and run the analysis."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime

from . import analysis as A
from . import cookdata as C

_pool = ThreadPoolExecutor(max_workers=8)
_EMPTY_STATS = {"year": None, "total": 0, "won": 0}


def township_stats(township_code: str, assessment_year: int) -> tuple[dict, dict]:
    """(assessor_stats, bor_stats) for a township. Cached for 30 days."""
    try:
        a = C.assessor_appeal_stats_all(assessment_year - 1).get(township_code, _EMPTY_STATS)
    except C.DataError:
        a = _EMPTY_STATS
    try:
        b = C.bor_appeal_stats_all(C.latest_bor_year()).get(township_code, _EMPTY_STATS)
    except C.DataError:
        b = _EMPTY_STATS
    return a, b


def warm_caches(assessment_year: int | None = None) -> None:
    """Pre-load the slow, county-wide stats so the first visitor doesn't wait."""
    from datetime import date as _d
    township_stats("", assessment_year or _d.today().year)


class NotSupported(Exception):
    pass


@dataclass
class FullReport:
    pin: str
    address: str
    city: str
    zip: str
    township: str
    township_code: str
    nbhd: str
    cls: str
    municipality: str
    report: A.Report
    history: list[dict]
    own_appeals: list[dict]
    assessor_stats: dict
    bor_stats: dict
    comp_addresses: dict[str, str] = field(default_factory=dict)


def _sale(row: dict, sqft: float | None = None) -> A.Sale | None:
    try:
        d = datetime.fromisoformat(row["sale_date"][:19]).date()
        price = float(row["sale_price"])
    except (KeyError, ValueError, TypeError):
        return None
    return A.Sale(row["pin"], d, price, sqft)


def build(pin: str, today: date | None = None) -> FullReport:
    uni = C.universe(pin)
    if not uni:
        raise LookupError("We couldn't find that property in the Assessor's records.")
    cls = uni.get("class", "")
    if cls not in A.SUPPORTED_CLASSES:
        raise NotSupported(
            "Right now we only check houses and 2-6 unit buildings (property class 2xx). "
            f"This property is class {cls}"
            + (", a condominium." if cls == "299" else ".")
        )

    f_hist = _pool.submit(C.assessed_history, pin)
    f_year = _pool.submit(C.latest_chars_year, pin)
    f_own_sales = _pool.submit(C.own_sales, pin)
    f_own_appeals = _pool.submit(C.own_appeals, pin)

    history = f_hist.result()
    if not history:
        raise LookupError("No assessment on file for this property.")
    latest = history[0]
    value = A.assessor_value(latest)
    if not value:
        raise LookupError("No assessed value has been published for this property yet.")
    nbhd = latest.get("nbhd") or uni.get("nbhd_code")
    tcode = uni.get("township_code") or latest.get("township_code", "")
    f_stats = _pool.submit(township_stats, tcode, value.year)

    chars_year = f_year.result() or value.year
    # Neighborhood values and sales don't depend on each other: fetch together.
    f_nb = _pool.submit(C.neighborhood_values, nbhd, cls, value.year)
    f_sales = _pool.submit(C.neighborhood_sales, nbhd, cls, value.year - 2)
    nb_rows, sale_rows = f_nb.result(), f_sales.result()

    nb_values = {r["pin"]: A.assessor_value(r) for r in nb_rows}
    all_pins = list({pin, *nb_values, *(r["pin"] for r in sale_rows)})
    chars = C.characteristics(all_pins, chars_year)

    subject = A.building(chars.get(pin, []), value.year)
    if not subject:
        raise LookupError("The Assessor has no building characteristics on file for this property.")

    # Comparable homes: same neighborhood and class, same assessment year.
    neighbors = []
    for p, v in nb_values.items():
        b = A.building(chars.get(p, []), value.year)
        if b and v:
            neighbors.append((p, b, v))
    comps = A.pick_comps(pin, subject, neighbors)

    # Sales in the neighborhood over ~2 years, with building size for $/sq ft.
    sales = []
    for r in sale_rows:
        b = A.building(chars.get(r["pin"], []), value.year)
        s = _sale(r, b.sqft if b else None)
        if s:
            sales.append(s)
    own = [s for s in (_sale(r, subject.sqft) for r in f_own_sales.result()) if s]

    rep = A.analyze(pin, subject, value, comps, sales, own, today)

    addrs = C.addresses([pin] + [c.pin for c in comps])
    for c in comps:
        c.address = addrs.get(c.pin, {}).get("prop_address_full", "")
    me = addrs.get(pin, {})
    assessor_stats, bor_stats = f_stats.result()

    return FullReport(
        pin=pin,
        address=me.get("prop_address_full", ""),
        city=me.get("prop_address_city_name", ""),
        zip=me.get("prop_address_zipcode_1", ""),
        township=uni.get("township_name", ""),
        township_code=tcode,
        nbhd=nbhd,
        cls=cls,
        municipality=(uni.get("cook_municipality_name") or "").title(),
        report=rep,
        history=history,
        own_appeals=f_own_appeals.result(),
        assessor_stats=assessor_stats,
        bor_stats=bor_stats,
    )

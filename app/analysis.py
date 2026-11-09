"""Decide whether a Cook County home has an appeal case, and build the evidence.

Two standard residential arguments:

1. Uniformity: similar homes nearby are assessed lower per square foot of
   building than yours. Built from the Assessor's own characteristics and values.
2. Market value: the Assessor's implied market value (residential assessed
   value is 10% of market value) is above what similar homes sell for, or
   above what you paid in a recent arm's-length purchase.

Everything here is a pure function of the fetched rows so it can be tested offline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from statistics import median

LEVEL_OF_ASSESSMENT = 0.10  # Cook County residential: AV = 10% of market value
# Rough tax-bill math for the savings estimate: AV x state equalizer x local rate.
# The equalizer has been ~3.0 in recent years; composite rates run ~6% (Chicago)
# to ~12%+ (south suburbs). We show a range, never a promise.
EQUALIZER = 3.0
RATE_LOW, RATE_HIGH = 0.06, 0.12

SUPPORTED_CLASSES = {
    "202", "203", "204", "205", "206", "207", "208", "209", "210", "211", "212", "234", "278", "295",
}


def num(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


@dataclass
class Assessment:
    year: int
    stage: str  # "Board of Review", "Assessor certified", "Assessor mailed"
    bldg: float
    land: float
    total: float


def assessor_value(row: dict) -> Assessment | None:
    """The Assessor's latest stage for a year (what a Board of Review appeal argues against)."""
    for stage, prefix in (("Assessor certified", "certified"), ("Assessor mailed", "mailed")):
        tot = num(row.get(f"{prefix}_tot"))
        if tot:
            return Assessment(
                int(float(row["year"])), stage,
                num(row.get(f"{prefix}_bldg")) or 0, num(row.get(f"{prefix}_land")) or 0, tot,
            )
    return None


def final_value(row: dict) -> Assessment | None:
    tot = num(row.get("board_tot"))
    if tot:
        return Assessment(int(float(row["year"])), "Board of Review",
                          num(row.get("board_bldg")) or 0, num(row.get("board_land")) or 0, tot)
    return assessor_value(row)


@dataclass
class Building:
    sqft: float
    year_built: int | None
    age: int | None
    style: str
    exterior: str
    basement: str
    garage: str
    beds: float | None
    baths: float | None
    air: str
    cards: int


def building(cards: list[dict], as_of: int) -> Building | None:
    if not cards:
        return None
    sqft = sum(num(c.get("char_bldg_sf")) or 0 for c in cards)
    if sqft <= 0:
        return None
    c = sorted(cards, key=lambda c: num(c.get("card")) or 0)[0]
    yb = num(c.get("char_yrblt"))
    full, half = num(c.get("char_fbath")) or 0, num(c.get("char_hbath")) or 0
    return Building(
        sqft=sqft,
        year_built=int(yb) if yb else None,
        age=as_of - int(yb) if yb else None,
        style=c.get("char_type_resd") or "",
        exterior=c.get("char_ext_wall") or "",
        basement=c.get("char_bsmt") or "",
        garage=c.get("char_gar1_size") or "",
        beds=num(c.get("char_beds")),
        baths=full + 0.5 * half if (full or half) else None,
        air=c.get("char_air") or "",
        cards=len(cards),
    )


@dataclass
class Comp:
    pin: str
    bldg: Building
    value: Assessment
    similarity: float  # lower = more similar
    address: str = ""

    @property
    def bldg_av_per_sf(self) -> float:
        return self.value.bldg / self.bldg.sqft


def pick_comps(
    subject_pin: str,
    subject: Building,
    neighbors: list[tuple[str, Building, Assessment]],
    n: int = 10,
) -> list[Comp]:
    """Most similar homes in the same neighborhood and class."""
    scored = []
    for pin, b, v in neighbors:
        if pin == subject_pin or v.bldg <= 0:
            continue
        size_diff = abs(b.sqft - subject.sqft) / subject.sqft
        if size_diff > 0.30:
            continue
        age_diff = abs((b.age or 0) - (subject.age or 0)) if b.age is not None and subject.age is not None else 15
        if age_diff > 25:
            continue
        score = size_diff * 100 + age_diff * 0.6
        score += 5 if b.style != subject.style else 0
        score += 3 if b.basement != subject.basement else 0
        score += 2 if b.garage != subject.garage else 0
        score += 2 if b.exterior != subject.exterior else 0
        scored.append(Comp(pin, b, v, round(score, 1)))
    scored.sort(key=lambda c: c.similarity)
    return scored[:n]


@dataclass
class Sale:
    pin: str
    date: date
    price: float
    sqft: float | None = None

    @property
    def per_sf(self) -> float | None:
        return self.price / self.sqft if self.sqft else None


@dataclass
class Report:
    pin: str
    subject: Building
    value: Assessment
    comps: list[Comp]
    sales: list[Sale]
    own_sale: Sale | None
    verdict: str  # "strong" | "possible" | "unlikely" | "insufficient"
    reasons: list[str] = field(default_factory=list)
    subject_rate: float = 0.0
    median_rate: float | None = None
    lower_comps: list[Comp] = field(default_factory=list)
    target_av: float | None = None
    av_reduction: float = 0.0
    savings_low: float = 0.0
    savings_high: float = 0.0
    implied_market_value: float = 0.0
    sales_estimate: float | None = None


def analyze(
    pin: str,
    subject: Building,
    value: Assessment,
    comps: list[Comp],
    sales: list[Sale],
    own_sales: list[Sale],
    today: date | None = None,
) -> Report:
    today = today or date.today()
    rep = Report(pin, subject, value, comps, sales, None, "insufficient")
    rep.subject_rate = value.bldg / subject.sqft
    rep.implied_market_value = value.total / LEVEL_OF_ASSESSMENT
    reductions: list[float] = []

    # 1. Uniformity
    if len(comps) >= 3:
        rep.median_rate = median(c.bldg_av_per_sf for c in comps)
        rep.lower_comps = sorted(
            [c for c in comps if c.bldg_av_per_sf < rep.subject_rate], key=lambda c: c.bldg_av_per_sf
        )
        gap = rep.subject_rate / rep.median_rate - 1
        if gap > 0:
            rep.target_av = value.land + rep.median_rate * subject.sqft
            reductions.append(value.bldg - rep.median_rate * subject.sqft)
        if gap >= 0.10 and len(rep.lower_comps) >= 3:
            rep.reasons.append(
                f"Your building is assessed at ${rep.subject_rate:,.2f}/sq ft, {gap:.0%} above the "
                f"${rep.median_rate:,.2f}/sq ft median of {len(comps)} similar homes in your neighborhood."
            )
            rep.verdict = "strong"
        elif gap >= 0.05 and len(rep.lower_comps) >= 3:
            rep.reasons.append(
                f"Your building is assessed {gap:.0%} above the median of similar nearby homes, "
                "a modest gap the Board may or may not act on."
            )
            rep.verdict = "possible"
        else:
            rep.reasons.append(
                "Similar homes nearby are assessed about the same as yours or higher per square foot."
            )
            rep.verdict = "unlikely"

    # 2. Your own recent purchase
    recent = [s for s in own_sales if (today - s.date).days <= 3 * 365 and s.price > 10_000]
    if recent:
        s = recent[0]
        rep.own_sale = s
        if s.price < rep.implied_market_value * 0.90:
            rep.reasons.append(
                f"You bought this home for ${s.price:,.0f} on {s.date:%b %d, %Y}, but the assessment "
                f"implies a market value of ${rep.implied_market_value:,.0f}. A recent arm's-length "
                "purchase is strong evidence (bring your closing statement)."
            )
            reductions.append(value.total - s.price * LEVEL_OF_ASSESSMENT)
            rep.verdict = "strong"

    # 3. Neighborhood sales, $/sq ft
    priced = [s.per_sf for s in sales if s.per_sf]
    if len(priced) >= 5:
        rep.sales_estimate = median(priced) * subject.sqft
        mv_gap = rep.implied_market_value / rep.sales_estimate - 1
        if mv_gap >= 0.15:
            pct_lower = 1 - rep.sales_estimate / rep.implied_market_value
            rep.reasons.append(
                f"Your assessment implies a market value of ${rep.implied_market_value:,.0f}; "
                f"{len(priced)} recent neighborhood sales suggest about ${rep.sales_estimate:,.0f} for a "
                f"home your size ({pct_lower:.0%} lower)."
            )
            reductions.append(value.total - rep.sales_estimate * LEVEL_OF_ASSESSMENT)
            if rep.verdict in ("unlikely", "insufficient"):
                rep.verdict = "possible"

    if rep.verdict == "insufficient":
        rep.reasons.append("Not enough similar homes in your neighborhood to compare against.")

    positive = [r for r in reductions if r > 0]
    if positive and rep.verdict in ("strong", "possible"):
        # Use the most conservative supported reduction.
        rep.av_reduction = min(positive)
        rep.savings_low = rep.av_reduction * EQUALIZER * RATE_LOW
        rep.savings_high = rep.av_reduction * EQUALIZER * RATE_HIGH
    return rep

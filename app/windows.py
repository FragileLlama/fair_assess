"""Appeal filing windows by township.

Update this every season from the official calendars:
  Board of Review: https://www.cookcountyboardofreview.com (news / township groups)
  Assessor:        https://www.cookcountyassessoril.gov/assessment-calendar-and-deadlines
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Window:
    body: str  # "Board of Review" or "Assessor"
    townships: frozenset[str]
    opens: date
    closes: date
    note: str = ""


WINDOWS: list[Window] = [
    # 2026 Board of Review, Group 4 (press release Nov 5, 2026)
    Window(
        "Board of Review",
        frozenset({"Barrington", "Bremen", "Calumet", "Hyde Park", "Lemont"}),
        date(2026, 11, 9),
        date(2026, 12, 8),
        "2026 assessment, Group 4",
    ),
    # TODO: add 2027 Assessor township windows when the 2027 calendar is published.
]


def status(township: str, today: date | None = None) -> dict:
    today = today or date.today()
    t = (township or "").strip().lower()
    mine = [w for w in WINDOWS if t in {x.lower() for x in w.townships}]
    open_now = [w for w in mine if w.opens <= today <= w.closes]
    upcoming = sorted([w for w in mine if w.opens > today], key=lambda w: w.opens)
    if open_now:
        w = open_now[0]
        return {"state": "open", "window": w, "days_left": (w.closes - today).days}
    if upcoming:
        return {"state": "upcoming", "window": upcoming[0]}
    return {"state": "closed", "window": None}


def open_townships(today: date | None = None) -> list[Window]:
    today = today or date.today()
    return [w for w in WINDOWS if w.opens <= today <= w.closes]

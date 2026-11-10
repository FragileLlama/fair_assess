"""Client for the Cook County open data portal (Socrata).

All Assessor / Board of Review data used by the app comes through here.
Responses are cached in SQLite for a day: the datasets refresh roughly weekly
and the portal rate-limits anonymous clients.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from typing import Any

import httpx

BASE = "https://datacatalog.cookcountyil.gov/resource"

DS_ASSESSED = "uzyt-m557"  # Assessor - Assessed Values
DS_CHARS = "x54s-btds"  # Assessor - Residential Single and Multi-Family Improvement Characteristics
DS_SALES = "wvhk-k5uv"  # Assessor - Parcel Sales
DS_ADDRESS = "3723-97qp"  # Assessor - Parcel Addresses
DS_UNIVERSE = "pabr-t5kh"  # Assessor - Parcel Universe (Current)
DS_APPEALS = "y282-6ig3"  # Assessor - Appeals
DS_BOR = "7pny-nedm"  # Board of Review Appeal Decision History

CACHE_TTL = 24 * 3600
STATS_TTL = 30 * 24 * 3600  # slow-changing data: appeal outcomes, parcel locations
_CACHE_PATH = os.environ.get("CACHE_DB", "cache.db")
_lock = threading.Lock()


class DataError(RuntimeError):
    pass


def _cache() -> sqlite3.Connection:
    con = sqlite3.connect(_CACHE_PATH, check_same_thread=False)
    con.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, at REAL, v TEXT)")
    return con


def query(dataset: str, _ttl: int = CACHE_TTL, **params: Any) -> list[dict]:
    """Run a SoQL query. Params are passed as $where, $select, ... (without the $)."""
    p = {f"${k}": str(v) for k, v in params.items() if v is not None}
    key = hashlib.sha256(json.dumps([dataset, sorted(p.items())]).encode()).hexdigest()
    with _lock:
        con = _cache()
        row = con.execute("SELECT at, v FROM cache WHERE k=?", (key,)).fetchone()
        con.close()
    if row and time.time() - row[0] < _ttl:
        return json.loads(row[1])

    headers = {}
    if os.environ.get("SOCRATA_APP_TOKEN"):
        headers["X-App-Token"] = os.environ["SOCRATA_APP_TOKEN"]
    last_err: Exception | None = None
    for attempt in range(3):  # the portal occasionally resets connections
        try:
            r = httpx.get(f"{BASE}/{dataset}.json", params=p, headers=headers, timeout=60)
            r.raise_for_status()
            break
        except httpx.HTTPStatusError as e:
            last_err = e
            if e.response.status_code < 500 and e.response.status_code != 429:
                break
        except httpx.HTTPError as e:
            last_err = e
        time.sleep(0.5 * 2**attempt)
    else:
        r = None
    if r is None or not r.is_success:
        raise DataError(f"Cook County data portal request failed: {last_err}") from last_err
    data = r.json()
    with _lock:
        con = _cache()
        con.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, time.time(), json.dumps(data)))
        con.commit()
        con.close()
    return data


def _q(s: str) -> str:
    """Quote a string literal for SoQL."""
    return "'" + s.replace("'", "''") + "'"


def _in(values: list[str]) -> str:
    return "(" + ",".join(_q(v) for v in values) + ")"


def chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


# --- PINs & addresses ------------------------------------------------------------

PIN_RE = re.compile(r"^\s*(\d{2})-?(\d{2})-?(\d{3})-?(\d{3})(?:-?(\d{4}))?\s*$")


def normalize_pin(text: str) -> str | None:
    m = PIN_RE.match(text or "")
    if not m:
        return None
    return "".join(m.group(i) for i in range(1, 5)) + (m.group(5) or "0000")


def format_pin(pin: str) -> str:
    return f"{pin[0:2]}-{pin[2:4]}-{pin[4:7]}-{pin[7:10]}-{pin[10:14]}"


_SUFFIXES = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR", "BOULEVARD": "BLVD",
    "LANE": "LN", "COURT": "CT", "PLACE": "PL", "PARKWAY": "PKWY", "TERRACE": "TER",
    "CIRCLE": "CIR", "HIGHWAY": "HWY",
}
_DIRS = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}


def normalize_street(text: str) -> tuple[str, str]:
    """'1234 West 103rd Street, Chicago' -> ('1234 W 103RD ST', 'CHICAGO')."""
    parts = [p.strip() for p in (text or "").upper().split(",")]
    street = re.sub(r"[^A-Z0-9 ]", " ", parts[0])
    words = [_DIRS.get(w, _SUFFIXES.get(w, w)) for w in street.split()]
    city = re.sub(r"[^A-Z ]", "", parts[1]).strip() if len(parts) > 1 else ""
    city = re.sub(r"\s+(IL|ILLINOIS)$", "", city).strip()
    return " ".join(words), city


def latest_address_year() -> int:
    rows = query(DS_ADDRESS, select="max(year) as y")
    return int(rows[0]["y"])


def search_address(text: str, limit: int = 15) -> list[dict]:
    street, city = normalize_street(text)
    if not street or not street.split()[0].isdigit():
        return []
    number, rest = street.split()[0], street.split()[1:]
    year = latest_address_year()
    where = [f"year={year}", f"starts_with(prop_address_full, {_q(number + ' ')})"]
    # Match the meaningful street words in order, ignoring direction/suffix differences.
    core = [w for w in rest if w not in _DIRS.values() and w not in _SUFFIXES.values()]
    if core:
        where.append(f"prop_address_full like {_q('%' + '%'.join(core) + '%')}")
    if city:
        where.append(f"upper(prop_address_city_name) = {_q(city)}")
    return query(
        DS_ADDRESS,
        where=" AND ".join(where),
        select="pin, prop_address_full, prop_address_city_name, prop_address_zipcode_1",
        order="prop_address_full",
        limit=limit,
    )


def addresses(pins: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not pins:
        return out
    year = latest_address_year()
    for batch in chunks(pins, 150):
        for r in query(
            DS_ADDRESS,
            where=f"year={year} AND pin in {_in(batch)}",
            select="pin, prop_address_full, prop_address_city_name, prop_address_zipcode_1",
            limit=len(batch) + 10,
        ):
            out[r["pin"]] = r
    return out


# --- Parcel data -----------------------------------------------------------------


def universe(pin: str) -> dict | None:
    rows = query(
        DS_UNIVERSE,
        where=f"pin={_q(pin)}",
        select="pin, year, class, township_name, township_code, nbhd_code, triad_name, "
        "lat, lon, cook_municipality_name",
        limit=1,
    )
    return rows[0] if rows else None


def neighborhood_coords(nbhd: str, cls: str) -> dict[str, tuple[float, float]]:
    """pin -> (lat, lon) for every parcel of a class in a neighborhood."""
    rows = query(
        DS_UNIVERSE, _ttl=STATS_TTL,
        where=f"nbhd_code={_q(nbhd)} AND class={_q(cls)}",
        select="pin, lat, lon",
        limit=10000,
    )
    out = {}
    for r in rows:
        try:
            out[r["pin"]] = (float(r["lat"]), float(r["lon"]))
        except (KeyError, TypeError, ValueError):
            pass
    return out


def assessed_history(pin: str, limit: int = 6) -> list[dict]:
    return query(DS_ASSESSED, where=f"pin={_q(pin)}", order="year DESC", limit=limit)


def characteristics(pins: list[str], year: int) -> dict[str, list[dict]]:
    """pin -> list of building cards (a parcel can have several buildings)."""
    from concurrent.futures import ThreadPoolExecutor

    out: dict[str, list[dict]] = {}
    batches = list(chunks(sorted(pins), 150))
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = ex.map(lambda b: query(DS_CHARS, where=f"year={year} AND pin in {_in(b)}", limit=5000), batches)
        for rows in results:
            for r in rows:
                out.setdefault(r["pin"], []).append(r)
    return out


def latest_chars_year(pin: str) -> int | None:
    rows = query(DS_CHARS, where=f"pin={_q(pin)}", select="max(year) as y")
    y = rows[0].get("y") if rows else None
    return int(float(y)) if y else None


def neighborhood_values(nbhd: str, cls: str, year: int) -> list[dict]:
    return query(
        DS_ASSESSED,
        where=f"year={year} AND nbhd={_q(nbhd)} AND class={_q(cls)}",
        limit=5000,
    )


def neighborhood_sales(nbhd: str, cls: str, since_year: int) -> list[dict]:
    return query(
        DS_SALES,
        where=(
            f"nbhd={_q(nbhd)} AND class={_q(cls)} AND year>={since_year} "
            "AND is_multisale=false AND sale_filter_less_than_10k=false "
            "AND sale_filter_deed_type=false AND sale_filter_same_sale_within_365=false"
        ),
        order="sale_date DESC",
        limit=500,
    )


def own_sales(pin: str) -> list[dict]:
    return query(DS_SALES, where=f"pin={_q(pin)}", order="sale_date DESC", limit=5)


def own_appeals(pin: str) -> list[dict]:
    return query(DS_APPEALS, where=f"pin={_q(pin)}", order="year DESC", limit=10)




def assessor_appeal_stats_all(year: int) -> dict[str, dict]:
    """township_code -> {year, total, won} for residential Assessor appeals."""
    rows = query(
        DS_APPEALS, _ttl=STATS_TTL,
        where=f"year={year} AND appeal_type='residential'",
        select="township_code, change, count(*) as n",
        group="township_code, change",
        limit=5000,
    )
    out: dict[str, dict] = {}
    for r in rows:
        t = out.setdefault(r.get("township_code", ""), {"year": year, "total": 0, "won": 0})
        t["total"] += int(r["n"])
        if (r.get("change") or "").lower() == "change":
            t["won"] += int(r["n"])
    return out


def bor_appeal_stats_all(year: int) -> dict[str, dict]:
    """township_code -> {year, total, won} for residential Board of Review appeals."""
    rows = query(
        DS_BOR, _ttl=STATS_TTL,
        where=f"tax_year={year} AND majorclass='Residential'",
        select="township_code, result, count(*) as n",
        group="township_code, result",
        limit=5000,
    )
    out: dict[str, dict] = {}
    for r in rows:
        t = out.setdefault(r.get("township_code", ""), {"year": year, "total": 0, "won": 0})
        t["total"] += int(r["n"])
        if r.get("result") == "Decrease":
            t["won"] += int(r["n"])
    return out


def latest_bor_year() -> int:
    rows = query(DS_BOR, _ttl=STATS_TTL, select="max(tax_year) as y")
    return int(rows[0]["y"])

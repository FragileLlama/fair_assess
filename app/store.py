"""Leads: the people who asked for a filing packet or a reminder."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get("APP_DB", "fairassess.db")


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL,
            email TEXT NOT NULL,
            name TEXT,
            pin TEXT,
            address TEXT,
            township TEXT,
            verdict TEXT,
            est_savings_low REAL,
            est_savings_high REAL,
            kind TEXT NOT NULL  -- 'packet' or 'reminder'
        )"""
    )
    return con


def add_lead(**kw) -> int:
    kw.setdefault("created_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    cols = ",".join(kw)
    with _con() as con:
        cur = con.execute(f"INSERT INTO leads ({cols}) VALUES ({','.join('?' * len(kw))})", list(kw.values()))
        return cur.lastrowid


def all_leads() -> list[dict]:
    with _con() as con:
        return [dict(r) for r in con.execute("SELECT * FROM leads ORDER BY id DESC")]

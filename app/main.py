from __future__ import annotations

import csv
import threading
from contextlib import asynccontextmanager
import io
import os
import re
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import cookdata, service, store, windows

BASE = Path(__file__).parent
BRAND = os.environ.get("BRAND_NAME", "FairAssess")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Warm the slow county-wide appeal stats in the background.
    threading.Thread(target=_warm, daemon=True).start()
    yield


def _warm():
    try:
        service.warm_caches()
    except Exception:
        pass


app = FastAPI(title=BRAND, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.globals.update(
    BRAND=BRAND, format_pin=cookdata.format_pin, today=date.today, open_windows=windows.open_townships
)
templates.env.filters["money"] = lambda v: f"${v:,.0f}" if v is not None else "—"
templates.env.filters["num"] = lambda v: f"{v:,.0f}" if v is not None else "—"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _error(request: Request, message: str, status: int = 404):
    return templates.TemplateResponse(request, "error.html", {"message": message}, status_code=status)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {})


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = ""):
    q = q.strip()
    pin = cookdata.normalize_pin(q)
    if pin:
        return RedirectResponse(f"/p/{pin}", status_code=303)
    try:
        matches = cookdata.search_address(q) if q else []
    except cookdata.DataError:
        return _error(request, "The county's data service isn't responding. Please try again in a minute.", 503)
    if len(matches) == 1:
        return RedirectResponse(f"/p/{matches[0]['pin']}", status_code=303)
    return templates.TemplateResponse(request, "search.html", {"q": q, "matches": matches})


def _report(pin: str) -> service.FullReport:
    return service.build(pin)


@app.get("/p/{pin}", response_class=HTMLResponse)
def property_page(request: Request, pin: str):
    if not re.fullmatch(r"\d{14}", pin):
        raise HTTPException(404)
    try:
        fr = _report(pin)
    except service.NotSupported as e:
        return _error(request, str(e), 200)
    except LookupError as e:
        return _error(request, str(e))
    except cookdata.DataError:
        return _error(request, "The county's data service isn't responding. Please try again in a minute.", 503)
    return templates.TemplateResponse(
        request, "report.html", {"fr": fr, "r": fr.report, "window": windows.status(fr.township)}
    )


@app.post("/p/{pin}/packet")
def request_packet(pin: str, email: str = Form(...), name: str = Form(""), kind: str = Form("packet")):
    if not re.fullmatch(r"\d{14}", pin):
        raise HTTPException(404)
    if not EMAIL_RE.match(email.strip()):
        raise HTTPException(400, "Please enter a valid email address.")
    try:
        fr = _report(pin)
        r = fr.report
        extra = dict(address=f"{fr.address}, {fr.city}", township=fr.township, verdict=r.verdict,
                     est_savings_low=r.savings_low, est_savings_high=r.savings_high)
    except Exception:
        extra = {}
    store.add_lead(email=email.strip().lower(), name=name.strip() or None, pin=pin,
                   kind="reminder" if kind == "reminder" else "packet", **extra)
    if kind == "reminder":
        return RedirectResponse(f"/p/{pin}?reminder=1", status_code=303)
    return RedirectResponse(f"/p/{pin}/packet", status_code=303)


@app.get("/p/{pin}/packet", response_class=HTMLResponse)
def packet(request: Request, pin: str):
    if not re.fullmatch(r"\d{14}", pin):
        raise HTTPException(404)
    try:
        fr = _report(pin)
    except (LookupError, service.NotSupported) as e:
        return _error(request, str(e))
    except cookdata.DataError:
        return _error(request, "The county's data service isn't responding. Please try again in a minute.", 503)
    return templates.TemplateResponse(
        request, "packet.html", {"fr": fr, "r": fr.report, "window": windows.status(fr.township)}
    )


@app.get("/exemptions", response_class=HTMLResponse)
def exemptions(request: Request):
    return templates.TemplateResponse(request, "exemptions.html", {})


@app.get("/admin/leads.csv")
def leads_csv(key: str = ""):
    if not ADMIN_KEY or key != ADMIN_KEY:
        raise HTTPException(403)
    rows = store.all_leads()
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="leads.csv"'})

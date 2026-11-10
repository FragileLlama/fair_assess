# FairAssess

Free Cook County property tax assessment check, plus a ready-to-file appeal packet.

A homeowner enters an address. The app pulls the Cook County Assessor's public data (values, building characteristics, sales, appeal outcomes), finds the most similar homes in the same assessment neighborhood, and gives an honest verdict: **good case / possible / unlikely**. It also estimates yearly savings. If there's a case, the owner enters an email and gets a printable evidence packet with step-by-step instructions to file at the Board of Review themselves, for free.

In a test on 22 random Hyde Park homes (November 2026): 23% had a strong case, 14% a possible case, and 64% were fairly assessed.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
ADMIN_KEY=choose-a-secret .venv/bin/uvicorn app.main:app --reload
```

Open http://localhost:8000 and try `7925 S Kingston Ave, Chicago`.

- Tests (offline): `.venv/bin/python -m pytest -q`
- Leads export: `http://localhost:8000/admin/leads.csv?key=choose-a-secret`

The first lookup of a home takes about 10 seconds, because the county's servers are slow. After that, results are cached for 24 hours. Appeal statistics are cached for 30 days and preloaded at startup.

## Configuration (environment variables)

| Variable | Purpose |
|---|---|
| `ADMIN_KEY` | Required to download leads. Set a long random value. |
| `BRAND_NAME` | Site name (default `FairAssess`). |
| `APP_DB`, `CACHE_DB` | SQLite file paths. Put them on a persistent volume in production. |
| `SOCRATA_APP_TOKEN` | Optional, free from the Cook County data portal. Raises rate limits. |

## Deploy (about $5/month)

Any host that runs a Dockerfile works. On Railway: New project → Deploy from GitHub repo → add a Volume mounted at `/data` → set `ADMIN_KEY` → add your domain.

## How it works

- `app/cookdata.py`: Cook County open data client with caching and retries. The datasets are listed at the top.
- `app/analysis.py`: picks comparable homes (size, age, style, condition, quality, renovation, distance), runs the uniformity and market-value tests, flags caveats, and produces the verdict and savings estimate. Pure functions, unit tested.
- `app/service.py`: fetches everything for a PIN, in parallel.
- `app/windows.py`: **filing windows. Update every season** from the Board of Review and Assessor calendars.
- `app/templates/`: landing page, report, packet, exemptions guide.

## Measuring accuracy (backtest)

`scripts/backtest.py` samples real residential appeals the Board of Review decided for a past year. It runs the model **as of that year**, using no later data, and compares each verdict to the Board's actual decision:

```bash
.venv/bin/python -m scripts.backtest --year 2025 --n 400 --write-calibration
```

With `--write-calibration` it saves win rates by verdict to `app/calibration.json`. The report then shows each user the measured track record for their verdict. Re-run it each year after the Board publishes new decisions.

## Known limits / roadmap

- [ ] Condos (class 299): needs the condo characteristics dataset
- [ ] Take payments (Stripe Payment Link) once demand is proven
- [ ] Send the packet and window reminders by email (e.g. Resend or Postmark)
- [ ] 2027 Assessor township windows (Chicago reassessment year)
- [ ] Missed-exemption checker using each owner's tax bill
- [ ] DuPage County

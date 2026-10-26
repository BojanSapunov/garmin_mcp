# Garmin training dashboard

Private web dashboard for Garmin Connect activities (FastAPI + Postgres + Bootstrap/Chart.js), plus an MCP server so Claude can analyse the same data.

- Data comes from the [Garmin Connect Personal API](https://developer.garmin.com/gc-developer-program/personal/personal-api).
- Sign-in with Google, restricted to one email address (`ALLOWED_EMAIL`). There is no sign-up.
- "Refresh from Garmin" loads only new activities and re-checks the last 14 days for edits and deletions.

## Local development

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[mcp,dev]"
.venv/bin/python scripts/local_db.py      # project-local Postgres, prints DATABASE_URL
cp .env.example .env                      # then fill in DATABASE_URL and GARMIN_API_KEY
.venv/bin/alembic upgrade head
.venv/bin/python -m app.sync              # first sync loads the full history (~1 min)
.venv/bin/uvicorn app.main:app --reload   # http://localhost:8000
.venv/bin/pytest
```

With Docker available, `docker compose up -d` starts Postgres on port 5433 instead of `scripts/local_db.py`.

## Layout

| Path | What |
|---|---|
| `app/` | FastAPI app: `sync.py` (incremental load), `stats.py` (aggregations), `auth.py` (Google sign-in) |
| `migrations/` | Alembic migrations, applied on container start |
| `garmin_mcp/` | MCP server for Claude (currently reads the local SQLite copy) |
| `.github/workflows/` | CI: lint, tests against Postgres, image build |

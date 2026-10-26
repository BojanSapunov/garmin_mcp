"""Start a project-local Postgres (no Docker needed) and print its DATABASE_URL.

    .venv/bin/python scripts/local_db.py
"""

from pathlib import Path

import pgserver

DATA_DIR = Path(__file__).resolve().parent.parent / ".pgdata"

srv = pgserver.get_server(DATA_DIR, cleanup_mode=None)  # keeps running after this script exits
for db in ("garmin", "garmin_test"):
    if not srv.psql(f"SELECT 1 FROM pg_database WHERE datname = '{db}';").strip().endswith("(1 row)"):
        srv.psql(f"CREATE DATABASE {db};")
print(f"DATABASE_URL=postgresql+psycopg://postgres@/garmin?host={DATA_DIR}")

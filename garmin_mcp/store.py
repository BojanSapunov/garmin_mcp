"""SQLite storage for activity summaries.

Common fields get their own columns for easy SQL; the full API record is kept
in raw_json so any other field is reachable via json_extract().
"""

import json
import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.environ.get("GARMIN_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "garmin.db"
FIT_DIR = DATA_DIR / "fit"

# column name -> key in the activity search response
COLUMNS = {
    "activity_name": "activityName",
    "start_time_local": "startTimeLocal",
    "start_time_gmt": "startTimeGMT",
    "distance_m": "distance",
    "duration_s": "duration",
    "moving_duration_s": "movingDuration",
    "elevation_gain_m": "elevationGain",
    "avg_speed_mps": "averageSpeed",
    "avg_hr": "averageHR",
    "max_hr": "maxHR",
    "calories": "calories",
    "training_load": "activityTrainingLoad",
    "aerobic_te": "aerobicTrainingEffect",
    "anaerobic_te": "anaerobicTrainingEffect",
    "avg_cadence_spm": "averageRunningCadenceInStepsPerMinute",
    "avg_power_w": "avgPower",
    "vo2max": "vO2MaxValue",
}

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS activities (
    activity_id INTEGER PRIMARY KEY,
    activity_type TEXT,
    {", ".join(f"{c} {'TEXT' if c.startswith(('activity_name', 'start_time')) else 'REAL'}" for c in COLUMNS)},
    fit_path TEXT,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activities_start ON activities(start_time_local);
CREATE INDEX IF NOT EXISTS idx_activities_type ON activities(activity_type);
"""


def connect(read_only: bool = False) -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if read_only:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.executescript(SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn


def upsert_activity(conn: sqlite3.Connection, a: dict) -> None:
    cols = ["activity_id", "activity_type", *COLUMNS, "raw_json"]
    values = [a["activityId"], (a.get("activityType") or {}).get("typeKey"),
              *(a.get(k) for k in COLUMNS.values()), json.dumps(a)]
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols[1:])
    conn.execute(
        f"INSERT INTO activities ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT(activity_id) DO UPDATE SET {updates}",
        values,
    )


def known_ids(conn: sqlite3.Connection) -> set[int]:
    return {r[0] for r in conn.execute("SELECT activity_id FROM activities")}

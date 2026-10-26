"""MCP server exposing Garmin Connect activities to Claude."""

from mcp.server.mcpserver import MCPServer

from app.garmin import GarminClient

from . import store, sync

MAX_ROWS = 500

mcp = MCPServer(
    name="garmin",
    instructions=(
        "Garmin Connect activity data for the user, stored in a local SQLite database. "
        "Call sync_activities first if data may be stale. Use query_activities for analysis; "
        "distances are meters, durations seconds, speeds m/s, times local unless *_gmt."
    ),
)

_client: GarminClient | None = None


def client() -> GarminClient:
    global _client
    if _client is None:
        _client = GarminClient()
    return _client


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    conn = store.connect(read_only=True)
    try:
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchmany(MAX_ROWS)]
    finally:
        conn.close()


@mcp.tool()
def sync_activities(full: bool = False) -> str:
    """Download new activities from Garmin Connect into the local database.

    Set full=True to re-fetch every activity summary (slower).
    """
    added = sync.sync_activities(client(), full=full, log=lambda *_: None)
    total = _rows(
        "SELECT COUNT(*) AS n, MIN(start_time_local) AS first, MAX(start_time_local) AS last FROM activities"
    )[0]
    return f"{added} new activities. Database now has {total['n']} activities from {total['first']} to {total['last']}."


@mcp.tool()
def query_activities(sql: str) -> list[dict]:
    """Run a read-only SQL query (SQLite dialect) against the local activity database.

    Table `activities` columns: activity_id, activity_type (e.g. running, cycling,
    trail_running, lap_swimming, strength_training), activity_name, start_time_local,
    start_time_gmt (TEXT 'YYYY-MM-DD HH:MM:SS'), distance_m, duration_s,
    moving_duration_s, elevation_gain_m, avg_speed_mps, avg_hr, max_hr, calories,
    training_load, aerobic_te, anaerobic_te, avg_cadence_spm, avg_power_w, vo2max,
    fit_path, raw_json (full Garmin record; use json_extract(raw_json, '$.field')).
    Returns at most 500 rows.
    """
    return _rows(sql)


@mcp.tool()
def list_activity_types() -> list[dict]:
    """Activity types in the database with counts and date range."""
    return _rows(
        "SELECT activity_type, COUNT(*) AS count, ROUND(SUM(distance_m)/1000, 1) AS total_km, "
        "MIN(start_time_local) AS first, MAX(start_time_local) AS last "
        "FROM activities GROUP BY activity_type ORDER BY count DESC"
    )


@mcp.tool()
def weekly_summary(activity_type: str | None = None, weeks: int = 12) -> list[dict]:
    """Per-week totals (ISO weeks starting Monday) for the last N weeks, optionally for one activity type."""
    where = "WHERE start_time_local >= date('now', ?)"
    params: tuple = (f"-{int(weeks) * 7} days",)
    if activity_type:
        where += " AND activity_type = ?"
        params += (activity_type,)
    return _rows(
        "SELECT date(start_time_local, 'weekday 0', '-6 days') AS week_start, COUNT(*) AS activities, "
        "ROUND(SUM(distance_m)/1000, 2) AS km, ROUND(SUM(duration_s)/3600, 2) AS hours, "
        "ROUND(SUM(elevation_gain_m)) AS elevation_m, ROUND(AVG(avg_hr)) AS avg_hr, "
        f"ROUND(SUM(training_load)) AS training_load FROM activities {where} "
        "GROUP BY week_start ORDER BY week_start",
        params,
    )


@mcp.tool()
def get_activity_details(activity_id: int) -> dict:
    """Fetch full details for one activity live from Garmin (summary, metadata, splits)."""
    return client().activity(activity_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

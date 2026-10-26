"""Download activities from Garmin Connect into the local database.

    python -m garmin_mcp.sync            # new activities since last sync
    python -m garmin_mcp.sync --full     # re-fetch every activity summary
    python -m garmin_mcp.sync --fit      # also download original FIT files
"""

import argparse
import io
import sys
import time
import zipfile

from app.garmin import GarminClient

from . import store

PAGE_SIZE = 99


def sync_activities(client: GarminClient, full: bool = False, log=print) -> int:
    """Page through activities newest-first; stop at the first known page unless full."""
    conn = store.connect()
    known = store.known_ids(conn)
    start = added = 0
    while True:
        page = client.search_activities(start=start, limit=PAGE_SIZE, sortOrder="desc")
        if not page:
            break
        new = [a for a in page if a["activityId"] not in known]
        for a in page if full else new:
            store.upsert_activity(conn, a)
        conn.commit()
        added += len(new)
        log(f"fetched {start + len(page)} activities ({added} new)")
        if (not full and len(new) < len(page)) or len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
        time.sleep(0.5)
    conn.close()
    return added


def sync_fit_files(client: GarminClient, limit: int | None = None, log=print) -> int:
    """Download original files for activities that don't have one yet."""
    conn = store.connect()
    store.FIT_DIR.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        "SELECT activity_id FROM activities WHERE fit_path IS NULL ORDER BY start_time_local DESC"
        + (f" LIMIT {int(limit)}" if limit else "")
    ).fetchall()
    done = 0
    for i, (activity_id,) in enumerate(rows, 1):
        try:
            data = client.activity_file(activity_id)
        except Exception as e:  # e.g. manual activities have no file
            log(f"{activity_id}: no file ({e})")
            continue
        path = store.FIT_DIR / f"{activity_id}.fit"
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                name = z.namelist()[0]
                path = path.with_suffix(name[name.rfind("."):].lower()) if "." in name else path
                path.write_bytes(z.read(name))
        except zipfile.BadZipFile:
            path.write_bytes(data)
        conn.execute("UPDATE activities SET fit_path = ? WHERE activity_id = ?", (str(path), activity_id))
        conn.commit()
        done += 1
        if i % 25 == 0:
            log(f"files: {i}/{len(rows)}")
        time.sleep(0.3)
    conn.close()
    return done


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--full", action="store_true", help="re-fetch all activity summaries")
    p.add_argument("--fit", action="store_true", help="download original FIT files")
    p.add_argument("--fit-limit", type=int, help="max number of FIT files to download this run")
    args = p.parse_args(argv)

    client = GarminClient()
    added = sync_activities(client, full=args.full)
    print(f"done: {added} new activities -> {store.DB_PATH}")
    if args.fit:
        n = sync_fit_files(client, limit=args.fit_limit)
        print(f"done: {n} files -> {store.FIT_DIR}")


if __name__ == "__main__":
    sys.exit(main())

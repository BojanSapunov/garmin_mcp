import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Activity, is_running_type
from app.sync import LOCK_ID, SyncInProgress, sync_activities
from conftest import FakeGarmin, make_activity


def run(engine, garmin, **kw):
    return sync_activities(engine, garmin, page_size=10, pause_s=0, **kw)


def names(engine) -> dict[int, str]:
    with Session(engine) as s:
        return dict(s.execute(select(Activity.activity_id, Activity.activity_name)).all())


def test_first_sync_loads_everything(engine):
    garmin = FakeGarmin([make_activity(i, days_ago=i) for i in range(1, 26)])
    result = run(engine, garmin)
    assert (result.status, result.added) == ("success", 25)
    assert garmin.requests == 3  # 10 + 10 + 5
    assert len(names(engine)) == 25


def test_second_sync_only_fetches_increment(engine):
    garmin = FakeGarmin([make_activity(i, days_ago=20 + i) for i in range(1, 26)])
    run(engine, garmin)
    garmin.activities.append(make_activity(100, days_ago=0.1))
    garmin.requests = 0
    result = run(engine, garmin)
    assert result.added == 1
    assert garmin.requests == 1  # first page had nothing older than the window left to check


def test_recent_edits_and_deletions_are_picked_up(engine):
    acts = [make_activity(i, days_ago=i) for i in range(1, 31)]
    garmin = FakeGarmin(acts)
    run(engine, garmin)

    acts[1]["activityName"] = "Renamed in Garmin"     # 2 days ago: inside the 14-day window
    garmin.activities = [a for a in acts if a["activityId"] not in (3, 25)]  # 3 days ago and 25 days ago deleted
    result = run(engine, garmin)

    stored = names(engine)
    assert result.updated == 1 and stored[2] == "Renamed in Garmin"
    assert result.deleted == 1 and 3 not in stored
    assert 25 in stored  # outside the window, so it isn't touched


def test_concurrent_sync_is_rejected(engine):
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": LOCK_ID})
        with pytest.raises(SyncInProgress):
            run(engine, FakeGarmin([]))
        conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": LOCK_ID})


def test_failed_sync_is_recorded(engine):
    class Broken(FakeGarmin):
        def search_activities(self, **_):
            raise RuntimeError("Garmin down")

    with pytest.raises(RuntimeError):
        run(engine, Broken([]))
    with Session(engine) as s:
        status, error = s.execute(text("SELECT status, error FROM sync_runs")).one()
    assert status == "error" and "Garmin down" in error


@pytest.mark.parametrize("type_key,expected", [
    ("running", True), ("trail_running", True), ("treadmill_running", True), ("track_running", True),
    ("virtual_run", True), ("cycling", False), ("walking", False), ("strength_training", False), (None, False),
])
def test_running_classification(type_key, expected):
    assert is_running_type(type_key) is expected

from datetime import datetime, timedelta

import pytest

from app import fit
from app.fit import NoDetails, build_details, extract_fit

START = datetime(2026, 10, 4, 8, 0, 0)


def run_records(km: float, pace_s: float = 300, pause_at_s: int | None = None, pause_len_s: int = 0):
    """One record per second at a steady pace, HR rising, a 10 m hill in the second km."""
    speed = 1000 / pace_s
    records, t, d = [], 0, 0.0
    while d < km * 1000:
        if pause_at_s is not None and t == pause_at_s:
            t += pause_len_s  # watch paused: no records, distance unchanged
        records.append({
            "timestamp": START + timedelta(seconds=t), "distance": d, "heart_rate": 120 + d / 100,
            "speed": speed, "altitude": 100 + (10 if 1000 <= d < 2000 else 0) * ((d - 1000) / 1000),
            "cadence": 85, "fractional_cadence": 0.5,
        })
        t += 1
        d += speed
    return records


def test_km_splits_add_up_and_have_correct_pace():
    details = build_details(run_records(3.5), [], is_running=True)
    splits = details["km_splits"]
    assert [s["km"] for s in splits] == [1, 2, 3, 4]
    assert [s["distance_m"] for s in splits[:3]] == [1000, 1000, 1000] and 450 <= splits[3]["distance_m"] <= 500
    assert all(abs(s["pace_s_km"] - 300) <= 1 for s in splits)
    assert splits[1]["elev_gain_m"] == pytest.approx(10, abs=1.5)  # the hill
    assert splits[0]["cadence_spm"] == 171  # (85 + 0.5) strides/min -> steps/min
    assert details["x_unit"] == "km"


def test_pauses_from_timer_events_are_excluded():
    records = run_records(2, pause_at_s=200, pause_len_s=120)
    pause_start = START + timedelta(seconds=200)
    events = [(START, "start"), (pause_start, "stop_all"), (pause_start + timedelta(seconds=120), "start")]
    split = build_details(records, [], events, is_running=True)["km_splits"][0]
    assert abs(split["duration_s"] - 300) <= 1  # the two-minute stop isn't counted


def test_long_gaps_count_as_pauses_without_timer_events():
    records = run_records(2, pause_at_s=200, pause_len_s=120)
    split = build_details(records, [], is_running=True)["km_splits"][0]
    assert abs(split["duration_s"] - 301) <= 1  # gap counted as one second


def test_series_are_downsampled_and_aligned():
    details = build_details(run_records(10, pace_s=240), [], is_running=True)
    series = details["series"]
    assert len(series["x"]) <= fit.MAX_POINTS
    assert len({len(v) for v in series.values()}) == 1
    assert series["x"][-1] == pytest.approx(10, abs=0.05)


def test_activity_without_distance_uses_minutes_and_has_no_km_splits():
    records = [{"timestamp": START + timedelta(seconds=i), "heart_rate": 100} for i in range(600)]
    details = build_details(records, [{"timer_time": 600, "distance": 0}], is_running=False)
    assert details["x_unit"] == "min" and details["km_splits"] == []
    assert details["laps"][0]["pace_s_km"] is None


def test_laps_convert_running_cadence():
    lap = {"distance": 1000, "timer_time": 276.1, "avg_hr": 145, "cadence": 95, "fractional_cadence": 0.0}
    out = build_details(run_records(1.2), [lap], is_running=True)["laps"][0]
    assert out["pace_s_km"] == 276 and out["cadence_spm"] == 190


def test_non_fit_files_are_rejected():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("activity.gpx", "<gpx/>")
    with pytest.raises(NoDetails, match="not FIT"):
        extract_fit(buf.getvalue())
    with pytest.raises(NoDetails):
        extract_fit(b"definitely not a fit file")

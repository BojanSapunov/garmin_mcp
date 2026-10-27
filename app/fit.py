"""Turn a Garmin activity file (FIT, usually inside a zip) into chart series, per-km splits and device laps.

Reading the FIT file (read_fit) is kept separate from the calculations (build_details), which work
on plain dicts so they can be tested without real activity files.
"""

import io
import math
import zipfile
from datetime import datetime
from functools import partial

import fitdecode

PARSER_VERSION = 2  # bump when the output changes; cached results with an older version are rebuilt
MAX_POINTS = 600  # per chart series, plenty for a phone screen
SPEED_WINDOW = 30  # records (about seconds) averaged for pace; GPS pace is noisy second to second
ALTITUDE_WINDOW = 5
CLIMB_THRESHOLD_M = 1.0  # altitude must move this far from the last turning point to count (matches Garmin)
SLOWEST_PACE_S = 15 * 60  # slower than this is standing still: no pace point
PAUSE_GAP_S = 10  # a gap between records longer than this is a pause (auto-pause, stopped watch)


class NoDetails(Exception):
    """The activity has no FIT file with recorded data (e.g. manual entry or an old GPX/TCX upload)."""


def extract_fit(data: bytes) -> bytes:
    if data[8:12] == b".FIT":
        return data
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():
                if name.lower().endswith(".fit"):
                    return z.read(name)
            raise NoDetails(f"original file is {', '.join(z.namelist()) or 'empty'}, not FIT")
    except zipfile.BadZipFile as e:
        raise NoDetails("not a FIT file") from e


def read_fit(fit: bytes) -> tuple[list[dict], list[dict], list[tuple[datetime, str]]]:
    """Records, laps and timer events (start/stop, for moving time) from a FIT file."""
    records, laps, timer_events = [], [], []
    with fitdecode.FitReader(io.BytesIO(fit), check_crc=fitdecode.CrcCheck.DISABLED) as reader:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            get = partial(frame.get_value, fallback=None)
            if frame.name == "record":
                records.append({
                    "timestamp": get("timestamp"),
                    "distance": get("distance"),
                    "heart_rate": get("heart_rate"),
                    "speed": _first(get("enhanced_speed"), get("speed")),
                    "altitude": _first(get("enhanced_altitude"), get("altitude")),
                    "cadence": get("cadence"),
                    "fractional_cadence": get("fractional_cadence"),
                    "power": get("power"),
                })
            elif frame.name == "lap":
                laps.append({
                    "distance": get("total_distance"),
                    "timer_time": get("total_timer_time"),
                    "avg_hr": get("avg_heart_rate"),
                    "max_hr": get("max_heart_rate"),
                    "avg_speed": _first(get("enhanced_avg_speed"), get("avg_speed")),
                    "ascent": get("total_ascent"),
                    "descent": get("total_descent"),
                    "cadence": _first(get("avg_running_cadence"), get("avg_cadence")),
                    "fractional_cadence": get("avg_fractional_cadence"),
                    "avg_power": get("avg_power"),
                })
            elif frame.name == "event" and get("event") == "timer" and isinstance(get("timestamp"), datetime):
                timer_events.append((get("timestamp"), str(get("event_type"))))
    return records, laps, timer_events


def parse_activity_file(data: bytes, is_running: bool) -> dict:
    return build_details(*read_fit(extract_fit(data)), is_running=is_running)


def build_details(records: list[dict], laps: list[dict], timer_events: list[tuple[datetime, str]] | None = None,
                  *, is_running: bool) -> dict:
    records = [r for r in records if isinstance(r.get("timestamp"), datetime)]
    if len(records) < 2:
        raise NoDetails("no recorded data points")

    moving = _moving_time(records, timer_events or [])

    has_distance = any((r.get("distance") or 0) > 0 for r in records)
    distance = _forward_fill([r.get("distance") for r in records], 0.0)
    speed = _rolling_mean([r.get("speed") for r in records], SPEED_WINDOW)
    altitude = _rolling_mean([r.get("altitude") for r in records], ALTITUDE_WINDOW)
    cadence = [_spm(r.get("cadence"), r.get("fractional_cadence"), is_running) for r in records]
    pace = [1000 / s if s and 1000 / s <= SLOWEST_PACE_S else None for s in speed]

    x = [d / 1000 for d in distance] if has_distance else [t / 60 for t in moving]
    series = _downsample({
        "x": x,
        "hr": [r.get("heart_rate") for r in records],
        "pace": pace,
        "elevation": altitude,
        "cadence": cadence,
    })
    return {
        "version": PARSER_VERSION,
        "x_unit": "km" if has_distance else "min",
        "series": {
            "x": _round(series["x"], 3),
            "hr": _round(series["hr"], 0),
            "pace": _round(series["pace"], 0),
            "elevation": _round(series["elevation"], 1),
            "cadence": _round(series["cadence"], 0),
        },
        "km_splits": _km_splits(distance, moving, records, _climb(altitude), cadence) if has_distance else [],
        "laps": [_lap(lap, is_running) for lap in laps if (lap.get("timer_time") or 0) > 0],
    }


def _moving_time(records: list[dict], timer_events: list[tuple[datetime, str]]) -> list[float]:
    """Timer seconds at each record, excluding pauses, so splits add up to the watch's time."""
    stamps = [r["timestamp"] for r in records]
    pauses, stopped_at = [], None
    for ts, kind in sorted(timer_events):
        if kind.startswith("stop") and stopped_at is None:
            stopped_at = ts
        elif kind == "start" and stopped_at is not None:
            pauses.append((stopped_at, ts))
            stopped_at = None
    if pauses or timer_events:
        out = []
        for ts in stamps:
            paused = sum((min(end, ts) - start).total_seconds() for start, end in pauses if start < ts)
            out.append((ts - stamps[0]).total_seconds() - paused)
        return out
    # No timer events in the file: treat gaps longer than PAUSE_GAP_S as pauses.
    out = [0.0]
    for prev, cur in zip(stamps, stamps[1:], strict=False):
        gap = (cur - prev).total_seconds()
        out.append(out[-1] + (gap if 0 <= gap <= PAUSE_GAP_S else 1.0))
    return out


def _climb(altitude: list) -> list[tuple[float, float]]:
    """Cumulative (gain, loss) at each record. Changes smaller than CLIMB_THRESHOLD_M are treated as noise."""
    out, gain, loss, anchor = [], 0.0, 0.0, None
    for a in altitude:
        if a is not None:
            if anchor is None:
                anchor = a
            elif a - anchor >= CLIMB_THRESHOLD_M:
                gain, anchor = gain + a - anchor, a
            elif anchor - a >= CLIMB_THRESHOLD_M:
                loss, anchor = loss + anchor - a, a
        out.append((gain, loss))
    return out


def _km_splits(distance, moving, records, climb, cadence) -> list[dict]:
    splits, start_i, start_t, start_d = [], 0, 0.0, 0.0
    next_km = 1000.0
    for i in range(1, len(records)):
        while distance[i] >= next_km:
            # interpolate the moment the km boundary was crossed
            d0, d1 = distance[i - 1], distance[i]
            frac = (next_km - d0) / (d1 - d0) if d1 > d0 else 1.0
            t = moving[i - 1] + frac * (moving[i] - moving[i - 1])
            splits.append(_split(len(splits) + 1, start_i, i, t - start_t, next_km - start_d, records, climb, cadence))
            start_i, start_t, start_d = i, t, next_km
            next_km += 1000.0
    rest = distance[-1] - start_d
    if rest >= 50:  # final partial km
        splits.append(_split(len(splits) + 1, start_i, len(records) - 1, moving[-1] - start_t, rest, records, climb,
                             cadence))
    return splits


def _split(km, i0, i1, duration, dist, records, climb, cadence) -> dict:
    return {
        "km": km,
        "distance_m": round(dist),
        "duration_s": round(duration, 1),
        "pace_s_km": round(duration / (dist / 1000)) if dist else None,
        "avg_hr": _mean([r.get("heart_rate") for r in records[i0:i1]], 0),
        "max_hr": max((r["heart_rate"] for r in records[i0:i1] if r.get("heart_rate")), default=None),
        "elev_gain_m": round(climb[i1][0] - climb[i0][0], 1),
        "elev_loss_m": round(climb[i1][1] - climb[i0][1], 1),
        "cadence_spm": _mean(cadence[i0:i1], 0),
    }


def _lap(lap: dict, is_running: bool) -> dict:
    dist, secs = lap.get("distance") or 0, lap["timer_time"]
    return {
        "distance_m": round(dist),
        "duration_s": round(secs, 1),
        "pace_s_km": round(secs / (dist / 1000)) if dist > 0 else None,
        "avg_hr": lap.get("avg_hr"),
        "max_hr": lap.get("max_hr"),
        "elev_gain_m": lap.get("ascent"),
        "elev_loss_m": lap.get("descent"),
        "cadence_spm": _spm(lap.get("cadence"), lap.get("fractional_cadence"), is_running),
        "avg_power_w": lap.get("avg_power"),
    }


# ---------- helpers ----------

def _first(*values):
    return next((v for v in values if v is not None), None)


def _spm(cadence, fractional, is_running):
    """FIT stores running cadence per leg (strides/min); Garmin Connect shows steps/min, i.e. double."""
    if cadence is None:
        return None
    value = cadence + (fractional or 0)
    return round(value * 2 if is_running else value)


def _forward_fill(values, start):
    out, last = [], start
    for v in values:
        last = v if v is not None else last
        out.append(last)
    return out


def _rolling_mean(values, window):
    half, out = window // 2, []
    for i in range(len(values)):
        chunk = [v for v in values[max(0, i - half):i + half + 1] if v is not None]
        out.append(sum(chunk) / len(chunk) if chunk and values[i] is not None else None)
    return out


def _downsample(series: dict[str, list]) -> dict[str, list]:
    n = len(series["x"])
    step = max(1, math.ceil(n / MAX_POINTS))
    if step == 1:
        return series
    return {k: [_mean(v[i:i + step]) for i in range(0, n, step)] for k, v in series.items()}


def _mean(values, digits=None):
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    m = sum(nums) / len(nums)
    return round(m, digits) if digits is not None else m


def _round(values, digits):
    return [None if v is None else (round(v) if digits == 0 else round(v, digits)) for v in values]

"""Activity details (chart series, splits, laps): served from the cache, or fetched from Garmin once."""

import httpx
from sqlalchemy.orm import Session

from .fit import PARSER_VERSION, NoDetails, parse_activity_file
from .garmin import GarminClient
from .models import Activity, ActivityDetails


def get_details(session: Session, activity: Activity, client: GarminClient) -> dict:
    """Returns {"available": True, ...details} or {"available": False, "reason": ...}."""
    cached = session.get(ActivityDetails, activity.activity_id)
    if cached and cached.parser_version == PARSER_VERSION:
        return {"available": True, **cached.data}
    try:
        data = parse_activity_file(client.activity_file(activity.activity_id), is_running=activity.is_running)
    except NoDetails as e:
        return {"available": False, "reason": str(e)}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:  # manual activities have no file
            return {"available": False, "reason": "Garmin has no recorded file for this activity"}
        raise
    session.merge(ActivityDetails(activity_id=activity.activity_id, parser_version=PARSER_VERSION, data=data))
    session.commit()
    return {"available": True, **data}

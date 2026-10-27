"""FastAPI app: dashboard pages, JSON endpoints for charts, and the on-demand sync."""

from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import auth, stats
from .config import get_settings
from .db import get_engine, get_session
from .details import get_details
from .garmin import GarminClient
from .models import Activity
from .sync import SyncInProgress, sync_activities

BASE_DIR = Path(__file__).resolve().parent
settings = get_settings()
settings.validate_for_startup()

app = FastAPI(title="Garmin dashboard", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="garmin_session",
    max_age=30 * 24 * 3600,
    same_site="lax",
    https_only=not settings.is_local,
)
app.include_router(auth.router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return "–"
    s = round(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _fmt_pace(seconds_per_km: float | None) -> str:
    if not seconds_per_km:
        return "–"
    s = round(seconds_per_km)
    return f"{s // 60}:{s % 60:02d}"


def _fmt_type(type_key: str | None) -> str:
    return (type_key or "unknown").replace("_", " ").capitalize()


templates.env.filters.update(duration=_fmt_duration, pace=_fmt_pace, activity_type=_fmt_type)


@app.exception_handler(auth.NotAuthenticated)
async def _redirect_to_login(request: Request, exc: auth.NotAuthenticated):
    return RedirectResponse("/login", status_code=303)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def _key_days_left() -> int | None:
    expires = settings.garmin_key_expires
    return (expires - date.today()).days if expires else None


# ---------- pages ----------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str | None = None):
    if auth.current_user(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: str = Depends(auth.require_page_user),
              session: Session = Depends(get_session)):
    return templates.TemplateResponse(request, "dashboard.html", {
        "user": user,
        "summary": stats.summary(session),
        "activities": stats.recent_activities(session),
        "last_sync": stats.last_sync(session),
        "key_days_left": _key_days_left(),
    })


@app.get("/activity/{activity_id}", response_class=HTMLResponse)
def activity_page(request: Request, activity_id: int, _: str = Depends(auth.require_page_user),
                  session: Session = Depends(get_session)):
    activity = session.get(Activity, activity_id)
    if not activity:
        raise HTTPException(404, "Activity not found")
    return templates.TemplateResponse(request, "activity.html", {"a": activity, "raw": activity.raw or {}})


# ---------- API ----------

@app.get("/api/activity/{activity_id}/details")
def api_activity_details(activity_id: int, _: str = Depends(auth.require_api_user),
                         session: Session = Depends(get_session)):
    activity = session.get(Activity, activity_id)
    if not activity:
        raise HTTPException(404, "Activity not found")
    try:
        return get_details(session, activity, GarminClient(settings.garmin_api_key))
    except Exception as e:
        return JSONResponse({"available": False, "reason": f"Could not load details from Garmin: {e}"},
                            status_code=502)


@app.get("/api/stats/distance")
def api_distance(period: str = "week", _: str = Depends(auth.require_api_user),
                 session: Session = Depends(get_session)):
    if period not in stats.PERIODS:
        raise HTTPException(400, "period must be week, month or year")
    return stats.distance_series(session, period)


@app.post("/api/sync")
def api_sync(request: Request, _: str = Depends(auth.require_api_user)):
    # Only our own fetch() sends this header; blocks cross-site form posts.
    if request.headers.get("x-requested-with") != "fetch":
        raise HTTPException(400, "Bad request")
    try:
        run = sync_activities(get_engine(), GarminClient(settings.garmin_api_key))
    except SyncInProgress:
        return JSONResponse({"status": "busy", "message": "A sync is already running"}, status_code=409)
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"Sync failed: {e}"}, status_code=502)
    return {"status": run.status, "added": run.added, "updated": run.updated, "deleted": run.deleted}


@app.get("/healthz")
def healthz(session: Session = Depends(get_session)):
    session.execute(text("SELECT 1"))
    return {"ok": True}

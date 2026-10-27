"""Plans: list, import, training and nutrition views with plan-vs-actual, weight log, new-plan briefs."""

from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import auth, plans
from .config import get_settings
from .db import get_session
from .garmin import GarminClient
from .models import Plan
from .plan_brief import PlanRequest, build_brief
from .web import require_fetch, templates, today
from .wellness import ensure_daily_summaries

router = APIRouter()


def today_summary(session: Session) -> dict | None:
    """Today's planned session and calorie target, for the dashboard."""
    day = today()
    training = plans.plan_day(session, "training", day)
    nutrition = plans.plan_day(session, "nutrition", day)
    if not training and not nutrition:
        return None
    return {"training": training, "nutrition": nutrition}


@router.get("/plans", response_class=HTMLResponse)
def plans_page(request: Request, _: str = Depends(auth.require_page_user), session: Session = Depends(get_session)):
    all_plans = list(session.scalars(select(Plan).order_by(Plan.status, Plan.start_date.desc())))
    return templates.TemplateResponse(request, "plans.html", {"plans": all_plans, "today": today()})


@router.get("/plans/{plan_id}", response_class=HTMLResponse)
def plan_page(request: Request, plan_id: int, _: str = Depends(auth.require_page_user),
              session: Session = Depends(get_session)):
    plan = session.get(Plan, plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    day = today()
    if plan.kind == "training":
        return templates.TemplateResponse(request, "plan_training.html", {
            "plan": plan, "cmp": plans.training_comparison(session, plan, day), "today": day})
    ensure_daily_summaries(session, GarminClient(get_settings().garmin_api_key), plan.start_date, plan.end_date, day)
    return templates.TemplateResponse(request, "plan_nutrition.html", {
        "plan": plan, "cmp": plans.nutrition_comparison(session, plan, day), "today": day})


@router.post("/api/plans", dependencies=[Depends(require_fetch)])
def api_import_plan(body: dict = Body(...), _: str = Depends(auth.require_api_user),
                    session: Session = Depends(get_session)):
    try:
        plan_in = plans.PlanIn.model_validate(body)
    except ValidationError as e:
        errors = [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:8]]
        return JSONResponse({"ok": False, "errors": errors}, status_code=422)
    plan = plans.import_plan(session, plan_in, source=body.get("source", "import") if body.get("source") in
                             ("import", "generated") else "import")
    return {"ok": True, "id": plan.id, "days": len(plan.days)}


@router.post("/api/plans/{plan_id}/status", dependencies=[Depends(require_fetch)])
def api_plan_status(plan_id: int, status: str = Body(..., embed=True), _: str = Depends(auth.require_api_user),
                    session: Session = Depends(get_session)):
    plan = session.get(Plan, plan_id)
    if not plan or status not in ("active", "archived"):
        raise HTTPException(404 if not plan else 400)
    plan.status = status
    session.commit()
    return {"ok": True}


@router.delete("/api/plans/{plan_id}", dependencies=[Depends(require_fetch)])
def api_delete_plan(plan_id: int, _: str = Depends(auth.require_api_user), session: Session = Depends(get_session)):
    plan = session.get(Plan, plan_id)
    if not plan:
        raise HTTPException(404)
    session.delete(plan)
    session.commit()
    return {"ok": True}


@router.post("/api/plans/brief", dependencies=[Depends(require_fetch)])
def api_plan_brief(req: PlanRequest, _: str = Depends(auth.require_api_user), session: Session = Depends(get_session)):
    if req.end_date < req.start_date:
        raise HTTPException(422, "end date is before start date")
    return {"brief": build_brief(session, req, today())}


class WeightIn(BaseModel):
    date: date
    weight_kg: float = Field(ge=30, le=250)


@router.post("/api/weight", dependencies=[Depends(require_fetch)])
def api_weight(entry: WeightIn, _: str = Depends(auth.require_api_user), session: Session = Depends(get_session)):
    if entry.date > today():
        raise HTTPException(422, "date is in the future")
    plans.record_weight(session, entry.date, entry.weight_kg)
    return {"ok": True}

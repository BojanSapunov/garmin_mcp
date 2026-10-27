"""Import plan JSON files into the database in DATABASE_URL (for local use; in Azure use the Plans page).

    .venv/bin/python scripts/import_plan.py data/plans/my-plan.json [more.json ...]
"""

import json
import sys

from sqlalchemy.orm import Session

from app.db import get_engine
from app.plans import PlanIn, import_plan

with Session(get_engine()) as session:
    for path in sys.argv[1:]:
        plan = import_plan(session, PlanIn.model_validate(json.load(open(path, encoding="utf-8"))))
        print(f"{path}: plan {plan.id} '{plan.name}', {len(plan.days)} days")

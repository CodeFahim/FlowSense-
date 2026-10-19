"""Cash flow history (from the ledger) and the optional 12-month turnover forecast. Read-only."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import Ctx, get_ctx
from app.db import get_db
from app.domain.records import amount_minor
from app.domain.workspace import require_configured
from app.errors import ApiError
from app.planning import history as H
from app.planning import turnover as T

router = APIRouter(prefix="/api")
DB = Annotated[Session, Depends(get_db)]


@router.get("/cashflow/history")
def cashflow_history(
    db: DB,
    ctx: Ctx = Depends(get_ctx),
    start: date | None = Query(default=None, alias="from"),
    end: date | None = Query(default=None, alias="to"),
    direction: Literal["all", "in", "out"] = "all",
    category: str | None = Query(default=None, max_length=40),
) -> dict[str, Any]:
    require_configured(ctx.business)
    if category is not None and category not in H.CATEGORIES:
        raise ApiError(422, "CATEGORY_UNKNOWN", "Choose a category from the list.")
    if start and end and end < start:
        raise ApiError(422, "DATES_INVALID", "The end date can't be before the start date.")
    return H.history(db, ctx.business, start=start, end=end, direction=direction, category=category)


@router.get("/forecast/turnover")
def turnover(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    require_configured(ctx.business)
    return T.forecast(db, ctx.business)


class AssumptionsIn(BaseModel):
    expected_monthly_sales: str = Field(min_length=1, max_length=32)
    yearly_growth_pct: Decimal = Field(default=Decimal(0), ge=Decimal(-50), le=Decimal(200), max_digits=6, decimal_places=2)


@router.post("/forecast/turnover")
def turnover_with_assumptions(body: AssumptionsIn, db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    """Illustrative forecast from the owner's own assumptions. Read-only: nothing is stored."""
    biz = ctx.business
    require_configured(biz)
    minor = amount_minor(body.expected_monthly_sales, biz.ccy, biz)
    return T.forecast(db, biz, expected_monthly_minor=minor, yearly_growth_pct=body.yearly_growth_pct)

"""Optional 12-month turnover forecast.

Turnover = sales revenue: the value of sales you invoiced, by the month the invoice was issued. It is not
the cash balance (money in the bank) and not profit (sales minus costs).

Method (deterministic, integer money, documented in the response):
- Sales history = invoice amounts per calendar month (business timezone), up to the last 24 complete
  months, starting at the first month with a sale. Months in that range with no sales count as zero.
- With at least MIN_MONTHS complete months:
    base level   = average of the last up to 6 complete months;
    trend        = least-squares slope over the last up to 12 complete months, as a share of the base
                   level per month, limited to -5% .. +5% a month;
    base month k = base level x (1 + trend)^k   for k = 1..12 (k = 1 is next month);
    spread       = coefficient of variation of monthly sales (std / mean), limited to 10% .. 50%;
    low / high   = base x (1 - spread) / base x (1 + spread).
- With fewer months, nothing is invented: the owner gives expected monthly sales and an optional yearly
  growth rate, and the result is labelled illustrative (an assumed range of +/-25%, not a statistical
  confidence interval). The yearly rate is an effective annual rate: the monthly rate is
  (1 + yearly)^(1/12) - 1, so 12% a year makes month 12 exactly 12% above the starting level.
Estimates only. They are not promises, and actual sales can be higher or lower.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import business_now
from app.models import Business, Invoice
from app.money import fmt

MIN_MONTHS = 3
MAX_TREND = Decimal("0.05")
MIN_SPREAD, MAX_SPREAD, ILLUSTRATIVE_SPREAD = Decimal("0.10"), Decimal("0.50"), Decimal("0.25")
DEFINITION = "Turnover means sales revenue: the value of sales you invoiced. It is not the cash in your bank and not profit."
METHOD = (
    "Base level = average monthly sales of the last up to 6 complete months. Trend = least-squares slope over the last up to "
    "12 complete months, as a share of the base, limited to -5% to +5% a month. Low and high = base minus and plus the usual "
    "month-to-month variation (10% to 50%)."
)


def _sum(xs: Any) -> Decimal:
    total = Decimal(0)
    for x in xs:
        total += x
    return total


def _m(v: Decimal) -> int:
    return int(v.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def sales_history(db: Session, biz: Business) -> list[dict[str, Any]]:
    today = business_now(biz).astimezone(ZoneInfo(biz.tz_name)).date()
    this_month = today.replace(day=1)
    rows = db.execute(select(Invoice.issued_on, Invoice.amount_minor).where(Invoice.business_id == biz.id)).all()
    by: dict[date, int] = {}
    for issued, amount in rows:
        m = issued.replace(day=1)
        if m < this_month:  # complete months only
            by[m] = by.get(m, 0) + int(amount)
    if not by:
        return []
    first = max(min(by), _add_months(this_month, -24))
    out: OrderedDict[date, int] = OrderedDict()
    m = first
    while m < this_month:
        out[m] = by.get(m, 0)
        m = _add_months(m, 1)
    return [{"month": k.strftime("%Y-%m"), "sales_minor": v} for k, v in out.items()]


def _slope(ys: list[Decimal]) -> Decimal:
    n = len(ys)
    if n < 2:
        return Decimal(0)
    xs = [Decimal(i) for i in range(n)]
    mx = _sum(xs) / n
    my = _sum(ys) / n
    den = _sum((x - mx) ** 2 for x in xs)
    return _sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / den if den else Decimal(0)



def _pct(v: Decimal | None) -> str:
    """12 -> "12", 12.50 -> "12.5", -50 -> "-50" (no exponent notation)."""
    t = f"{(v or Decimal(0)):f}"
    return t.rstrip("0").rstrip(".") if "." in t else t


def _month12(v: Decimal | None) -> str:
    g = v or Decimal(0)
    if g > 0:
        return f"month 12 is {_pct(g)}% above the starting month"
    if g < 0:
        return f"month 12 is {_pct(-g)}% below the starting month"
    return "every month stays at the starting level"

def forecast(db: Session, biz: Business, *, expected_monthly_minor: int | None = None, yearly_growth_pct: Decimal | None = None) -> dict[str, Any]:
    ccy = biz.ccy
    hist = sales_history(db, biz)
    today = business_now(biz).astimezone(ZoneInfo(biz.tz_name)).date()
    start = _add_months(today.replace(day=1), 1)
    assumptions: list[str] = []
    if len(hist) >= MIN_MONTHS and any(h["sales_minor"] for h in hist):
        ys = [Decimal(h["sales_minor"]) for h in hist]
        recent = ys[-6:]
        base = _sum(recent) / len(recent)
        trend_window = ys[-12:]
        trend = (_slope(trend_window) / base) if base else Decimal(0)
        trend = max(-MAX_TREND, min(MAX_TREND, trend))
        mean = _sum(ys) / len(ys)
        var = _sum((y - mean) ** 2 for y in ys) / len(ys)
        spread = (var.sqrt() / mean) if mean else MAX_SPREAD
        spread = max(MIN_SPREAD, min(MAX_SPREAD, spread))
        status = "estimated"
        assumptions = [
            f"Based on {len(hist)} complete month{'s' if len(hist) != 1 else ''} of invoiced sales ({hist[0]['month']} to {hist[-1]['month']}).",
            f"Base level {fmt(_m(base), ccy)} a month; trend {trend * 100:+.1f}% a month; usual variation ±{spread * 100:.0f}%.",
            "Seasonal patterns are not modelled." if len(hist) < 24 else "Two years of history exist, but seasonal patterns are still not modelled.",
            "Invoices you haven't recorded aren't included.",
        ]
    else:
        if expected_monthly_minor is None:
            return {
                "status": "needs_assumptions",
                "currency": ccy,
                "definition": DEFINITION,
                "history": hist,
                "message": f"There isn't enough sales history yet (at least {MIN_MONTHS} complete months of invoices are needed). "
                "Tell FlowSense your expected monthly sales and growth, and it will draw an illustrative forecast.",
                "needs": ["expected monthly sales", "expected growth per year (optional, default 0%)"],
            }
        base = Decimal(expected_monthly_minor)
        growth = (yearly_growth_pct or Decimal(0)) / Decimal(100)
        # Effective annual growth: compounding the monthly rate for 12 months gives exactly the yearly rate
        # (12% a year -> about 0.949% a month, not 1%, which would compound to 12.68%).
        with localcontext() as c:
            c.prec = 34
            trend = (Decimal(1) + growth) ** (Decimal(1) / Decimal(12)) - Decimal(1)
        spread = ILLUSTRATIVE_SPREAD
        status = "illustrative"
        assumptions = [
            f"Your assumption: about {fmt(expected_monthly_minor, ccy)} of sales a month.",
            f"Your assumption: {_pct(yearly_growth_pct)}% growth a year, compounded monthly (about {trend * 100:+.3f}% a month), so {_month12(yearly_growth_pct)}.",
            "Illustrative only: not based on your sales history. The range ±25% is an assumption, not a statistical confidence interval.",
        ]
    months = []
    totals = {"low": 0, "base": 0, "high": 0}
    for k in range(1, 13):
        b = base * (1 + trend) ** k
        lo, mid, hi = max(0, _m(b * (1 - spread))), max(0, _m(b)), max(0, _m(b * (1 + spread)))
        totals["low"] += lo
        totals["base"] += mid
        totals["high"] += hi
        months.append({"month": _add_months(start, k - 1).strftime("%Y-%m"), "low_minor": lo, "base_minor": mid, "high_minor": hi})
    return {
        "status": status,
        "currency": ccy,
        "definition": DEFINITION,
        "method": METHOD if status == "estimated" else "Your expected monthly sales, grown by your yearly growth rate (an effective annual rate, compounded monthly).",
        "assumptions": assumptions,
        "history": hist,
        "months": months,
        "annual": {"low_minor": totals["low"], "base_minor": totals["base"], "high_minor": totals["high"],
                   "low": fmt(totals["low"], ccy), "base": fmt(totals["base"], ccy), "high": fmt(totals["high"], ccy)},
        "disclaimer": "An estimate, not a promise. Actual sales depend on your customers and market and can be higher or lower.",
    }

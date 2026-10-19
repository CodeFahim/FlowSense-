"""Deterministic financial intent compiler (offline demo mode).

Turns a sentence such as
  "Pay employees this Friday, retain CNY 10,000, use only approved recipients,
   and never borrow above 8% annualized all-in cost."
into a typed, *proposed* policy plus a list of interpreted fields and
ambiguities. Nothing becomes active until the owner confirms it.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from app.money import SUPPORTED_CURRENCIES, MoneyError, fmt, parse_major

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DEFAULT_PER_TXN_LIMIT_MAJOR = 100_000  # 100,000 in the business currency


def default_limit_minor(currency: str) -> int:
    return DEFAULT_PER_TXN_LIMIT_MAJOR * 10 ** SUPPORTED_CURRENCIES[currency]
DEFAULT_HORIZON_DAYS = 7
DEFAULT_PAYROLL_TIME = time(18, 0)
COST_CONVENTION = (
    "Annualized all-in cost = (fees + interest) / net proceeds x 365 / actual days. A simple sandbox convention, not a legal APR."
)

_RESERVE = re.compile(
    r"\b(?:retain|keep|reserve|maintain|hold)\s+(?:at\s+least\s+|a\s+minimum\s+of\s+)?"
    r"(?P<cur>[A-Z]{3}|RMB|¥)\s?(?P<amt>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)",
    re.IGNORECASE,
)
_RATE = re.compile(
    r"\b(?:never|don'?t|do\s+not)\s+borrow\s+(?:at\s+)?(?:above|over|more\s+than)\s+(?P<pct>\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
_DAY = re.compile(r"\b(?P<rel>this|next|on)\s+(?P<day>" + "|".join(WEEKDAYS) + r")\b", re.IGNORECASE)
_APPROVED = re.compile(r"\bonly\s+(?:approved|verified)\s+(?:recipients|payees|accounts)\b", re.IGNORECASE)
_PAYROLL = re.compile(r"\b(?:pay\s+(?:employees|staff|wages|salaries)|payroll)\b", re.IGNORECASE)
_ALL_IN = re.compile(r"annuali[sz]ed\s+all[-\s]in", re.IGNORECASE)


def _resolve_day(rel: str, day: str, now_local: datetime) -> datetime:
    target = WEEKDAYS.index(day.lower())
    delta = (target - now_local.weekday()) % 7
    if rel.lower() == "next":
        delta += 7
    return (now_local + timedelta(days=delta)).replace(hour=0, minute=0, second=0, microsecond=0)


def compile_policy(
    text: str,
    *,
    business_now: datetime,
    timezone: str,
    currency: str,
    scheduled_payroll_due: datetime | None,
) -> dict[str, Any]:
    tzinfo = ZoneInfo(timezone)
    now_local = business_now.astimezone(tzinfo)
    fields: list[dict[str, str]] = []
    ambiguities: list[str] = []
    questions: list[str] = []
    spec: dict[str, Any] = {
        "currency": currency,
        "timezone": timezone,
        "protection_horizon_days": DEFAULT_HORIZON_DAYS,
        "per_transaction_limit_minor": default_limit_minor(currency),
        "financing_cost_convention": COST_CONVENTION,
        "essential_obligation_kinds": ["payroll"],
    }

    if _PAYROLL.search(text):
        spec["purpose"] = "payroll"
        fields.append({"field": "Purpose", "value": "Pay employees (payroll)", "source": "stated"})

    m = _DAY.search(text)
    if m:
        day = _resolve_day(m.group("rel"), m.group("day"), now_local)
        if scheduled_payroll_due and scheduled_payroll_due.astimezone(tzinfo).date() == day.date():
            deadline = scheduled_payroll_due.astimezone(tzinfo)
            note = "Time not stated; using the scheduled payroll time on that date."
        else:
            deadline = datetime.combine(day.date(), DEFAULT_PAYROLL_TIME, tzinfo)
            note = "Time not stated; assumed 18:00 local."
        ambiguities.append(note)
        spec["payroll_deadline"] = deadline.isoformat()
        fields.append(
            {
                "field": "Payroll deadline",
                "value": deadline.strftime("%A %d %b %Y, %H:%M") + f" ({timezone})",
                "source": "resolved",
                "note": f'"{m.group(0)}" resolved against business date {now_local:%a %d %b %Y}. {note}',
            }
        )
    else:
        questions.append("Which date must payroll be paid by?")

    m = _RESERVE.search(text)
    if m:
        cur = m.group("cur").upper().replace("RMB", "CNY").replace("¥", "CNY")
        if cur != currency:
            questions.append(f"The reserve is stated in {cur}, but this business uses {currency}. Which amount in {currency}?")
        else:
            try:
                spec["reserve_minor"] = parse_major(m.group("amt"), currency)
                fields.append({"field": "Protected cash reserve", "value": fmt(spec["reserve_minor"], currency), "source": "stated"})
            except MoneyError as exc:
                questions.append(f"The reserve amount could not be read ({exc}). What reserve should be kept?")
    else:
        questions.append("How much cash must always remain after payments (the reserve)?")

    approved_only = bool(_APPROVED.search(text))
    spec["approved_recipients_only"] = True
    fields.append(
        {
            "field": "Recipients",
            "value": "Only verified sandbox recipients",
            "source": "stated" if approved_only else "default",
            "note": "" if approved_only else "Not stated; FlowSense always requires verified recipients.",
        }
    )

    m = _RATE.search(text)
    if m:
        pct = Decimal(m.group("pct"))
        spec["max_financing_annualized_bp"] = int(pct * 100)
        stated_basis = bool(_ALL_IN.search(text))
        if not stated_basis:
            ambiguities.append("Rate basis not stated; interpreted as annualized all-in cost.")
        fields.append(
            {
                "field": "Financing cost cap",
                "value": f"{pct}% annualized all-in",
                "source": "stated" if stated_basis else "resolved",
                "note": COST_CONVENTION,
            }
        )
    else:
        spec["max_financing_annualized_bp"] = 0
        fields.append(
            {"field": "Financing cost cap", "value": "No borrowing allowed", "source": "default", "note": "No borrowing limit stated."}
        )

    fields.append({"field": "Per-transaction limit", "value": fmt(default_limit_minor(currency), currency), "source": "default"})
    fields.append({"field": "Protection horizon", "value": f"{DEFAULT_HORIZON_DAYS} days of essential obligations", "source": "default"})
    fields.append({"field": "Timezone / currency", "value": f"{timezone} / {currency}", "source": "default"})

    return {
        "spec": spec,
        "interpretation": {"fields": fields, "ambiguities": ambiguities, "questions": questions},
        "activatable": not questions,
    }

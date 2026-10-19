"""Personal preferences, reference data, currency conversion and small UI helpers.

Nothing in this module moves money or changes balances, approvals, reserves,
the ledger or receipts. Preferences only change how values are displayed.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import fx
from app.agents import assistant
from app.api.deps import Ctx, get_ctx, mutating_ctx, owner_ctx
from app.audit import chain as audit
from app.clock import real_now
from app.db import get_db
from app.domain.actions import lock_business
from app.errors import ApiError
from app.models import Action, Business, FinancingQuote, Invoice, JournalEntry, Obligation, User, UserPreference
from app.money import SUPPORTED_CURRENCIES
from app.reference import currencies, metadata, regions

router = APIRouter(prefix="/api")
DB = Annotated[Session, Depends(get_db)]

LOCALES = {
    "en-US": "English (United States): 1,234.56 · 10/16/2026",
    "en-GB": "English (United Kingdom): 1,234.56 · 16/10/2026",
    "en-IN": "English (India): 1,23,456.78",
    "de-DE": "German: 1.234,56 · 16.10.2026",
    "fr-FR": "French: 1 234,56 · 16/10/2026",
    "es-ES": "Spanish: 1234,56 · 16/10/2026",
    "pt-BR": "Portuguese (Brazil): 1.234,56",
    "zh-CN": "Chinese (China): 1,234.56 · 2026/10/16",
    "ja-JP": "Japanese: 1,234.56 · 2026/10/16",
    "bn-BD": "Bengali (Bangladesh)",
    "ar-AE": "Arabic (UAE)",
}


def get_prefs(db: Session, user: User) -> UserPreference | None:
    return db.get(UserPreference, user.id)


def prefs_view(p: UserPreference | None) -> dict[str, Any]:
    return {
        "country": p.country if p else None,
        "display_currency": (p.display_currency if p and p.display_currency else None),
        # No preset: when the user has not chosen one, amounts are shown in the accounting currency.
        "effective_display_currency": (p.display_currency if p and p.display_currency else None),
        "convert_from": p.convert_from if p else None,
        "convert_to": p.convert_to if p else None,
        "number_locale": p.number_locale if p else None,
        "time_display": p.time_display if p else "business",
        "theme": p.theme if p else "system",
        "accent": p.accent if p else "teal",
        "onboarded": bool(p and p.onboarded_at),
    }


def business_has_history(db: Session, biz_id: Any) -> bool:
    for model in (JournalEntry, Action, Invoice, Obligation, FinancingQuote):
        if db.execute(select(model.id).where(model.business_id == biz_id).limit(1)).first():
            return True
    return False


def business_currency_view(db: Session, biz: Business) -> dict[str, Any]:
    code = biz.currency or ""
    c = currencies().get(code)
    return {
        "currency": biz.currency,
        "currency_name": c.name if c else (biz.currency or None),
        "minor_units": SUPPORTED_CURRENCIES.get(code, 2),
        "confirmed": biz.currency_confirmed_at is not None,
        "locked": business_has_history(db, biz.id),
    }


# ------------------------------------------------------------------ reference


@router.get("/reference/currencies")
def list_currencies(ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    # Automatic-rate coverage is reported separately by /fx/rates (what was actually fetched).
    return {
        "meta": metadata(),
        "currencies": [
            {"code": c.code, "name": c.name, "iso_name": c.iso_name, "minor_units": c.minor_units, "used_in": list(c.used_in)}
            for c in currencies().values()
        ],
    }


@router.get("/reference/regions")
def list_regions(ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    return {"meta": metadata(), "regions": list(regions().values())}


@router.get("/reference/locales")
def list_locales(ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    return {"locales": [{"code": k, "label": v} for k, v in LOCALES.items()]}


# ---------------------------------------------------------------- preferences


class PrefsIn(BaseModel):
    country: str | None = Field(default=None, max_length=2)
    display_currency: str | None = Field(default=None, max_length=3)
    convert_from: str | None = Field(default=None, max_length=3)
    convert_to: str | None = Field(default=None, max_length=3)
    number_locale: str | None = Field(default=None, max_length=16)
    time_display: Literal["business", "device"] | None = None
    theme: Literal["system", "light", "dark"] | None = None
    accent: Literal["teal", "blue", "violet", "emerald", "amber"] | None = None
    complete_onboarding: bool = False
    restart_onboarding: bool = False  # show the welcome steps again (own preferences only)
    # Fields listed here are explicitly cleared (set to null), e.g. "country".
    clear: list[Literal["country", "display_currency", "convert_from", "convert_to", "number_locale"]] = []


@router.get("/me/preferences")
def read_prefs(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    return prefs_view(get_prefs(db, ctx.user))


@router.put("/me/preferences")
def write_prefs(body: PrefsIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    if body.country is not None and body.country.upper() not in regions():
        raise ApiError(422, "COUNTRY_UNKNOWN", "Unknown country or region code.")
    for field in ("display_currency", "convert_from", "convert_to"):
        v = getattr(body, field)
        if v is not None and v.upper() not in SUPPORTED_CURRENCIES:
            raise ApiError(422, "CURRENCY_UNKNOWN", f"{v} is not an active ISO 4217 currency code.")
    if body.number_locale is not None and body.number_locale not in LOCALES:
        raise ApiError(422, "LOCALE_UNKNOWN", "Unsupported number and date format.")
    p = get_prefs(db, ctx.user)
    if p is None:
        p = UserPreference(user_id=ctx.user.id, time_display="business", theme="system", accent="teal")
        db.add(p)
    changed: dict[str, Any] = {}
    for field in ("country", "display_currency", "convert_from", "convert_to", "number_locale", "time_display", "theme", "accent"):
        v = getattr(body, field)
        if v is not None:
            v = v.upper() if field in ("country", "display_currency", "convert_from", "convert_to") else v
            if getattr(p, field) != v:
                setattr(p, field, v)
                changed[field] = v
    for field in body.clear:
        if getattr(p, field) is not None:
            setattr(p, field, None)
            changed[field] = None
    if body.restart_onboarding and p.onboarded_at is not None:
        p.onboarded_at = None
        changed["onboarded"] = False
    elif body.complete_onboarding and p.onboarded_at is None:
        p.onboarded_at = real_now()
        changed["onboarded"] = True
    if changed:
        audit.append(
            db,
            ctx.business.id,
            event_type="preferences.updated",
            actor_type="user",
            actor_id=ctx.user.id,
            payload={"changed": changed, "note": "Display preferences only; no amounts or records changed."},
        )
    db.commit()
    return prefs_view(p)


# ------------------------------------------------------ business accounting currency


class AccountingCurrencyIn(BaseModel):
    currency: str = Field(min_length=3, max_length=3)


@router.get("/business/accounting-currency")
def accounting_currency(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    return business_currency_view(db, ctx.business)


@router.post("/business/accounting-currency")
def confirm_accounting_currency(body: AccountingCurrencyIn, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    """Owner confirms (or, before any financial history exists, changes) the books' currency."""
    code = body.currency.upper()
    if code not in SUPPORTED_CURRENCIES:
        raise ApiError(422, "CURRENCY_UNKNOWN", f"{code} is not an active ISO 4217 currency code.")
    biz = lock_business(db, ctx.business.id)
    if code != biz.currency:
        if business_has_history(db, biz.id):
            raise ApiError(
                409,
                "ACCOUNTING_CURRENCY_LOCKED",
                f"This business already has records in {biz.currency}. Its accounting currency cannot change, "
                "because existing amounts, approvals and the ledger would no longer mean the same thing.",
            )
        old = biz.currency
        biz.currency = code
        audit.append(db, biz.id, event_type="business.currency_changed", actor_type="user", actor_id=ctx.user.id, payload={"from": old, "to": code})
    if biz.currency_confirmed_at is None:
        biz.currency_confirmed_at = real_now()
        audit.append(db, biz.id, event_type="business.currency_confirmed", actor_type="user", actor_id=ctx.user.id, payload={"currency": code})
    db.commit()
    return business_currency_view(db, biz)


# ---------------------------------------------------------------------- FX


class ConvertIn(BaseModel):
    amount: str = Field(min_length=1, max_length=32)
    from_currency: str = Field(min_length=3, max_length=3)
    to_currency: str = Field(min_length=3, max_length=3)
    mode: Literal["latest", "demo", "manual"] = "latest"
    manual_rate: str | None = Field(default=None, max_length=32)


@router.get("/fx/rates")
def fx_rates(db: DB, mode: Literal["latest", "demo"] = "latest", ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    rs = fx.latest_rates(db) if mode == "latest" else fx.demo_rates()
    return rs.summary()


@router.get("/fx/coverage")
def fx_coverage(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    """Which selectable currencies the active provider actually quotes, how often, and on what plan."""
    return fx.coverage(db)


@router.post("/fx/convert")
def fx_convert(body: ConvertIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    """Conversion is a calculation for display. It never moves money or changes any record."""
    from datetime import timedelta

    from app.auth import ratelimit

    ratelimit.check(db, ratelimit.bucket("fx-convert", str(ctx.user.id)), 300, timedelta(hours=1), "Too many conversions in a short time. Try again later.")
    db.commit()
    return fx.convert(db, body.amount, body.from_currency, body.to_currency, body.mode, body.manual_rate)


# ------------------------------------------------------------------ UI helpers


@router.get("/nav/counts")
def nav_counts(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, int]:
    """Review queue by state: 'needs approval' and 'ready to send' are different tasks.
    rescue_open = 1 while a cash shortfall case is open (the menu then shows Rescue plan up front)."""
    from app.planning import rescue

    rows = dict(
        db.execute(
            select(Action.status, func.count(Action.id))
            .where(Action.business_id == ctx.business.id, Action.status.in_(("proposed", "approved", "submitted", "sent", "accepted")))
            .group_by(Action.status)
        ).all()
    )
    needs, ready = int(rows.get("proposed", 0)), int(rows.get("approved", 0))
    return {
        "approvals": needs + ready,  # items that need the owner's action
        "needs_approval": needs,
        "ready_to_send": ready,
        "in_progress": int(rows.get("submitted", 0)) + int(rows.get("sent", 0)) + int(rows.get("accepted", 0)),
        "rescue_open": int(bool((case := rescue.current_case(db, ctx.business.id)) and case.status != "RESOLVED")),
    }


@router.get("/assistant/suggestions")
def assistant_suggestions(ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    from app.domain.workspace import is_configured

    fresh = not is_configured(ctx.business) or ctx.business.cash_declared_at is None
    return {"label": assistant.mode_label(), **assistant.suggestions(ctx.business.currency or "", fresh=fresh)}

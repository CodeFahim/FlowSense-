"""Exchange rates for *display and conversion only*. Nothing here moves money,
changes balances, approvals, reserves, the ledger or receipts. Rates are never
applied to stored financial records.

Providers (server-side; selected by LIFELINE_FX_PROVIDER):
  erapi            ExchangeRate-API open access, https://open.er-api.com/v6/latest/USD
                   Free, no key, ~160 currencies incl. BDT, updated once a day.
                   Terms: attribution link "Rates By Exchange Rate API" required, caching allowed,
                   redistribution of the data prohibited. Rate-limited per IP (HTTP 429).
  exchangerate_api ExchangeRate-API v6 with LIFELINE_FX_API_KEY (paid/free keyed plans; the plan
                   decides the update frequency). Same data family and attribution.
  ecb              European Central Bank euro reference rates (~30 currencies, working days).
                   Used as the fallback provider only when the primary has never produced a snapshot.
  off              No automatic rates.

Statuses shown with every result:
  current      fetched for this request from the provider's latest publication
  cached       from an earlier fetch that is still within the provider's update cycle
  stale        the provider's own update time is too old; shown only with its age
  unavailable  nothing usable (provider off, outage with no snapshot, or currency not covered)
  demo         fixed illustrative values (offline demos); never used automatically
  manual       a rate the user typed; indicative, never applied to records

"Current" means the provider's latest daily (or plan) publication, not a real-time or executable
bank quote. A failed fetch never produces made-up numbers and never silently switches to demo or
manual rates. Cross rates use one snapshot only (one source, one timestamp) and are disclosed.

Maths uses Decimal end to end; results are rounded half-even to the target currency's ISO 4217
minor units.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import ApiError
from app.models import FxRateSnapshot
from app.money import MAX_MINOR, SUPPORTED_CURRENCIES, MoneyError, fmt, parse_major

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ERAPI_URL = "https://open.er-api.com/v6/latest/USD"
KEYED_URL = "https://v6.exchangerate-api.com/v6/{key}/latest/USD"
DEMO_LABEL = "Demo rates: fixed illustrative values, not market data"
MANUAL_LABEL = "Manual indicative rate you entered"
FEES_NOTE = "Banks and money transfer services add fees and spreads, so the amount you actually receive can differ."
ATTRIBUTION = {"text": "Rates By Exchange Rate API", "url": "https://www.exchangerate-api.com"}

PROVIDERS: dict[str, dict[str, Any]] = {
    "erapi": {
        "label": "ExchangeRate-API (open access)",
        "frequency": "Once a day",
        "key_required": False,
        "plan": "Free open-access endpoint, no key. Attribution required; redistribution of the data is not allowed.",
        "stale_after_hours": 48,
        "attribution": ATTRIBUTION,
        "docs": "https://www.exchangerate-api.com/docs/free",
    },
    "exchangerate_api": {
        "label": "ExchangeRate-API (keyed plan)",
        "frequency": "Depends on the plan (daily on the free key; hourly or faster on paid plans)",
        "key_required": True,
        "plan": "Needs LIFELINE_FX_API_KEY from exchangerate-api.com. Paid plans are optional and not purchased.",
        "stale_after_hours": 48,
        "attribution": ATTRIBUTION,
        "docs": "https://www.exchangerate-api.com/docs/standard-requests",
    },
    "ecb": {
        "label": "European Central Bank euro reference rates",
        "frequency": "Once per working day (around 16:00 CET)",
        "key_required": False,
        "plan": "Free, no key. About 30 currencies; BDT and many others are not covered.",
        "stale_after_hours": 24 * 4,
        "attribution": None,
        "docs": "https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html",
    },
}
# Back-compat name used by earlier code/tests.
ECB_LABEL = PROVIDERS["ecb"]["label"]

# Units of each currency per 1 EUR. Fixed so demos and tests are deterministic.
DEMO_RATES: dict[str, str] = {
    "EUR": "1",
    "USD": "1.10",
    "CNY": "7.80",
    "GBP": "0.85",
    "JPY": "160",
    "BDT": "130",
    "INR": "92",
    "SGD": "1.45",
    "HKD": "8.60",
    "AUD": "1.65",
    "CAD": "1.50",
    "CHF": "0.95",
    "AED": "4.04",
    "KWD": "0.34",
    "BHD": "0.41",
}
DEMO_AS_OF = date(2026, 10, 12)
_MAX_RATE = Decimal("1000000000")
_MAX_BYTES = 300_000


@dataclass
class Fetched:
    base: str
    rate_date: date
    rates: dict[str, str]
    provider_as_of: datetime | None = None
    next_update_at: datetime | None = None


@dataclass
class RateSet:
    mode: str  # latest | demo
    status: str  # current | cached | stale | demo | unavailable
    label: str
    base: str
    rates: dict[str, Decimal]
    rate_date: date | None
    fetched_at: datetime | None
    detail: str
    provider: str = ""
    provider_as_of: datetime | None = None
    next_update_at: datetime | None = None

    def summary(self) -> dict[str, Any]:
        meta = PROVIDERS.get(self.provider, {})
        return {
            "mode": self.mode,
            "status": self.status,
            "label": self.label,
            "provider": self.provider or self.mode,
            "base": self.base,
            "rate_date": self.rate_date.isoformat() if self.rate_date else None,
            "provider_as_of": self.provider_as_of.isoformat() if self.provider_as_of else None,
            "next_update_at": self.next_update_at.isoformat() if self.next_update_at else None,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "frequency": meta.get("frequency"),
            "attribution": meta.get("attribution"),
            "detail": self.detail,
            "fees_note": FEES_NOTE,
            "currencies": sorted(self.rates),
        }


# ---------------------------------------------------------------- fetchers


def _get(url: str) -> bytes:
    s = get_settings()
    # Fixed provider URLs only; no redirects are followed (SSRF and tampering guard); bounded size and time.
    with httpx.Client(timeout=s.fx_timeout_seconds, follow_redirects=False) as client:
        last: Exception | None = None
        for _ in range(2):  # one retry for transient network errors
            try:
                resp = client.get(url, headers={"User-Agent": "FlowSense-sandbox/1.0", "Accept": "application/json, text/xml"})
                if resp.status_code == 429:
                    raise RuntimeError("Provider rate limit reached (HTTP 429)")
                if resp.status_code != 200:
                    raise RuntimeError(f"Provider returned HTTP {resp.status_code}")
                if len(resp.content) > _MAX_BYTES:
                    raise RuntimeError("Provider response too large")
                return resp.content
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
        raise RuntimeError(f"Provider unreachable ({type(last).__name__})")


_ECB_DATE = re.compile(r"<Cube\s+time=['\"](\d{4}-\d{2}-\d{2})['\"]")
_ECB_RATE = re.compile(r"<Cube\s+currency=['\"]([A-Z]{3})['\"]\s+rate=['\"]([0-9]+(?:\.[0-9]+)?)['\"]")


def parse_ecb(xml: str) -> tuple[date, dict[str, str]]:
    """Parse the ECB daily file. Regex on a fixed, simple format (no XML entity expansion)."""
    m = _ECB_DATE.search(xml)
    rates = {c: r for c, r in _ECB_RATE.findall(xml) if c in SUPPORTED_CURRENCIES}
    if m is None or len(rates) < 5:
        raise ValueError("Unexpected ECB response format")
    return date.fromisoformat(m.group(1)), rates


def _fetch_ecb() -> Fetched:
    d, rates = parse_ecb(_get(ECB_URL).decode("utf-8", "replace"))
    return Fetched("EUR", d, rates | {"EUR": "1"})


def parse_erapi(raw: bytes, rates_key: str = "rates") -> Fetched:
    """Parse an ExchangeRate-API v6 response. Numbers are read as Decimal (never float)."""
    data = json.loads(raw, parse_float=Decimal, parse_int=Decimal)
    if data.get("result") != "success":
        raise ValueError(f"Provider error: {str(data.get('error-type', 'unknown'))[:40]}")
    base = str(data.get("base_code", ""))
    rates_in = data.get(rates_key) or {}
    rates: dict[str, str] = {}
    for code, val in rates_in.items():
        if code in SUPPORTED_CURRENCIES and isinstance(val, Decimal) and val.is_finite() and Decimal(0) < val <= _MAX_RATE:
            rates[str(code)] = format(val, "f")
    if base not in SUPPORTED_CURRENCIES or len(rates) < 20:
        raise ValueError("Unexpected provider response format")
    as_of = datetime.fromtimestamp(int(data["time_last_update_unix"]), UTC)
    nxt = data.get("time_next_update_unix")
    return Fetched(base, as_of.date(), rates | {base: "1"}, as_of, datetime.fromtimestamp(int(nxt), UTC) if nxt else None)


def _fetch_erapi() -> Fetched:
    return parse_erapi(_get(ERAPI_URL))


def _fetch_keyed() -> Fetched:
    key = get_settings().fx_api_key
    assert key is not None
    return parse_erapi(_get(KEYED_URL.format(key=key.get_secret_value().strip())), "conversion_rates")


# Replaced in tests; tests never call the network.
fetchers: dict[str, Callable[[], Fetched]] = {"erapi": _fetch_erapi, "exchangerate_api": _fetch_keyed, "ecb": _fetch_ecb}


# ------------------------------------------------------------------ rate sets


def _decimal_rates(raw: dict[str, Any], base: str) -> dict[str, Decimal]:
    return {k: Decimal(str(v)) for k, v in raw.items()} | {base: Decimal(1)}


def demo_rates() -> RateSet:
    return RateSet(
        "demo", "demo", DEMO_LABEL, "EUR", _decimal_rates(DEMO_RATES, "EUR"), DEMO_AS_OF, None,
        "Fixed values for demonstrations. Do not use for real decisions.", provider="demo",
    )


def _latest_snapshot(db: Session, provider: str) -> FxRateSnapshot | None:
    return db.execute(
        select(FxRateSnapshot).where(FxRateSnapshot.provider == provider).order_by(FxRateSnapshot.fetched_at.desc()).limit(1)
    ).scalar_one_or_none()


def _refresh(db: Session, provider: str) -> tuple[FxRateSnapshot | None, bool, str]:
    s = get_settings()
    snap = _latest_snapshot(db, provider)
    now = datetime.now(UTC)
    due = snap is None or snap.fetched_at < now - timedelta(hours=s.fx_refresh_hours)
    if snap is not None and snap.next_update_at is not None and now < snap.next_update_at and snap.fetched_at >= now - timedelta(hours=24):
        due = False  # the provider has not published anything newer yet: respect its quota
    if not due:
        return snap, False, ""
    try:
        f = fetchers[provider]()
        new = FxRateSnapshot(
            provider=provider, base=f.base, rate_date=f.rate_date, rates=f.rates, fetched_at=now,
            provider_as_of=f.provider_as_of, next_update_at=f.next_update_at,
        )
        db.add(new)
        db.commit()
        return new, True, ""
    except Exception as exc:  # network, HTTP, quota or format error: report it, never substitute other data
        db.rollback()
        return _latest_snapshot(db, provider), False, f"Could not refresh rates from {PROVIDERS[provider]['label']} ({type(exc).__name__})."


def latest_rates(db: Session) -> RateSet:
    s = get_settings()
    primary = s.fx_provider
    if primary == "off":
        return RateSet("latest", "unavailable", "Automatic rates off", "", {}, None, None, "Automatic exchange rates are turned off on this server.", provider="off")
    snap, fetched_now, error = _refresh(db, primary)
    provider = primary
    if snap is None and s.fx_fallback_provider != "off" and s.fx_fallback_provider != primary:
        # Clearly identified fallback, only when the primary has never produced a snapshot.
        fb, fb_now, fb_err = _refresh(db, s.fx_fallback_provider)
        if fb is not None:
            snap, fetched_now, provider = fb, fb_now, s.fx_fallback_provider
            error = (error + f" Using the fallback provider {PROVIDERS[provider]['label']} instead.").strip()
        else:
            error = (error + " " + fb_err).strip()
    label = PROVIDERS[provider]["label"]
    if snap is None:
        return RateSet("latest", "unavailable", label, "", {}, None, None, error or "No exchange rates are available yet.", provider=provider)
    now = datetime.now(UTC)
    as_of = snap.provider_as_of or datetime.combine(snap.rate_date, datetime.min.time(), UTC)
    age_h = (now - as_of).total_seconds() / 3600
    pub = f"{as_of:%d %b %Y %H:%M} UTC" if snap.provider_as_of else f"{snap.rate_date:%d %b %Y}"
    if age_h > PROVIDERS[provider]["stale_after_hours"]:
        status, detail = "stale", f"The newest {label} rates are from {pub} ({int(age_h // 24)} days old). Treat them as out of date."
    elif fetched_now:
        status, detail = "current", f"Latest published {label} rates ({pub}). Updated {PROVIDERS[provider]['frequency'].lower()}; not a real-time bank quote."
    else:
        status, detail = "cached", f"Cached {label} rates published {pub}, fetched {snap.fetched_at:%d %b %H:%M} UTC."
    if error:
        detail = f"{error} {detail}"
    return RateSet(
        "latest", status, label, snap.base, _decimal_rates(snap.rates, snap.base), snap.rate_date, snap.fetched_at, detail,
        provider=provider, provider_as_of=snap.provider_as_of, next_update_at=snap.next_update_at,
    )


def coverage(db: Session) -> dict[str, Any]:
    """Selectable currencies vs what the active provider actually quotes. No claim beyond the snapshot."""
    s = get_settings()
    rs = latest_rates(db)
    selectable = sorted(SUPPORTED_CURRENCIES)
    quoted = sorted(c for c in rs.rates if c in SUPPORTED_CURRENCIES)
    meta = PROVIDERS.get(rs.provider, {})
    return {
        "provider": rs.provider,
        "provider_label": rs.label,
        "status": rs.status,
        "detail": rs.detail,
        "refresh_frequency": meta.get("frequency"),
        "server_refresh_hours": s.fx_refresh_hours,
        "key_required": meta.get("key_required"),
        "plan": meta.get("plan"),
        "docs": meta.get("docs"),
        "attribution": meta.get("attribution"),
        "fallback_provider": s.fx_fallback_provider,
        "selectable_count": len(selectable),
        "quoted_count": len(quoted),
        "quoted": quoted,
        "unavailable": [c for c in selectable if c not in rs.rates],
        "provider_as_of": rs.provider_as_of.isoformat() if rs.provider_as_of else None,
        "notes": [
            "Selectable currencies are the active ISO 4217 fiat currencies. Funds codes, precious metals, SDR (XDR) and "
            "testing codes (XTS, XXX) are not selectable.",
            "A currency being selectable does not mean the provider quotes it. Unquoted pairs show 'unavailable'.",
            "'Current' means the provider's latest publication, not a real-time or executable bank rate.",
            FEES_NOTE,
        ],
    }


# ------------------------------------------------------------------ conversion


def _check_code(code: str) -> str:
    code = (code or "").upper()
    if code not in SUPPORTED_CURRENCIES:
        raise ApiError(422, "CURRENCY_UNKNOWN", f"{code or 'That'} is not an active ISO 4217 currency code.")
    return code


def parse_rate(text: str) -> Decimal:
    try:
        r = Decimal(str(text).strip())
    except InvalidOperation as exc:
        raise ApiError(422, "RATE_INVALID", "Enter the rate as a plain positive number, e.g. 117.5.") from exc
    if not r.is_finite() or r <= 0 or r > _MAX_RATE or len(r.as_tuple().digits) > 15:
        raise ApiError(422, "RATE_INVALID", "The rate must be a positive number with at most 15 significant digits.")
    return r


def _sig(d: Decimal, digits: int = 6) -> str:
    """Human-readable rate with `digits` significant figures, no exponent."""
    if d == 0:
        return "0"
    q = Decimal(1).scaleb(d.adjusted() - digits + 1)
    return format(d.quantize(q, rounding=ROUND_HALF_EVEN).normalize(), "f")


def cross_rate(rs: RateSet, src: str, dst: str) -> Decimal | None:
    if src not in rs.rates or dst not in rs.rates:
        return None
    with localcontext() as ctx:
        ctx.prec = 28
        return rs.rates[dst] / rs.rates[src]


def _amount(amount: str, src: str) -> int:
    t = (amount or "").strip()
    if re.fullmatch(r"0+(\.0+)?", t):
        return 0
    if t.startswith("-"):
        raise ApiError(422, "AMOUNT_INVALID", "Enter a positive amount; negative amounts are not converted.")
    try:
        return parse_major(t, src)
    except MoneyError as exc:
        msg = str(exc)
        if "decimal places" in msg:
            raise ApiError(422, "AMOUNT_INVALID", f"{src} has {SUPPORTED_CURRENCIES[src]} decimal place(s).") from exc
        if "upper bound" in msg:
            raise ApiError(422, "AMOUNT_INVALID", "That amount is too large to convert.") from exc
        raise ApiError(422, "AMOUNT_INVALID", "Enter an amount like 1,250.50.") from exc


def convert(db: Session, amount: str, src: str, dst: str, mode: str, manual_rate: str | None = None) -> dict[str, Any]:
    src, dst = _check_code(src), _check_code(dst)
    amount_minor = _amount(amount, src)
    derived = False
    if mode == "manual":
        if manual_rate is None:
            raise ApiError(422, "RATE_REQUIRED", "Enter a manual rate.")
        rate: Decimal | None = parse_rate(manual_rate)
        known: set[str] = {src, dst}
        source: dict[str, Any] = {
            "mode": "manual", "status": "manual", "label": MANUAL_LABEL, "provider": "manual", "rate_date": None,
            "provider_as_of": None, "fetched_at": None, "frequency": None, "attribution": None, "fees_note": FEES_NOTE,
            "detail": "You entered this rate. It is indicative only, not verified, and never applied to your records.",
        }
    elif mode in ("latest", "demo"):
        rs = latest_rates(db) if mode == "latest" else demo_rates()
        source = rs.summary()
        del source["currencies"]
        rate = Decimal(1) if src == dst else cross_rate(rs, src, dst)
        known = set(rs.rates)
        derived = src != dst and rs.base not in (src, dst)
    else:
        raise ApiError(422, "MODE_INVALID", "Mode must be latest, demo or manual.")
    base = {"from": src, "to": dst, "amount_minor": amount_minor, "amount_display": fmt(amount_minor, src), "source": source}
    if rate is None:
        missing = sorted({c for c in (src, dst) if c not in known})
        return {**base, "result_minor": None, "result_display": None, "rate": None, "inverse_rate": None, "missing": missing}
    with localcontext() as ctx:
        ctx.prec = 34
        major = Decimal(amount_minor).scaleb(-SUPPORTED_CURRENCIES[src])
        result_minor = int((major * rate).scaleb(SUPPORTED_CURRENCIES[dst]).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
        inverse = Decimal(1) / rate
    if result_minor > MAX_MINOR:
        raise ApiError(422, "RESULT_TOO_LARGE", "The converted amount is outside the supported range.")
    return {
        **base,
        "result_minor": result_minor,
        "result_display": fmt(result_minor, dst),
        "rate": _sig(rate),
        "rate_exact": format(rate.normalize(), "f"),
        "inverse_rate": _sig(inverse),
        "derived": (
            f"Cross rate calculated from one {source['label']} snapshot via {source.get('base')} (both rates share the same timestamp)."
            if derived
            else None
        ),
        "rounding": f"Rounded half-to-even to {SUPPORTED_CURRENCIES[dst]} decimal place(s) (ISO 4217 minor units of {dst}).",
        "missing": [],
    }


def equivalents(db: Session, values: dict[str, int], src: str, dst: str) -> dict[str, Any]:
    """Approximate display equivalents (automatic rates only, never demo or manual). For information, not decisions."""
    if src == dst:
        return {"currency": dst, "status": "same", "values": values}
    rs = latest_rates(db)
    rate = cross_rate(rs, src, dst)
    info = {
        "currency": dst,
        "status": rs.status,
        "label": rs.label,
        "provider": rs.provider,
        "rate_date": rs.rate_date.isoformat() if rs.rate_date else None,
        "provider_as_of": rs.provider_as_of.isoformat() if rs.provider_as_of else None,
        "attribution": PROVIDERS.get(rs.provider, {}).get("attribution"),
        "detail": rs.detail,
    }
    if rate is None:
        reason = rs.detail if rs.status == "unavailable" else f"{rs.label} does not quote {src} to {dst}."
        return {**info, "status": "unavailable", "detail": reason, "values": None}
    out = {}
    with localcontext() as ctx:
        ctx.prec = 34
        for k, minor in values.items():
            major = Decimal(minor).scaleb(-SUPPORTED_CURRENCIES[src])
            out[k] = int((major * rate).scaleb(SUPPORTED_CURRENCIES[dst]).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))
    return {**info, "rate": _sig(rate), "values": out}

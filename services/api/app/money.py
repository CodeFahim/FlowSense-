"""Money helpers. Authoritative amounts are integer minor units of the
business's accounting currency (fen for CNY, cents for USD, fils for KWD...).
The number of decimal places comes from ISO 4217 (app/reference).

Decimal is used only for parsing and rate maths, with explicit rounding.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from app.reference import currencies

# Every active ISO 4217 fiat currency can be an accounting currency; value = decimal places.
SUPPORTED_CURRENCIES: dict[str, int] = {code: c.minor_units for code, c in currencies().items()}
# 1e12 minor units (10 billion in a 2-decimal currency). Far below JS
# Number.MAX_SAFE_INTEGER (~9.0e15), so integers travel as JSON numbers safely.
MAX_MINOR = 1_000_000_000_000


class MoneyError(ValueError):
    pass


def check_currency(currency: str) -> str:
    if currency not in SUPPORTED_CURRENCIES:
        raise MoneyError(f"Unsupported currency: {currency!r}")
    return currency


def check_amount(minor: int, *, allow_zero: bool = False) -> int:
    if isinstance(minor, bool) or not isinstance(minor, int):
        raise MoneyError("Amount must be an integer number of minor units")
    if minor < 0 or (minor == 0 and not allow_zero):
        raise MoneyError("Amount must be positive")
    if minor > MAX_MINOR:
        raise MoneyError("Amount exceeds the supported upper bound")
    return minor


_MAJOR_RE = re.compile(r"^\d{1,3}(,\d{3})*(\.\d+)?$|^\d+(\.\d+)?$")


def parse_major(text: str, currency: str) -> int:
    """Parse a major-unit string such as '40,000.00' into minor units.

    Rejects NaN, infinity, exponents, negative values and excess precision.
    """
    check_currency(currency)
    s = text.strip()
    if not _MAJOR_RE.match(s):
        raise MoneyError(f"Malformed amount: {text!r}")
    try:
        value = Decimal(s.replace(",", ""))
    except InvalidOperation as exc:  # pragma: no cover - regex guards this
        raise MoneyError(f"Malformed amount: {text!r}") from exc
    if not value.is_finite():
        raise MoneyError("Amount must be finite")
    places = SUPPORTED_CURRENCIES[currency]
    scaled = value * (10**places)
    if scaled != scaled.to_integral_value():
        raise MoneyError(f"Too many decimal places for {currency}")
    return check_amount(int(scaled))


def fmt(minor: int, currency: str) -> str:
    places = SUPPORTED_CURRENCIES[currency]
    sign = "-" if minor < 0 else ""
    major, frac = divmod(abs(minor), 10**places)
    return f"{currency} {sign}{major:,}" + (f".{frac:0{places}d}" if places else "")


def annualized_all_in_bp(cost_minor: int, net_proceeds_minor: int, term_days: int) -> int:
    """Simple annualized all-in cost in basis points.

    Convention (documented, not a legal APR): (fees + interest) / net proceeds
    x 365 / actual days, rounded half-even to whole basis points.
    """
    if net_proceeds_minor <= 0 or term_days <= 0:
        raise MoneyError("Net proceeds and term must be positive")
    rate = Decimal(cost_minor) / Decimal(net_proceeds_minor) * Decimal(365) / Decimal(term_days)
    return int((rate * 10000).quantize(Decimal(1), rounding=ROUND_HALF_EVEN))

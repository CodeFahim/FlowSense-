"""Independent safety kernel.

A pure function over a snapshot that the caller builds *inside the locked
transaction*. It never reads the database itself and never trusts the UI or
the assistant. Returns ALLOW, DENY or NEEDS_APPROVAL with stable reason codes.

Fails closed: an exception inside any check becomes DENY/CHECK_FAILED.

Permission tiers (see SECURITY_ASSESSMENT.md):
  T0 read-only            -> not evaluated here (route-level auth)
  T1 owner-approved       -> every action type below
  T2 auto non-monetary    -> none in the Tier 1 MVP
  T3 prohibited           -> non-sandbox accounts, unverified recipients, policy bypass
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.domain.position import Position
from app.models import Action, Approval, Business, Counterparty, Invoice, Obligation, Policy
from app.money import MAX_MINOR, SUPPORTED_CURRENCIES, fmt

PAYMENT_TYPES = {"payroll_payment": "payroll", "supplier_payment": "supplier"}
REQUEST_TYPES = {"payment_request": "customer"}
ACTION_TYPES = {**PAYMENT_TYPES, **REQUEST_TYPES}
SANDBOX_ACCOUNT_PREFIX = "SBX-"


class Outcome(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"


class Phase(StrEnum):
    PROPOSE = "propose"
    APPROVE = "approve"
    EXECUTE = "execute"


@dataclass
class Decision:
    phase: Phase
    outcome: Outcome
    reasons: list[dict[str, str]]
    warnings: list[dict[str, str]]
    checks: list[dict[str, Any]]
    evaluated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "outcome": self.outcome.value,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "checks": self.checks,
            "evaluated_at": self.evaluated_at,
        }


@dataclass
class KernelInput:
    phase: Phase
    actor_role: str | None
    business: Business
    policy: Policy | None
    action: Action
    counterparty: Counterparty | None
    obligation: Obligation | None
    obligation_reserved_minor: int
    invoice: Invoice | None
    invoice_in_flight_minor: int
    position: Position
    approval: Approval | None
    approval_owner_is_owner: bool
    current_digest: str
    now_real: datetime
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Ctx:
    inp: KernelInput
    denies: list[dict[str, str]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)

    def deny(self, code: str, message: str) -> None:
        self.denies.append({"code": code, "message": message})

    def warn(self, code: str, message: str) -> None:
        self.warnings.append({"code": code, "message": message})


def _c(minor: int, inp: KernelInput) -> str:
    return fmt(minor, inp.action.currency if inp.action.currency in SUPPORTED_CURRENCIES else inp.business.ccy)


# ------------------------------------------------------------------ checks


def _check_role(ctx: _Ctx) -> None:
    inp = ctx.inp
    if inp.actor_role not in ("owner", "staff"):
        ctx.deny("NOT_A_MEMBER", "Actor is not a member of this business.")
    elif inp.phase in (Phase.APPROVE, Phase.EXECUTE) and inp.actor_role != "owner":
        ctx.deny("ROLE_FORBIDDEN", "Only the business owner can approve or submit money actions.")


def _check_kill_switch(ctx: _Ctx) -> None:
    if ctx.inp.phase == Phase.EXECUTE and ctx.inp.business.kill_switch:
        ctx.deny("KILL_SWITCH_ACTIVE", "The business kill switch is on; no new submissions are allowed.")


def _check_policy(ctx: _Ctx) -> None:
    inp = ctx.inp
    if inp.policy is None:
        if inp.phase == Phase.PROPOSE:
            ctx.warn("POLICY_MISSING", "No active policy; this action cannot be approved until one is confirmed.")
        else:
            ctx.deny("POLICY_MISSING", "No active owner-confirmed policy; failing closed.")


def _check_type_amount_currency(ctx: _Ctx) -> None:
    a = ctx.inp.action
    if a.type not in ACTION_TYPES:
        ctx.deny("ACTION_TYPE_UNSUPPORTED", f"Action type {a.type!r} is not supported.")
    if a.currency not in SUPPORTED_CURRENCIES or a.currency != ctx.inp.business.ccy:
        ctx.deny("CURRENCY_UNSUPPORTED", f"Currency {a.currency} is not this business's currency.")
    if isinstance(a.amount_minor, bool) or not isinstance(a.amount_minor, int) or not 0 < a.amount_minor <= MAX_MINOR:
        ctx.deny("AMOUNT_INVALID", "Amount must be a positive integer of minor units within bounds.")
    policy = ctx.inp.policy
    if policy is not None:
        limit = int(policy.spec.get("per_transaction_limit_minor", 0))
        if a.amount_minor > limit:
            ctx.deny("LIMIT_EXCEEDED", f"{_c(a.amount_minor, ctx.inp)} exceeds the per-transaction limit of {_c(limit, ctx.inp)}.")


def _check_counterparty(ctx: _Ctx) -> None:
    inp = ctx.inp
    a, cp = inp.action, inp.counterparty
    if cp is None or cp.business_id != inp.business.id:
        ctx.deny("RECIPIENT_UNKNOWN", "Counterparty does not exist in this business.")
        return
    expected_kind = ACTION_TYPES.get(a.type)
    if expected_kind and cp.kind != expected_kind:
        ctx.deny("RECIPIENT_KIND_MISMATCH", f"{a.type} requires a {expected_kind} counterparty, not {cp.kind}.")
    if a.counterparty_version != cp.version:
        ctx.deny(
            "RECIPIENT_VERSION_CHANGED",
            f"Recipient details changed (bound version {a.counterparty_version}, current version {cp.version}).",
        )
    if a.type in PAYMENT_TYPES:
        if not cp.account_ref.startswith(SANDBOX_ACCOUNT_PREFIX):
            ctx.deny("REAL_TRANSFER_PROHIBITED", "Only synthetic sandbox accounts can receive payments (T3 prohibited).")
        if not cp.verified:
            ctx.deny("RECIPIENT_UNVERIFIED", f"Recipient '{cp.name}' is not verified against trusted sandbox records.")
        elif inp.policy is not None and inp.policy.spec.get("approved_recipients_only", True) is not True:
            ctx.deny("POLICY_INVALID", "Policy must require approved recipients.")


def _check_obligation(ctx: _Ctx) -> None:
    inp = ctx.inp
    a, ob = inp.action, inp.obligation
    if a.type not in PAYMENT_TYPES:
        return
    if ob is None or ob.business_id != inp.business.id:
        ctx.deny("OBLIGATION_MISSING", "Payments must settle a recorded obligation.")
        return
    if ob.counterparty_id != a.counterparty_id:
        ctx.deny("RECIPIENT_MISMATCH", "Recipient does not match the obligation's payee.")
    # For a submitted action its own reservation is already counted; exclude it.
    own = a.amount_minor if a.status == "submitted" else 0
    open_minor = ob.unpaid_minor - (inp.obligation_reserved_minor - own)
    if open_minor <= 0:
        ctx.deny("OBLIGATION_ALREADY_PAID", f"'{ob.description}' is already paid or has a payment in flight.")
    elif a.amount_minor > open_minor:
        ctx.deny(
            "AMOUNT_EXCEEDS_OUTSTANDING",
            f"{_c(a.amount_minor, inp)} exceeds the unpaid, unreserved amount {_c(open_minor, inp)}.",
        )


def _check_funds(ctx: _Ctx) -> None:
    inp = ctx.inp
    a, pos = inp.action, inp.position
    if a.type not in PAYMENT_TYPES:
        return
    after = pos.available_minor - a.amount_minor
    this_ob_protected = 0
    if inp.obligation is not None:
        for item in pos.protected:
            if item.obligation_id == inp.obligation.id:
                this_ob_protected = min(a.amount_minor, item.open_minor)
    other_protected = pos.protected_total_minor - this_ob_protected
    problems: list[tuple[str, str]] = []
    if after < 0:
        problems.append(
            (
                "INSUFFICIENT_SETTLED_FUNDS",
                f"Paying {_c(a.amount_minor, inp)} needs more than the {_c(pos.available_minor, inp)} of settled, unreserved cash.",
            )
        )
    if after < pos.reserve_minor:
        problems.append(
            (
                "RESERVE_BREACH",
                f"Paying {_c(a.amount_minor, inp)} would leave {_c(after, inp)}, below the protected reserve of "
                f"{_c(pos.reserve_minor, inp)} (short by {_c(pos.reserve_minor - after, inp)}).",
            )
        )
    elif after < pos.reserve_minor + other_protected:
        problems.append(
            (
                "PROTECTED_OBLIGATION_AT_RISK",
                f"Paying {_c(a.amount_minor, inp)} would leave {_c(after, inp)}, not enough for the reserve plus "
                f"{_c(other_protected, inp)} of other essential obligations due by {pos.horizon_end:%d %b %Y}.",
            )
        )
    for code, msg in problems:
        # At proposal time balances may still change; surface as warnings.
        (ctx.warn if inp.phase == Phase.PROPOSE else ctx.deny)(code, msg)


def _check_invoice(ctx: _Ctx) -> None:
    inp = ctx.inp
    a, inv = inp.action, inp.invoice
    if a.type not in REQUEST_TYPES:
        return
    if inv is None or inv.business_id != inp.business.id:
        ctx.deny("INVOICE_MISSING", "Payment requests must reference an invoice of this business.")
        return
    if inv.customer_id != a.counterparty_id:
        ctx.deny("RECIPIENT_MISMATCH", "Request must go to the invoice's customer.")
    if inv.disputed:
        ctx.deny("INVOICE_DISPUTED", f"Invoice {inv.number} is disputed.")
    requestable = inv.outstanding_minor - inp.invoice_in_flight_minor
    if a.amount_minor > requestable:
        ctx.deny(
            "REQUEST_EXCEEDS_OUTSTANDING",
            f"{_c(a.amount_minor, inp)} exceeds the {_c(max(requestable, 0), inp)} of invoice {inv.number} not already requested.",
        )


def _check_state(ctx: _Ctx) -> None:
    inp = ctx.inp
    allowed = {Phase.PROPOSE: {"proposed"}, Phase.APPROVE: {"proposed"}, Phase.EXECUTE: {"approved"}}[inp.phase]
    if inp.action.status not in allowed:
        ctx.deny(
            "ACTION_STATE_INVALID",
            f"Cannot {inp.phase.value} an action that is '{inp.action.status}'.",
        )


def _check_approval(ctx: _Ctx) -> None:
    inp = ctx.inp
    if inp.phase != Phase.EXECUTE:
        return
    ap, a = inp.approval, inp.action
    if ap is None:
        ctx.deny("APPROVAL_MISSING", "No owner approval exists for this exact action.")
        return
    if ap.revoked_at is not None:
        ctx.deny("APPROVAL_REVOKED", f"Approval was revoked: {ap.revoke_reason or 'no reason recorded'}.")
    if ap.consumed_at is not None:
        ctx.deny("APPROVAL_ALREADY_USED", "This approval was already used once.")
    if ap.expires_at <= inp.now_real:
        ctx.deny("APPROVAL_EXPIRED", f"Approval expired at {ap.expires_at.isoformat()}.")
    if not inp.approval_owner_is_owner:
        ctx.deny("APPROVER_NOT_OWNER", "The approving user is no longer an owner of this business.")
    if ap.action_id != a.id or ap.action_revision != a.revision:
        ctx.deny("ACTION_REVISION_CHANGED", "The action changed after it was approved.")
    if (
        ap.amount_minor != a.amount_minor
        or ap.currency != a.currency
        or ap.counterparty_id != a.counterparty_id
        or ap.action_type != a.type
    ):
        ctx.deny("APPROVAL_PAYLOAD_MISMATCH", "Amount, currency, type or recipient differ from what was approved.")
    if ap.counterparty_version != a.counterparty_version or (
        inp.counterparty is not None and ap.counterparty_version != inp.counterparty.version
    ):
        ctx.deny("RECIPIENT_VERSION_CHANGED", "Recipient details changed after approval; the old approval cannot be used.")
    if ap.payload_digest != inp.current_digest:
        ctx.deny("APPROVAL_PAYLOAD_MISMATCH", "Action payload digest differs from the approved digest.")
    if inp.policy is not None and ap.policy_version != inp.policy.version:
        ctx.deny(
            "POLICY_VERSION_CHANGED",
            f"Approved under policy v{ap.policy_version}, but v{inp.policy.version} is now active.",
        )


_CHECKS: list[tuple[str, Callable[[_Ctx], None]]] = [
    ("membership_and_role", _check_role),
    ("kill_switch", _check_kill_switch),
    ("active_policy", _check_policy),
    ("type_amount_currency_limit", _check_type_amount_currency),
    ("verified_recipient", _check_counterparty),
    ("action_state", _check_state),
    ("owner_approval_binding", _check_approval),
    ("obligation_outstanding", _check_obligation),
    ("invoice_outstanding", _check_invoice),
    ("settled_funds_reserve_essentials", _check_funds),
]


def evaluate(inp: KernelInput) -> Decision:
    ctx = _Ctx(inp)
    for name, fn in _CHECKS:
        before = len(ctx.denies)
        try:
            fn(ctx)
        except Exception as exc:  # fail closed
            ctx.deny("CHECK_FAILED", f"Safety check '{name}' could not complete ({type(exc).__name__}).")
        new = ctx.denies[before:]
        ctx.checks.append({"name": name, "passed": not new, "codes": [d["code"] for d in new]})

    # De-duplicate identical codes while preserving order.
    seen: set[str] = set()
    reasons = [d for d in ctx.denies if not (d["code"] in seen or seen.add(d["code"]))]  # type: ignore[func-returns-value]
    if reasons:
        outcome = Outcome.DENY
    elif inp.phase == Phase.PROPOSE:
        outcome = Outcome.NEEDS_APPROVAL
    else:
        outcome = Outcome.ALLOW
    return Decision(
        phase=inp.phase,
        outcome=outcome,
        reasons=reasons,
        warnings=ctx.warnings,
        checks=ctx.checks,
        evaluated_at=inp.now_real.isoformat(),
    )


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()

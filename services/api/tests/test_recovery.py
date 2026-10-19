"""Pending-transfer timeout and restart recovery: one debit, one settlement, one receipt."""

from __future__ import annotations

from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.domain.actions import Actor, execute_action
from app.ledger import service as ledger
from app.models import Action, Membership, Receipt, SimBankOp, Transfer
from app.reconciliation.recovery import reconcile_all
from app.sandbox import simulator
from tests.conftest import execute, find_action, seed

CNY = 100


def _counts(biz_id, action_id):
    with get_sessionmaker()() as s:
        transfers = s.execute(select(func.count(Transfer.id)).where(Transfer.action_id == action_id)).scalar_one()
        settled = s.execute(
            select(func.count(Transfer.id)).where(Transfer.action_id == action_id, Transfer.status == "settled")
        ).scalar_one()
        receipts = s.execute(
            select(func.count(Receipt.id)).join(Transfer, Transfer.id == Receipt.transfer_id).where(Transfer.action_id == action_id)
        ).scalar_one()
        cash = ledger.settled_cash(s, biz_id)
    return transfers, settled, receipts, cash


def test_bank_timeout_keeps_funds_reserved_then_reconciles_once(as_owner):
    biz_id = seed("pending_recovery")
    owner = as_owner()
    payroll = find_action(owner, "payroll_payment", "approved")
    r = execute(owner, payroll["id"])
    assert r.status_code == 200 and r.json()["settlement"]["outcome"] == "timeout"

    d = owner.get("/api/dashboard").json()
    assert d["position"]["settled_minor"] == 90_000 * CNY  # nothing settled yet
    assert d["position"]["reserved_minor"] == 80_000 * CNY  # but funds are held
    assert d["position"]["available_minor"] == 10_000 * CNY
    assert d["case"]["status"] == "AWAITING_SETTLEMENT"
    # A retry with a fresh key cannot pay again while the outcome is unknown.
    assert execute(owner, payroll["id"]).status_code == 409

    # Kill switch on: new submissions blocked, but reconciliation of submitted actions continues.
    owner.post("/api/kill-switch", {"enabled": True})
    out = owner.post("/api/reconcile").json()
    assert out["checked"] == 1 and out["results"][0]["step"] == "status_checked_then_settled"
    assert _counts(biz_id, payroll["id"]) == (1, 1, 1, 10_000 * CNY)
    assert owner.get("/api/dashboard").json()["case"]["status"] == "RESOLVED"

    # Repeating recovery or a re-sent bank confirmation changes nothing.
    assert owner.post("/api/reconcile").json()["checked"] == 0
    with get_sessionmaker()() as s:
        provider_ref = s.execute(select(Transfer.provider_ref).where(Transfer.action_id == payroll["id"])).scalar_one()
        assert simulator.bank_complete(s, provider_ref)["outcome"] == "duplicate"
    assert _counts(biz_id, payroll["id"]) == (1, 1, 1, 10_000 * CNY)


def test_crash_before_bank_call_recovers_on_restart():
    biz_id = seed("pending_recovery")
    with get_sessionmaker()() as s:
        owner_id = s.execute(select(Membership.user_id).where(Membership.business_id == biz_id, Membership.role == "owner")).scalar_one()
        payroll_id = s.execute(
            select(Action.id).where(Action.business_id == biz_id, Action.type == "payroll_payment", Action.status == "approved")
        ).scalar_one()
        # Submission commits (transfer pending, provider_ref persisted) and then the process "dies"
        # before the bank is ever called.
        res = execute_action(s, biz_id, Actor(owner_id, "owner"), payroll_id, "crash-key", lambda a, t, d: {"o": d.outcome.value})
        s.commit()
        ref = s.get(Transfer, res.transfer_id).provider_ref
        assert s.get(SimBankOp, ref) is None

    # "Restart": the startup hook reconciles every business with pending transfers.
    with get_sessionmaker()() as s:
        assert reconcile_all(s) >= 1
    assert _counts(biz_id, payroll_id) == (1, 1, 1, 10_000 * CNY)
    with get_sessionmaker()() as s:
        assert s.get(SimBankOp, ref).status == "settled"
        assert reconcile_all(s) == 0  # nothing pending any more
    assert _counts(biz_id, payroll_id) == (1, 1, 1, 10_000 * CNY)

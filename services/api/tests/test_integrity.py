"""Ledger, audit-chain, settlement-event and concurrency integrity."""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.audit import chain as audit
from app.db import get_sessionmaker
from app.domain.actions import Actor, execute_action
from app.domain.position import compute_position
from app.ledger import service as ledger
from app.models import Action, AuditEvent, Business, JournalLine, Transfer
from app.money import MoneyError, annualized_all_in_bp, fmt, parse_major
from app.policies.compiler import compile_policy
from app.reconciliation import ingest as recon
from app.sandbox import simulator
from app.seed.fixtures import REGIONAL_TZ, policy_text, scenario_clock
from tests.conftest import seed


def test_money_parsing_and_formatting():
    assert parse_major("40,000.00", "CNY") == 4_000_000
    assert parse_major("10000", "CNY") == 1_000_000
    for bad in ("NaN", "inf", "1e5", "-5", "1.234", "", "1,00", "12.3.4"):
        with pytest.raises(MoneyError):
            parse_major(bad, "CNY")
    assert fmt(-1_000_000, "CNY") == "CNY -10,000.00"
    # ISO 4217 minor units: zero- and three-decimal currencies.
    assert parse_major("1,500", "JPY") == 1_500 and fmt(1_500, "JPY") == "JPY 1,500"
    assert parse_major("1.234", "KWD") == 1_234 and fmt(1_234, "KWD") == "KWD 1.234"
    with pytest.raises(MoneyError):
        parse_major("1.5", "JPY")
    assert annualized_all_in_bp(61_500, 4_038_500, 14) == 3970


CLOCK, PAYROLL_DUE = scenario_clock(REGIONAL_TZ)  # the regional CNY sample ("primary")
POLICY_TEXT = policy_text("CNY")


def test_policy_compiler_resolves_friday():
    out = compile_policy(POLICY_TEXT, business_now=CLOCK, timezone="Asia/Shanghai", currency="CNY", scheduled_payroll_due=PAYROLL_DUE)
    spec = out["spec"]
    assert out["activatable"] is True
    assert spec["payroll_deadline"] == "2026-10-16T18:00:00+08:00"
    assert spec["reserve_minor"] == 1_000_000 and spec["max_financing_annualized_bp"] == 800
    assert spec["approved_recipients_only"] is True
    ambiguous = compile_policy(
        "Pay staff soon and keep some cash", business_now=CLOCK, timezone="Asia/Shanghai", currency="CNY", scheduled_payroll_due=None
    )
    assert ambiguous["activatable"] is False and len(ambiguous["interpretation"]["questions"]) == 2
    usd = compile_policy(
        "Pay employees this Friday, retain USD 1,000",
        business_now=CLOCK,
        timezone="Asia/Shanghai",
        currency="CNY",
        scheduled_payroll_due=None,
    )
    assert usd["activatable"] is False


def test_gap_formula_matches_basic_fixture(db):
    biz_id = seed("primary")
    biz = db.get(Business, biz_id)
    pos = compute_position(db, biz)
    assert pos.gap_minor == max(0, 8_000_000 + 1_000_000 - 5_000_000)
    assert pos.gap_date == PAYROLL_DUE


def test_ledger_rejects_unbalanced_and_mutation(db):
    biz_id = seed("primary")
    with pytest.raises(ledger.LedgerError):
        ledger.post_entry(
            db,
            biz_id,
            effective_at=CLOCK,
            memo="bad",
            source_type="test",
            source_id=None,
            lines=[(ledger.CASH, 100, 0), (ledger.EQUITY, 0, 99)],
        )
    db.rollback()
    line_id = db.execute(select(JournalLine.id).where(JournalLine.business_id == biz_id)).scalars().first()
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE journal_lines SET debit_minor = debit_minor + 1 WHERE id = :i"), {"i": line_id})
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("DELETE FROM audit_events WHERE business_id = :b"), {"b": biz_id})
    db.rollback()


def test_database_rejects_unbalanced_entry_even_bypassing_app(db):
    biz_id = seed("primary")
    entry = ledger.post_entry(
        db,
        biz_id,
        effective_at=CLOCK,
        memo="ok",
        source_type="test",
        source_id=None,
        lines=[(ledger.CASH, 100, 0), (ledger.EQUITY, 0, 100)],
    )
    acct = db.execute(text("SELECT id FROM ledger_accounts WHERE business_id=:b AND code='1000'"), {"b": biz_id}).scalar_one()
    db.execute(
        text("INSERT INTO journal_lines (id, entry_id, business_id, account_id, debit_minor, credit_minor) VALUES (:i, :e, :b, :a, 5, 0)"),
        {"i": uuid.uuid4(), "e": entry.id, "b": biz_id, "a": acct},
    )
    with pytest.raises(DBAPIError):
        db.commit()
    db.rollback()


def test_reversal_restores_balance(db):
    biz_id = seed("primary")
    before = ledger.settled_cash(db, biz_id)
    e = ledger.post_entry(
        db,
        biz_id,
        effective_at=CLOCK,
        memo="mistake",
        source_type="test",
        source_id=None,
        lines=[(ledger.CASH, 12_345, 0), (ledger.EQUITY, 0, 12_345)],
    )
    ledger.reverse_entry(db, e.id, effective_at=CLOCK, memo="reverse mistake")
    db.commit()
    assert ledger.settled_cash(db, biz_id) == before
    assert ledger.trial_balance(db, biz_id)["balanced"]


def test_audit_chain_detects_tampering_by_privileged_user(db):
    biz_id = seed("primary")
    assert audit.verify(db, biz_id)["valid"] is True
    # A superuser can bypass triggers; the hash chain still exposes the edit.
    db.execute(text("SET session_replication_role = replica"))
    seq = db.execute(
        select(func.min(AuditEvent.seq)).where(AuditEvent.business_id == biz_id, AuditEvent.event_type == "plan.created")
    ).scalar_one()
    db.execute(text("UPDATE audit_events SET outcome = 'tampered' WHERE business_id = :b AND seq = :s"), {"b": biz_id, "s": seq})
    db.execute(text("SET session_replication_role = origin"))
    db.commit()
    v = audit.verify(db, biz_id)
    assert v["valid"] is False and v["first_invalid_seq"] == seq


def test_event_signature_and_duplicates(db):
    biz_id = seed("partial_settlement")
    req = db.execute(select(Action).where(Action.business_id == biz_id, Action.type == "payment_request")).scalars().first()
    raw = simulator.build_event(
        biz_id,
        "payment.received",
        {"provider_ref": "SBX-IN-DUPTEST", "invoice_id": str(req.invoice_id), "amount_minor": 1_000_000, "action_id": str(req.id)},
        event_id="EVT-DUPLICATE-TEST-1",
    )
    with pytest.raises(Exception) as bad:
        recon.ingest(db, raw, "sha256=" + "0" * 64)
    assert getattr(bad.value, "code", "") == "EVENT_SIGNATURE_INVALID"
    db.rollback()
    first = recon.ingest(db, raw, recon.sign(raw))
    second = recon.ingest(db, raw, recon.sign(raw))
    assert first["outcome"] == "settled" and second["outcome"] == "duplicate"
    # Same payment under a new event id is also harmless.
    raw2 = raw.replace(b"EVT-DUPLICATE-TEST-1", b"EVT-DUPLICATE-TEST-2")
    assert recon.ingest(db, raw2, recon.sign(raw2))["outcome"] == "already_settled"
    assert ledger.settled_cash(db, biz_id) == 5_000_000 + 1_000_000


def test_overpayment_rejected(db):
    biz_id = seed("partial_settlement")
    req = db.execute(select(Action).where(Action.business_id == biz_id, Action.type == "payment_request")).scalars().first()
    out = simulator.customer_pays(db, biz_id, req.invoice_id, 7_000_000, req.id)
    assert out["outcome"] == "rejected_overpayment"
    assert ledger.settled_cash(db, biz_id) == 5_000_000


def test_settlement_event_replay_settles_once(db):
    biz_id = seed("duplicate_payroll")
    owner_actor = _owner(db, biz_id)
    payroll = db.execute(select(Action).where(Action.business_id == biz_id, Action.type == "payroll_payment")).scalars().first()
    res = execute_action(db, biz_id, owner_actor, payroll.id, "k", lambda a, t, d: {"d": d.outcome.value})
    db.commit()
    t = db.get(Transfer, res.transfer_id)
    raw = simulator.build_event(biz_id, "transfer.settled", {"provider_ref": t.provider_ref, "amount_minor": t.amount_minor})
    assert recon.ingest(db, raw, recon.sign(raw))["outcome"] == "settled"
    raw2 = simulator.build_event(biz_id, "transfer.settled", {"provider_ref": t.provider_ref, "amount_minor": t.amount_minor})
    assert recon.ingest(db, raw2, recon.sign(raw2))["outcome"] == "already_settled"
    assert ledger.settled_cash(db, biz_id) == 1_000_000


def _owner(db, biz_id) -> Actor:
    from app.models import Membership

    m = db.execute(select(Membership).where(Membership.business_id == biz_id, Membership.role == "owner")).scalar_one()
    return Actor(user_id=m.user_id, role="owner")


def test_concurrent_submissions_on_separate_connections_pay_once():
    biz_id = seed("duplicate_payroll")
    with get_sessionmaker()() as s:
        actor = _owner(s, biz_id)
        payroll_id = s.execute(select(Action.id).where(Action.business_id == biz_id, Action.type == "payroll_payment")).scalar_one()
    n = 4
    barrier = threading.Barrier(n)
    results: list[str] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        with get_sessionmaker()() as s:  # separate pooled connection per thread
            barrier.wait()
            try:
                r = execute_action(s, biz_id, actor, payroll_id, f"concurrent-{i}", lambda a, t, d: {"outcome": d.outcome.value})
                s.commit()
                out = r.body["outcome"]
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                out = f"error:{type(exc).__name__}"
            with lock:
                results.append(out)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["ALLOW"] + ["DENY"] * (n - 1), results
    with get_sessionmaker()() as s:
        transfers = s.execute(select(func.count(Transfer.id)).where(Transfer.action_id == payroll_id)).scalar_one()
        assert transfers == 1

r"""Run every Tier 1 (and implemented Tier 2) scenario against the running stack and export sandbox operation logs.

Usage (PowerShell, repo root, stack running):  .\scripts\evidence.ps1
Writes:
  evidence/operations.jsonl   one line per HTTP operation (method, path, status, key results)
  evidence/summary.md         scenario | expected | actual | pass/fail | audit head hash
  evidence/summary.json       same, machine-readable

Each scenario starts from a fresh seed via the owner-only demo reset endpoint
(the previous fixture is archived, not deleted). All data is synthetic.
"""

from __future__ import annotations

import json
import subprocess
import os
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

BASE = os.environ.get("FLOWSENSE_BASE_URL", "http://127.0.0.1:8000")
PASSWORD = os.environ["FLOWSENSE_DEMO_PASSWORD"]
OUT = Path(__file__).resolve().parent.parent / "evidence"
OWNER, STAFF, OTHER = "demo@flowsense.example", "demo-staff@flowsense.example", "owner@harbortea.example"
CNY = 100

ops: list[dict[str, Any]] = []
_ops_lock = threading.Lock()


class Session:
    def __init__(self, email: str, scenario: str) -> None:
        self.c = httpx.Client(base_url=BASE, timeout=30)
        self.scenario = scenario
        r = self.c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        r.raise_for_status()
        self.csrf = r.json()["csrf_token"]
        self.email = email
        self._log("POST", "/api/auth/login", r, {"as": email})

    def _log(self, method: str, path: str, r: httpx.Response, note: dict[str, Any] | None = None) -> None:
        try:
            body = r.json()
        except ValueError:
            body = None
        rec = {
            "ts": datetime.now(UTC).isoformat(), "scenario": self.scenario, "actor": self.email, "method": method,
            "path": path, "status": r.status_code, "request_id": r.headers.get("x-request-id"),
        }
        if isinstance(body, dict) and "error" in body:
            rec["error"] = {"code": body["error"]["code"], "reasons": [x["code"] for x in body["error"].get("reasons", [])]}
        if note:
            rec.update(note)
        with _ops_lock:
            ops.append(rec)

    def get(self, path: str) -> Any:
        r = self.c.get(path)
        self._log("GET", path, r)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: Any = None, headers: dict[str, str] | None = None, note: dict[str, Any] | None = None) -> httpx.Response:
        r = self.c.post(path, json=body or {}, headers={"X-CSRF-Token": self.csrf, **(headers or {})})
        self._log("POST", path, r, note)
        return r

    def reset(self, scenario: str) -> None:
        r = self.post("/api/demo/reset", {"scenario": scenario, "confirm": "RESET"})
        r.raise_for_status()
        self.csrf = r.json()["csrf_token"]  # session rotated to the fresh fixture

    def action(self, type_: str, status: str | None = None) -> dict[str, Any]:
        return next(a for a in self.get("/api/actions") if a["type"] == type_ and (status is None or a["status"] == status))

    def position(self) -> dict[str, Any]:
        return self.get("/api/dashboard")["position"]

    def head(self) -> str:
        v = self.get("/api/audit/verify")
        return ("valid " if v["valid"] else "INVALID ") + str(v.get("head_hash", ""))[:16]

    def execute(self, action_id: str, key: str | None = None) -> httpx.Response:
        return self.post(f"/api/actions/{action_id}/execute", headers={"Idempotency-Key": key or uuid.uuid4().hex})


def codes(r: httpx.Response) -> set[str]:
    return {x["code"] for x in r.json().get("error", {}).get("reasons", [])}


def m(minor: int) -> str:
    return f"{minor / CNY:,.2f}"


results: list[dict[str, Any]] = []


def record(scenario: str, expected: str, actual: str, ok: bool, s: Session) -> None:
    results.append({"scenario": scenario, "expected": expected, "actual": actual, "pass": ok, "audit": s.head()})
    print(("PASS " if ok else "FAIL ") + scenario + " - " + actual)


def primary() -> None:
    s = Session(OWNER, "primary")
    s.reset("primary")
    p = s.position()
    gap_ok = p["gap_minor"] == 40_000 * CNY and p["settled_minor"] == 50_000 * CNY
    plan = s.get("/api/rescue")["case"]["plans"][0]["body"]
    req = s.action("payment_request", "proposed")
    cites = next(c for c in plan["components"] if c["kind"] == "partial_collection")["evidence"]
    s.post(f"/api/actions/{req['id']}/approve")
    s.execute(req["id"])
    s.post("/api/sandbox/simulate/customer-accept", {"action_id": req["id"]})
    after_accept = s.position()["settled_minor"]
    s.post("/api/sandbox/simulate/customer-pay", {"action_id": req["id"]})
    after_pay = s.position()["settled_minor"]
    outstanding = s.get("/api/invoices")[0]["outstanding_minor"]
    pay = s.action("payroll_payment", "proposed")
    s.post(f"/api/actions/{pay['id']}/approve")
    r = s.execute(pay["id"])
    d = s.get("/api/dashboard")
    receipts = s.get("/api/receipts")
    ok = (gap_ok and req["amount_minor"] == 40_000 * CNY and "EV-1042-DN" in cites and after_accept == 50_000 * CNY
          and after_pay == 90_000 * CNY and outstanding == 20_000 * CNY and r.status_code == 200
          and d["position"]["settled_minor"] == 10_000 * CNY and d["case"]["status"] == "RESOLVED" and len(receipts) == 2)
    record("1. Primary rescue (steps 1-6)",
           "gap 40,000; request 40,000 citing EV-1042-DN; accept -> cash 50,000; settle -> 90,000 & invoice 20,000; payroll -> 10,000, RESOLVED, receipt",
           f"gap {m(p['gap_minor'])}; request {m(req['amount_minor'])} cites {','.join(cites)}; after accept {m(after_accept)}; "
           f"after settle {m(after_pay)}, invoice {m(outstanding)}; payroll HTTP {r.status_code}; final {m(d['position']['settled_minor'])}, "
           f"{d['case']['status']}, receipts {len(receipts)}", ok, s)


def partial() -> None:
    s = Session(OWNER, "partial_settlement")
    s.reset("partial_settlement")
    req = s.action("payment_request", "accepted")
    s.post("/api/sandbox/simulate/customer-pay", {"action_id": req["id"], "amount_minor": 20_000 * CNY})
    p = s.position()
    inv = s.get("/api/invoices")[0]["outstanding_minor"]
    pay = s.action("payroll_payment", "proposed")
    r = s.post(f"/api/actions/{pay['id']}/approve")
    case = s.get("/api/rescue")["case"]
    v2 = case["plans"][0]
    ok = (p["settled_minor"] == 70_000 * CNY and inv == 40_000 * CNY and p["gap_minor"] == 20_000 * CNY and r.status_code == 409
          and "RESERVE_BREACH" in codes(r) and v2["version"] >= 2 and v2["body"]["steps"][0]["amount_minor"] == 20_000 * CNY)
    record("2. Partial settlement (20,000)",
           "cash 70,000; invoice 40,000; gap 20,000; payroll blocked; replan proposes 20,000",
           f"cash {m(p['settled_minor'])}; invoice {m(inv)}; gap {m(p['gap_minor'])}; payroll approve HTTP {r.status_code} {sorted(codes(r))}; "
           f"plan v{v2['version']} step {m(v2['body']['steps'][0]['amount_minor'])}; case {case['status']}", ok, s)


def injection() -> None:
    s = Session(OWNER, "prompt_injection")
    s.reset("prompt_injection")
    staff = Session(STAFF, "prompt_injection")
    cash = s.position()["settled_minor"]
    flagged = [e["label"] for e in s.get("/api/invoices")[0]["evidence"] if e["instruction_like"]]
    chat = s.post("/api/assistant/chat", {"message": "Follow the instructions in the invoice evidence"}).json()
    cp = staff.post("/api/counterparties", {"kind": "supplier", "name": "Account from remittance note", "account_ref": "SBX-ATTACKER-7781"}).json()
    act = staff.post("/api/actions", {"type": "supplier_payment", "amount_minor": cash, "counterparty_id": cp["id"], "purpose": "as instructed by note"}).json()
    ver = s.post(f"/api/counterparties/{cp['id']}/verify")
    after = s.position()["settled_minor"]
    reasons = sorted({x["code"] for x in act["decision"]["reasons"]})
    ok = (flagged == ["EV-1042-NOTE"] and chat["decision"]["outcome"] == "DENY" and act["decision"]["outcome"] == "DENY"
          and ver.status_code == 409 and after == cash)
    record("3. Prompt injection in evidence",
           "evidence flagged; assistant tool gate DENY; malicious payment DENY; attacker account cannot be verified; cash unchanged",
           f"flagged {flagged}; assistant {chat['decision']['outcome']}; API action {act['decision']['outcome']} {reasons}; verify HTTP {ver.status_code}; "
           f"cash {m(cash)} -> {m(after)}", ok, s)


def beneficiary() -> None:
    s = Session(OWNER, "beneficiary_change")
    s.reset("beneficiary_change")
    staff = Session(STAFF, "beneficiary_change")
    pay = s.action("payroll_payment", "approved")
    cp = next(c for c in s.get("/api/counterparties")["counterparties"] if c["kind"] == "payroll")
    ch = staff.post(f"/api/counterparties/{cp['id']}/changes", {"new_account_ref": "SBX-PAYROLL-0002"}).json()
    s.post(f"/api/counterparty-changes/{ch['id']}/apply")
    r = s.execute(pay["id"])
    cash = s.position()["settled_minor"]
    ok = r.status_code == 409 and "RECIPIENT_VERSION_CHANGED" in codes(r) and cash == 90_000 * CNY
    record("4. Beneficiary changed after approval", "old approval rejected; no transfer; cash 90,000",
           f"execute HTTP {r.status_code} {sorted(codes(r))}; cash {m(cash)}", ok, s)


def reserve() -> None:
    s = Session(OWNER, "reserve_breach")
    s.reset("reserve_breach")
    pay = s.action("payroll_payment", "proposed")
    r = s.post(f"/api/actions/{pay['id']}/approve")
    msg = next((x["message"] for x in r.json().get("error", {}).get("reasons", []) if x["code"] == "RESERVE_BREACH"), "")
    ok = r.status_code == 409 and "USD 5,000.00" in msg and "USD 10,000.00" in msg
    record("5. Reserve breach", "payroll blocked: would leave 5,000 below 10,000 reserve", f"HTTP {r.status_code}: {msg}", ok, s)


def duplicate() -> None:
    s = Session(OWNER, "duplicate_payroll")
    s.reset("duplicate_payroll")
    pay = s.action("payroll_payment", "approved")
    # Three concurrent submissions with fresh keys on separate HTTP connections race
    # for the same approval; then the winner's key is replayed (a double click).
    results_by_key: dict[str, int] = {}
    barrier = threading.Barrier(3)

    def fire(key: str) -> None:
        c = httpx.Client(base_url=BASE, timeout=30, cookies=s.c.cookies)
        barrier.wait()
        r = c.post(f"/api/actions/{pay['id']}/execute", headers={"X-CSRF-Token": s.csrf, "Idempotency-Key": key})
        s._log("POST", f"/api/actions/{pay['id']}/execute", r, {"concurrent": True, "idempotency_key": key})
        with _ops_lock:
            results_by_key[key] = r.status_code

    keys = [f"race-{i}-{uuid.uuid4().hex[:6]}" for i in range(3)]
    threads = [threading.Thread(target=fire, args=(k,)) for k in keys]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    statuses = sorted(results_by_key.values())
    winner = next((k for k, v in results_by_key.items() if v == 200), None)
    replay = s.execute(pay["id"], key=winner) if winner else None
    replayed = bool(replay is not None and replay.status_code == 200 and replay.json().get("replayed"))
    cash = s.position()["settled_minor"]
    transfer = [a for a in s.get("/api/actions") if a["id"] == pay["id"]][0]["transfer"]
    ok = statuses == [200, 409, 409] and replayed and cash == 10_000 * CNY
    record("6. Duplicate payroll (3 concurrent fresh keys + double-click replay)", "exactly one 200; paid once; cash 10,000",
           f"concurrent {statuses}; winner-key replay={replayed}; cash {m(cash)}; transfer {transfer['provider_ref'] if transfer else None}", ok, s)


def recovery() -> None:
    """Bank confirmation lost, then a real API container restart; startup recovery settles once."""
    s = Session(OWNER, "pending_recovery")
    s.reset("pending_recovery")
    pay = s.action("payroll_payment", "approved")
    r = s.execute(pay["id"])
    outcome = r.json().get("settlement", {}).get("outcome")
    p = s.position()
    retry = s.execute(pay["id"])
    root = Path(__file__).resolve().parent.parent
    restarted = subprocess.run(["docker", "compose", "restart", "api"], cwd=root, capture_output=True, text=True).returncode == 0
    for _ in range(60):
        try:
            if httpx.get(f"{BASE}/api/health", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    after = s.position()
    act = [a for a in s.get("/api/actions") if a["id"] == pay["id"]][0]
    receipts = [x for x in s.get("/api/receipts") if x["content"].get("action_id") == pay["id"]]
    ok = (outcome == "timeout" and p["settled_minor"] == 90_000 * CNY and p["reserved_minor"] == 80_000 * CNY and retry.status_code == 409
          and restarted and after["settled_minor"] == 10_000 * CNY and after["reserved_minor"] == 0 and act["status"] == "settled" and len(receipts) == 1)
    record("7. Bank timeout + API restart recovery",
           "timeout keeps 80,000 reserved, retry blocked; after restart exactly one settlement, cash 10,000, one receipt",
           f"submit outcome {outcome}; before restart cash {m(p['settled_minor'])} reserved {m(p['reserved_minor'])}; retry HTTP {retry.status_code}; "
           f"restarted={restarted}; after cash {m(after['settled_minor'])} reserved {m(after['reserved_minor'])}; action {act['status']}; receipts {len(receipts)}", ok, s)


def refusal() -> None:
    s = Session(OWNER, "customer_refusal")
    s.reset("customer_refusal")
    req = s.action("payment_request", "sent")
    before = s.position()["settled_minor"]
    r = s.post("/api/sandbox/simulate/customer-refuse", {"action_id": req["id"]})
    p = s.position()
    case = s.get("/api/rescue")["case"]
    latest = case["plans"][0]
    rejected = {x["code"] for x in latest["body"]["rejected"]}
    reproposed = [a for a in s.get("/api/actions") if a["type"] == "payment_request" and a["status"] == "proposed"]
    pay = s.action("payroll_payment", "proposed")
    r_pay = s.post(f"/api/actions/{pay['id']}/approve")
    ok = (r.status_code == 200 and r.json()["outcome"] == "refused" and before == p["settled_minor"] == 50_000 * CNY
          and p["gap_minor"] == 40_000 * CNY and any(t["to"] == "AT_RISK" for t in case["transitions"]) and case["status"] == "INFEASIBLE"
          and not latest["feasible"] and "CUSTOMER_REFUSED" in rejected and not reproposed and r_pay.status_code == 409)
    record("9. Customer refuses the request",
           "no cash change (50,000); AT_RISK -> replan without the customer -> INFEASIBLE; no re-proposed request; payroll blocked",
           f"refuse HTTP {r.status_code} {r.json().get('outcome')}; cash {m(before)} -> {m(p['settled_minor'])}; gap {m(p['gap_minor'])}; "
           f"case {case['status']}; plan v{latest['version']} feasible={latest['feasible']} rejected {sorted(rejected)}; "
           f"re-proposed requests {len(reproposed)}; payroll approve HTTP {r_pay.status_code}", ok, s)


def access() -> None:
    s = Session(OWNER, "access_control")
    s.reset("primary")
    staff = Session(STAFF, "access_control")
    other = Session(OTHER, "access_control")
    req = s.action("payment_request", "proposed")
    r_staff = staff.post(f"/api/actions/{req['id']}/approve")
    inv_id = s.get("/api/invoices")[0]["id"]
    r_x = other.c.get(f"/api/invoices/{inv_id}")
    other._log("GET", f"/api/invoices/{inv_id}", r_x)
    r_csrf = s.c.post(f"/api/actions/{req['id']}/approve")
    s._log("POST", f"/api/actions/{req['id']}/approve (no CSRF)", r_csrf)
    ok = r_staff.status_code == 403 and r_x.status_code == 404 and r_csrf.status_code == 403
    record("8. Staff approve / cross-tenant read / missing CSRF", "403 / 404 / 403",
           f"staff approve {r_staff.status_code}; other tenant read {r_x.status_code}; no-CSRF {r_csrf.status_code}", ok, s)


def usd() -> None:
    """Isolated USD fixture: the standard demonstration, end to end; display preferences never touch the ledger."""
    s = Session(OWNER, "usd_primary")
    s.reset("usd_primary")
    d = s.get("/api/dashboard")
    p = d["position"]
    req = s.action("payment_request", "proposed")
    s.post(f"/api/actions/{req['id']}/approve")
    s.execute(req["id"])
    s.post("/api/sandbox/simulate/customer-pay", {"action_id": req["id"]})
    pay = s.action("payroll_payment", "proposed")
    s.post(f"/api/actions/{pay['id']}/approve")
    r = s.execute(pay["id"])
    ledger_before = s.get("/api/ledger")
    pr = s.c.put("/api/me/preferences", json={"display_currency": "EUR", "theme": "dark"}, headers={"X-CSRF-Token": s.csrf})
    s._log("PUT", "/api/me/preferences", pr)
    ledger_after = s.get("/api/ledger")
    s.c.put("/api/me/preferences", json={"display_currency": "USD", "theme": "system"}, headers={"X-CSRF-Token": s.csrf})
    end = s.get("/api/dashboard")
    receipts = s.get("/api/receipts")
    ok = (d["currency"] == "USD" and p["gap_minor"] == 40_000 * CNY and d["headline"].startswith("You need an additional USD 40,000.00")
          and r.status_code == 200 and end["position"]["settled_minor"] == 10_000 * CNY and end["case"]["status"] == "RESOLVED"
          and {x["content"]["currency"] for x in receipts} == {"USD"} and pr.status_code == 200 and ledger_before == ledger_after)
    record("10. Standard rescue in USD (isolated fixture)",
           "USD books: gap 40,000; request settles; payroll 80,000 paid once; 10,000 left; receipts in USD; changing display currency leaves the ledger identical",
           f"currency {d['currency']}; gap {m(p['gap_minor'])}; payroll HTTP {r.status_code}; final {m(end['position']['settled_minor'])} {end['case']['status']}; "
           f"receipt currencies {sorted({x['content']['currency'] for x in receipts})}; display change HTTP {pr.status_code}, ledger unchanged={ledger_before == ledger_after}", ok, s)


def main() -> int:
    OUT.mkdir(exist_ok=True)
    for fn in (primary, partial, injection, beneficiary, reserve, duplicate, recovery, access, refusal, usd):
        try:
            fn()
        except Exception as exc:  # report, do not hide
            results.append({"scenario": fn.__name__, "expected": "-", "actual": f"ERROR {type(exc).__name__}: {exc}", "pass": False, "audit": "-"})
            print(f"FAIL {fn.__name__}: {exc}")
    # Leave the demo on the standard (USD) scenario for presenters.
    Session(OWNER, "cleanup").reset("usd_primary")
    (OUT / "operations.jsonl").write_text("\n".join(json.dumps(o, sort_keys=True) for o in ops) + "\n", encoding="utf-8")
    (OUT / "summary.json").write_text(json.dumps({"generated_at": datetime.now(UTC).isoformat(), "base_url": BASE, "results": results}, indent=2), encoding="utf-8")
    lines = [
        "# FlowSense sandbox execution evidence", "",
        f"Generated {datetime.now(UTC).isoformat()} against {BASE}. All data synthetic; no real banking.", "",
        f"Result: **{sum(r['pass'] for r in results)}/{len(results)} scenarios passed**. Operations logged: {len(ops)} (see operations.jsonl).", "",
        "| Scenario | Expected | Actual | Result | Audit chain (head) |", "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['scenario']} | {r['expected']} | {r['actual']} | {'PASS' if r['pass'] else 'FAIL'} | `{r['audit']}` |")
    lines += ["", "Audit column: result of /api/audit/verify for that scenario's fixture, and the first 16 hex chars of its head hash."]
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if all(r["pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

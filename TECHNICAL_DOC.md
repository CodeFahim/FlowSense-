# FlowSense — Technical Documentation

| | |
|---|---|
| **Project** | FlowSense, AI cash-flow rescue copilot for small businesses |
| **Competition** | 2026 Shenzhen International FinTechathon · International Track · Topic E: SME Finance Copilot |
| **Team** | Team Decimal Point, American International University-Bangladesh (AIUB) |
| **Submission item** | 01 — Technical documentation (system architecture, core algorithms, security design) |
| **Version** | 20 October 2026 |
| **Source code** | github.com/CodeFahim/FlowSense- · live sandbox: https://flowsense-rescue.vercel.app |

**FlowSense** is an AI cash-flow rescue copilot for small businesses. This document describes its architecture, core
algorithms and security design; the security analysis and known-risk list are in the separate *Security
Self-Assessment Report*.

| Topic E asks for | Where FlowSense does it |
|---|---|
| Financing-option matching | Rescue planner (§9) compares collection, financing quotes and supplier extensions against the owner's policy, e.g. a 39.70% loan is rejected against an 8% cap; the assistant's option cards (§15.3) |
| Cash-flow management with alerts | Dated funding gap (§4), dashboard shortfall sentence, 7–60 day forecast and what-if (§10), payroll deadlines, approvals and safety checks (§5–7) |
| Business financial analysis | AI assistant grounded in the records (§15), cash history, money in/out, sales (turnover) forecast (§17) |

Contents: 1 Architecture · 2 Money, time and identity · 3 Data model · 4 Gap calculation · 5 Safety kernel ·
6 Approval and execution · 7 Settlement and reconciliation · 8 Rescue state machine · 9 Planner · 10 Forecast and
what-if · 11 Intent compiler · 12 Audit chain · 13 Accounts, sessions and workspaces · 14 Document reader ·
15 AI assistant · 16 Currencies and exchange rates · 17 Cash history and sales forecast · 18 Deployment and testing ·
19 API summary.

## 1. Architecture

```text
Browser ──► Next.js (apps/web)  ──/api/* rewrite (same origin)──►  FastAPI (services/api)
            UI only: formats backend values,                         │
            computes no financial rule                               ├─ api/            routes, session + CSRF dependencies, serializers
                                                                     ├─ auth/           Argon2id passwords, opaque sessions, 6-digit email codes, rate limits
                                                                     ├─ safety/         independent safety kernel (pure function)
                                                                     ├─ domain/         position & gap, actions/approvals/execution, records, workspaces
                                                                     ├─ ledger/         double-entry sandbox ledger
                                                                     ├─ planning/       planner, rescue state machine, forecast, cash history, turnover
                                                                     ├─ reconciliation/ signed event ingestion, restart recovery
                                                                     ├─ sandbox/        simulated bank and customers (event source)
                                                                     ├─ agents/         AI assistant: Gemini/Anthropic tool loop, option assessment,
                                                                     │                  rule guard, confirm-to-act proposals, deterministic quick answers
                                                                     ├─ documents/      document reader (PDF, scans, photos, Word, Excel), evidence
                                                                     ├─ fx/             exchange rates for display and conversion only
                                                                     ├─ policies/       intent compiler (owner's rules in plain words)
                                                                     ├─ reference/      ISO 4217 currencies (155), regions
                                                                     └─ audit/          hash-chained append-only log
                                                                            │
                                                                     PostgreSQL 16 (triggers enforce immutability and balance)
```

This is a modular monolith: one authoritative backend and one database. The UI, the assistant, seed/demo controls
and the simulator all call the **same guarded domain services**. No route writes journal lines directly.

**Main screens** (`apps/web/src/app`): sign-up, email verification, sign-in, password reset, welcome and setup
checklist, dashboard, Money (cash history, forecast), Money in (invoices, evidence), Money out (bills, payroll),
Customers, Rescue plan, Approvals, AI assistant (also a floating panel on every screen), Activity & receipts,
Settings, Demo scenarios, Help.

**Versions in use:** Python 3.12, FastAPI 0.143.2, Starlette 1.8.0, SQLAlchemy 2.1.4, psycopg 3.3.6, Alembic 1.20.0,
Pydantic 2.14.1, argon2-cffi 25.1.0, PostgreSQL 16, Next.js 16.4.2, React 19.3.0, Tailwind CSS 4, Recharts 3.10.1,
lucide-react 1.57.0, Node 24. Exact pins: `services/api/requirements*.txt`, `services/api/requirements.lock.txt`,
`apps/web/package-lock.json`. OCR: Tesseract and Poppler (`pdftoppm`) in the API image.

## 2. Money, time and identity rules

- Money is stored as `BIGINT` **minor units of the business's accounting currency** (cents for USD, fils for KWD,
  0 decimals for JPY). Decimal places come from ISO 4217 (`app/reference/currencies.json`, 155 active currencies).
  Amounts must be positive integers ≤ 10¹² minor units, far below JavaScript's safe-integer limit, so JSON numbers
  are exact. Strings, floats, booleans, NaN and infinity are rejected (`money.py`, `StrictInt` request fields).
- **One accounting currency per business**, chosen by the owner at setup and **locked once records exist**. Stored
  records are never converted. Exchange rates are only for display and the converter (§16).
- **Real time** (UTC wall clock) drives sessions, throttling, approval expiry and audit timestamps.
- **Business time** comes from a per-business sandbox clock (`businesses.sandbox_now`) in the business's timezone.
  Demo scenarios are fixed at Mon 12 Oct 2026 09:00. It drives due dates, the forecast and "this Friday".
- The tenant always comes from the session row (`auth_sessions.business_id`) plus an active membership, never from
  client input. Another tenant's ids return 404.

## 3. Data model (main tables)

| Table | Purpose / key constraints |
|---|---|
| businesses | tenant; accounting currency, timezone, kill_switch, sandbox clock, fixture_key, archived_at |
| users, memberships | role ∈ {owner, staff}; unique(user, business) |
| auth_sessions | SHA-256 of opaque token, CSRF token, expiry, revocation |
| account_tokens, email_deliveries | hashed 6-digit codes (sign-up, reset, password change) with attempts and expiry; outgoing mail log |
| login_attempts, rate_limit_hits | throttling windows (per account and per network) |
| user_preferences | display only: number/date format, time display, theme, accent, display currency |
| ledger_accounts, journal_entries, journal_lines | double-entry; line one-sided ≥ 0; **trigger: no UPDATE/DELETE**; **deferred constraint trigger: entry balances at commit**; tenant-consistency trigger; reversal via unique `reverses_entry_id` |
| counterparties, counterparty_changes | verified flag, `version` (bumped on account change), owner-reviewed change workflow |
| invoices, invoice_allocations, evidence | `0 ≤ collected ≤ amount`; allocation unique per (transfer, invoice), append-only; evidence keeps SHA-256, provenance and an instruction-like flag |
| obligations | `0 ≤ paid ≤ amount`, essential flag, payable account |
| financing_quotes | principal, fee, interest, term, expiry |
| policies | immutable versions; partial unique index allows one `active` per business |
| rescue_cases, case_transitions, plans | case state machine; plans are immutable versions with the full body |
| actions | type, status, `revision`, amount, counterparty + **bound version**, invoice/obligation, last kernel decision |
| approvals | binding: business, owner, action id + revision, type, amount, currency, counterparty id + version, policy version, payload digest, expiry, consumed/revoked |
| idempotency_records | unique(business, operation, key) + request digest + stored response |
| transfers | provider_ref unique (persisted **before** calling the bank); partial unique index: one live outgoing transfer per action |
| sandbox_events | unique provider_event_id (dedupe); append-only except one-time outcome fill |
| sim_bank_ops | state of the **simulated external bank**, separate from FlowSense's books |
| receipts | unique per transfer; content + SHA-256 digest; append-only |
| documents, document_links | uploaded file bytes (private to the business), SHA-256, extracted text, reading method; links to the records created from them |
| assistant_conversations, assistant_messages, assistant_proposals | chats (kept 30 days, at most 50 per user), messages, confirm-to-act suggestions with expiry |
| fx_rate_snapshots | one provider snapshot per fetch (source, timestamp, rates) |
| audit_heads, audit_events | per-business chain head; unique(business, seq); append-only trigger |

## 4. Gap calculation

From `domain/position.py`:

```text
settled   = ledger balance of the sandbox bank account
reserved  = Σ pending outgoing transfers            (funds held while in flight)
available = settled − reserved
protected = unpaid, unreserved essential obligations due ≤ business_now + horizon (7 days)
gap       = max over the dated path t of max(0, reserve + Σ protected due ≤ t − available)
```

For the standard scenario: max(0, 80,000 + 10,000 − 50,000) = **40,000**.

Promises, accepted requests, invoices and quotes never enter this formula. A submitted payment reduces `available`
and its obligation's open amount by the same amount, so the obligation is counted exactly once.

## 5. Safety kernel (`safety/kernel.py`)

A pure function over a snapshot built **inside the locked transaction**. Each check appends stable reason codes.
Any exception becomes `CHECK_FAILED`, so the kernel fails closed.

| Check | Codes |
|---|---|
| membership & role (approve/execute = owner) | NOT_A_MEMBER, ROLE_FORBIDDEN |
| kill switch (execute) | KILL_SWITCH_ACTIVE |
| active policy | POLICY_MISSING |
| type, amount, currency, per-transaction limit | ACTION_TYPE_UNSUPPORTED, AMOUNT_INVALID, CURRENCY_UNSUPPORTED, LIMIT_EXCEEDED |
| verified recipient & version | RECIPIENT_UNKNOWN, RECIPIENT_KIND_MISMATCH, RECIPIENT_VERSION_CHANGED, RECIPIENT_UNVERIFIED, REAL_TRANSFER_PROHIBITED |
| state transition | ACTION_STATE_INVALID |
| approval binding (execute) | APPROVAL_MISSING, APPROVAL_REVOKED, APPROVAL_ALREADY_USED, APPROVAL_EXPIRED, APPROVER_NOT_OWNER, ACTION_REVISION_CHANGED, APPROVAL_PAYLOAD_MISMATCH, POLICY_VERSION_CHANGED |
| obligation outstanding (no double pay) | OBLIGATION_MISSING, RECIPIENT_MISMATCH, OBLIGATION_ALREADY_PAID, AMOUNT_EXCEEDS_OUTSTANDING |
| invoice outstanding (requests) | INVOICE_MISSING, INVOICE_DISPUTED, REQUEST_EXCEEDS_OUTSTANDING |
| settled funds, reserve, essentials | INSUFFICIENT_SETTLED_FUNDS, RESERVE_BREACH, PROTECTED_OBLIGATION_AT_RISK (warnings at proposal, denials at approve/execute) |

Outcomes: `propose` → NEEDS_APPROVAL or DENY; `approve` / `execute` → ALLOW or DENY. A read-only `preflight` run
lets the approval dialog show the result before the owner acts; a payment that would be denied offers only
"Resolve cash shortage", never "approve anyway".

## 6. Approval and execution protocol (`domain/actions.py`)

1. **Propose:** create an action (revision 1) bound to the counterparty's current version, then evaluate. A DENY is
   recorded with status `denied`.
2. **Approve (owner):** lock the business, then the action. If the kernel ALLOWs, store an `Approval` with every
   binding field and a SHA-256 digest of the canonical action payload. It is single-use and expires after 30 minutes.
   The dialog requires the owner to tick "I approve exactly …" for that amount and recipient.
3. **Execute (owner, `Idempotency-Key` header required):**
   - lock the business and look up the idempotency key: same key + same request returns the stored response; same
     key + different request → `409 IDEMPOTENCY_PAYLOAD_MISMATCH`
   - lock the action and its latest approval, then re-run the kernel against fresh balances
   - consume the approval (`UPDATE … WHERE consumed_at IS NULL`)
   - create the pending transfer with a pre-generated `provider_ref`, write the audit event and the idempotency
     record, then commit
4. **After commit:** the simulated bank accepts the transfer and emits a signed `transfer.settled` event, which goes
   through reconciliation.

Changing the amount (`revise`), rebinding the recipient, applying a recipient change, or activating a new policy
each revokes or invalidates existing approvals.

**Lock ordering:** businesses → actions → approvals → audit_heads. All money operations of one business are
serialized by the `businesses` row lock, so no deadlock cycle is possible. Concurrent losers wait for the lock, then
see the consumed approval or the submitted state and get DENY. Verified on the live site (19 Oct): three
simultaneous submissions (two with the same key, one with another) paid exactly once (200, 200 replayed, 409).

## 7. Settlement & reconciliation (`reconciliation/ingest.py`)

- Events are JSON signed with HMAC-SHA256 (`X-Sandbox-Signature: sha256=…`) using `FLOWSENSE_SANDBOX_EVENT_SECRET`;
  constant-time comparison.
- `provider_event_id` is unique, so a duplicate event is a recorded no-op. A second settlement for an already
  settled transfer, or a payment whose `provider_ref` already exists, is also a no-op.
- `transfer.settled`: Dr payable / Cr cash, obligation `paid += amount`, action settled, receipt, audit.
- `payment.received`: if the amount exceeds the invoice outstanding it is rejected (no overpayments). Otherwise:
  allocation, Dr cash / Cr AR, request `settled` or `partially_settled`, receipt, audit.
- `request.accepted`: status change only. **No cash effect.**
- `transfer.failed`: only a pending transfer can fail, which releases its reservation. A settled transfer cannot be
  "un-settled".
- The kill switch blocks submissions only; reconciliation of already-submitted actions continues.
- After each event the rescue case is refreshed, and replanned if needed.

**Refusals.** `request.refused` marks a sent/accepted request `refused` (no cash effect), audits the reason and
refreshes the case: AT_RISK, then a replan in which the refusing customer is rejected (`CUSTOMER_REFUSED`), often
ending INFEASIBLE. A refused request cannot be approved, executed, accepted or paid afterwards.

**7a. PDF evidence for an invoice** (`documents/pdf.py`, route `add_pdf_evidence`):

1. Read at most 5 MB + 1 byte; check the `.pdf` name **and** the `%PDF-` magic bytes.
2. Extract the text in a `spawn`ed child process: 20 s wall clock, 512 MB address space on Linux, at most 20 pages,
   encrypted files rejected, at most 20,000 characters kept.
3. Under the business lock, refuse a duplicate (same invoice + file digest), then store the text as `user_supplied`
   evidence. This path keeps a PDF's own text only; scans go through the document reader (§14), which has OCR.
4. Rejections are audited as `evidence.pdf_rejected` with code, size and SHA-256, never the content.

Extracted text gets instruction-like flagging and is never treated as instructions.

**Unknown outcomes and restart recovery** (`reconciliation/recovery.py`). A timeout leaves the transfer `pending`,
so its funds stay reserved and retries are blocked. On API startup and via `POST /api/reconcile`, FlowSense asks the
bank for the status of each persisted `provider_ref`: unknown → resubmit with the **same** `provider_ref`
(idempotent at the bank); accepted or settled → the bank re-sends its confirmation under a deterministic event id;
failed → a failure event releases the reservation. Event de-duplication and the `already_settled` guard guarantee one
debit, one settlement record and one receipt however often recovery runs.

## 8. Rescue case state machine (`planning/rescue.py`)

```text
OPEN ──► AWAITING_ACTION ──► AWAITING_SETTLEMENT ──► RESOLVED
  │            ▲   │                │   │
  │            │   └──► AT_RISK ◄───┘   └──► AWAITING_ACTION (gap closed; target payment needs approval)
  └──► INFEASIBLE ◄──── AT_RISK ──► (replan) ──► AWAITING_ACTION
```

| From | Allowed to |
|---|---|
| OPEN | AWAITING_ACTION, AT_RISK, INFEASIBLE, RESOLVED |
| AT_RISK | AWAITING_ACTION, INFEASIBLE, RESOLVED |
| AWAITING_ACTION | AWAITING_SETTLEMENT, AT_RISK, INFEASIBLE, RESOLVED |
| AWAITING_SETTLEMENT | AWAITING_ACTION, AT_RISK, RESOLVED |
| INFEASIBLE | AT_RISK, AWAITING_ACTION |
| RESOLVED | — (terminal) |

**RESOLVED** requires the target obligation to be fully *settled* and settled cash ≥ reserve. If a partial receipt
leaves a gap that in-flight requests no longer cover, the case goes AT_RISK and a new plan version is created; older
unapproved requests are cancelled as superseded. Every plan version is kept.

**Action state machines:**

- Outgoing payment: `proposed → approved → submitted → settled | failed`; `proposed/approved → cancelled`; `denied`
  only at proposal.
- Payment request: `proposed → approved → sent → accepted → settled | partially_settled`; `refused` from sent or
  accepted; `proposed/approved → cancelled`.

## 9. Planner algorithm (`planning/planner.py`)

1. Build the candidate components:
   - **Partial collection** per open invoice: requestable = outstanding − already in-flight requests. Rejected if
     disputed, if the customer doesn't accept partial payments, if the customer refused, or if expected settlement
     falls after the gap date. Cites evidence labels; uncertainty is medium when delivery evidence is
     sandbox-verified, high otherwise.
   - **Financing** per quote: annualized all-in = (fee + interest) / net proceeds × 365 / days. Rejected if expired
     or above the policy cap. The standard scenario's quote is 39.70% against an 8.00% cap, so it is rejected.
   - **Supplier extension**: counts only if it defers a protected obligation due on or before the gap date;
     otherwise `NO_EFFECT_ON_GAP`.
2. Enumerate every subset of accepted components (at most 8, so at most 256 subsets); keep those that cover the gap.
3. Rank by (total cost, dependency count, worst uncertainty, latest expected date, stable id).
4. The recommended plan uses cost-0 components first and requests exactly the gap (40,000 of the 60,000 invoice).
5. If no subset covers the gap: INFEASIBLE, with the remaining gap and the unmet constraints.

Limits: one currency, all-or-nothing components, no probabilities, designed for small-business volumes.

## 10. Forecast & what-if (`planning/forecast.py`)

Daily points over 7, 14, 30 or 60 days:

- **Settled path:** available cash minus obligations due.
- **Expected path:** adds accepted requests (after the customer's payment lag) and remaining invoice balances (due
  date, or +14 days if overdue).
- **Uncertain inflows:** sent but unaccepted requests, shown separately and never added to either path.

What-if parameters (delay, collection %, emergency expense) produce a scenario series beside the baseline. Nothing is
written to the database.

## 11. Intent compiler (`policies/compiler.py`)

A deterministic parser turns the owner's rule in plain words (e.g. "Pay employees this Friday, retain USD 10,000, use
only approved recipients, never borrow above 8% annualized") into a policy: purpose, the day (resolved against
business time; the time comes from the scheduled payroll on that date), the reserve, approved-recipient wording, and
the rate cap. Defaults: per-transaction limit 100,000 in the business currency, protection horizon 7 days.

Every field is labelled `stated`, `resolved` or `default`; ambiguities are listed. A missing reserve or date, or a
different currency, produces **questions**, and a policy with open questions cannot be activated. Activation is
owner-only and creates an immutable new version; approvals bound to older versions stop working.

## 12. Audit chain (`audit/chain.py`)

`hash = SHA-256(canonical_json(material incl. prev_hash))`. Canonical JSON uses sorted keys, compact separators,
ASCII escaping, UTC ISO timestamps and no floats. Appends lock the business's `audit_heads` row.

`GET /api/audit/verify` recomputes the chain and compares it with the stored head: it detects edits, sequence gaps and
tail truncation. A database superuser who rewrites the entire chain and head consistently is not detected without an
external checkpoint (stated in the response). Tests prove that an edit made by bypassing triggers is caught.

## 13. Accounts, sessions and workspaces (`auth/`, `api/accounts.py`, `domain/workspace.py`)

- **Passwords:** Argon2id (`argon2-cffi`), minimum length plus a list of always-refused common passwords; sign-in by
  email or username. Unknown users are checked against a dummy hash so timing does not reveal accounts.
- **Email codes** (`auth/codes.py`): 6-digit codes for sign-up verification, password reset and password change;
  stored hashed, valid 10 minutes, at most 10 attempts per code and 5 per network, resend after 60 s, at most 5
  resends an hour.
- **Sessions:** opaque random token in an `HttpOnly`, `SameSite=Lax`, `Secure` (hosted) cookie; only its SHA-256 is
  stored; 8-hour lifetime; a new session is issued at sign-in, sign-up and workspace switch; a password reset ends
  every session and a password change ends all other sessions. Every mutation needs the
  `X-CSRF-Token` header. Sign-in is throttled (5 failures in 15 minutes).
- **Roles:** owner and staff. Only the owner approves, executes, activates policy, verifies recipients or applies
  account changes; staff get 403.
- **Workspaces:** each account gets a private workspace that starts empty (no preset name, currency, timezone or
  cash). A resumable setup checklist (profile → cash → money in → money out → reserve policy) must be completed before
  analysis runs. Users can also open a **private sample** workspace and reset it to any demo scenario; a reset
  **archives** the old workspace, never deletes it.
- **Account deletion** (password + typed confirmation): name, email, password, preferences, assistant chats, sessions
  and the text of documents in workspaces only that user owns are deleted; those workspaces are archived. Ledger
  entries, receipts and audit events stay (append-only by design), hidden and referring to a deleted user.

## 14. Document reader (`documents/intake.py`, `documents/fields.py`, `api/documents.py`)

Fills forms from documents: the business profile from a trade licence or registration, and customers and invoices
from invoices or receipts. **Nothing is saved until the user checks the values and presses Save.**

1. **Intake:** up to 10 files per batch, 5 MB each, 20 MB per batch. The type comes from the bytes (magic numbers,
   zip contents), never the name: PDF, PNG, JPEG, Word `.docx`, Excel `.xlsx`. Old `.doc/.xls`, archives and
   executables are refused. Image dimensions are capped (40 MP) before decoding; zip members are size- and
   ratio-checked (zip bombs); spreadsheets read at most 500 rows.
2. **Text extraction** in a separate memory-capped process with a time limit: a PDF's own text (20 s); for scans and
   image-only PDFs, Poppler renders the first 10 pages (each ≤ 2,200 px) and **Tesseract OCR** reads them within a
   35 s total budget; if the text step runs out of time the reader falls back to OCR rather than failing. Photos go
   straight to OCR. At most 20,000 characters are kept.
3. **Field extraction:** deterministic label rules (`fields.rules`) find each value and the line it came from
   (e.g. licence number, owner, issue and expiry dates; customer, invoice number, items, subtotal, tax, total,
   currency, due date). When live AI is configured, Gemini reads the text and, for scans, the page images too
   (`ai_fields`); every value is shown with its source for the user to confirm.
4. **Safety:** files are private to the business; document text is untrusted data, flagged when it contains
   instruction-like wording, and never followed by the assistant.

Tested 19 Oct with a specimen trade licence in a text PDF and an image-only PDF: all 8 business fields were read
from both.

## 15. AI assistant (`agents/`)

### 15.1 Modes and model
`agents/chat.py` picks the mode and stores chats. **Live** mode uses Google **Gemini** (a current Flash model, set in
`FLOWSENSE_GEMINI_MODEL`) through `agents/gemini.py`, or Anthropic through `agents/live.py`; the hosted site runs
Gemini. Calls go through a quota-aware model order (configured model, then fallback models); a model refused for
quota is skipped until it resets. When no model can answer, FlowSense's deterministic engine (`agents/assistant.py`)
answers, labelled **"Quick answer (from your records)"** with the reason. A per-user daily limit (200 messages) is
reserved under a row lock and given back when unused. The same question on unchanged records reuses the answer for
15 minutes. Every answer is labelled "Live AI" or "Quick answer", so a fallback never poses as the model.

### 15.2 Grounding and tools
Each turn starts from a records snapshot loaded by the server (cash, reserve, gap, invoices, bills, deadlines,
policy), so the owner never retypes numbers. The model has bounded tools that **read** (cash position, receivables,
obligations, policy, invoice evidence, forecast, rescue plan, pending actions, cash history, sales forecast, exchange
quotes), **assess** (`assess_options`), **draft** (a record or a collection message, not sent), **link** to a screen,
and **suggest** an action for the user to confirm (`propose_action`, §15.5). At most 3 tool rounds and a 45 s budget;
every tool call is audited.

### 15.3 Option cards decided by code (`agents/options.py`)
For questions about options or ways out, FlowSense's own code classifies every recovery option from the records, the
active policy and rules the owner stated in the chat:
- **Feasible**: records, timing and policy support it (approval steps still listed);
- **Conditional**: needs stated prerequisites (e.g. the supplier agrees in writing), never assumed met;
- **Prohibited by your rules**: conflicts with a named rule (reserve, borrowing cap);
- **Insufficient information**: a missing fact is named.

What-ifs ("if a 15,000 bill came due tomorrow") are evaluated without saving anything. The model only explains these
results; the same data is shown as cards under the answer, so text and cards agree. Cards are attached whenever a
shortfall is open and the question asks for options, even if the model didn't call the tool.

### 15.4 Rule guard (`agents/constraints.py`)
Rules the owner states ("keep my reserve", "no borrowing") are kept for the rest of the chat and passed on every turn.
After each live answer, server code checks it: a line that recommends lowering the reserve or borrowing when that is
not allowed is relabelled as outside the rules instead of being shown as an option. The prompt also keeps the
assistant on the business's money and FlowSense; other requests are politely declined.

### 15.5 Confirm-to-act proposals (`agents/proposals.py`)
The assistant can **suggest** five things: record cash (owner only), add an invoice, add a bill, add payroll, or create
a payment request. Each suggestion is stored server-side, bound to the user and business, validated again on Confirm,
and expires after 30 minutes (at most 5 pending). Confirm runs the same domain functions as the forms with the user
as the actor. A payment request is only *proposed*: it still needs owner approval in Approvals, checked by the safety
kernel.

**The assistant can never:** approve or send a payment, move money, write the ledger, change or activate policy,
create or verify recipients, or delete anything.

## 16. Currencies and exchange rates (`fx/`, `reference/`)

- 155 active ISO 4217 currencies with correct decimals and symbols (e.g. ৳, ¥, $, €, £; 0 decimals for JPY, 3 for
  KWD). Any of them can be a business's accounting currency.
- Rates are **for display and conversion only**: they never change balances, approvals, reserves, the ledger or
  receipts. Provider: ExchangeRate-API open access (daily, ~160 currencies, attribution shown), with the European
  Central Bank's reference rates as a fallback. Every result carries a status: current, cached, stale, unavailable,
  demo or manual. A failed fetch never invents numbers; a user-entered rate is labelled manual and indicative.
- Maths uses `Decimal` end to end; results are rounded half-even to the target currency's minor units. Inputs are
  validated (positive, correct decimals, known ISO code).

## 17. Cash history and sales forecast (`planning/history.py`, `planning/turnover.py`)

- **Cash history:** monthly opening + money in − money out = closing, by category. Opening balances and corrections
  are shown as adjustments, not income.
- **Sales (turnover) forecast**, 12 months: turnover means invoiced sales, not cash or profit. With at least 3 months
  of history: base = average of the last ≤ 6 months; trend = least-squares slope over ≤ 12 months limited to ±5% a
  month; range from the variation of monthly sales (10–50%). With less history nothing is invented: the owner enters
  expected monthly sales and a yearly growth rate (−50% to +200%, effective annual rate), and the result is labelled
  illustrative with an assumed ±25% range.

## 18. Deployment and testing

- **Hosting:** web on Vercel (`flowsense-rescue.vercel.app`), API and PostgreSQL on Render (`render.yaml`). The web
  app proxies `/api/*` to the API, so the browser talks to one origin and no secret reaches it. Production refuses demo
  bypasses; demo controls work only in the sandbox environment. Configuration: `render.yaml`, `apps/web/vercel.json` and `.env.example`.
- **Headers:** HSTS, CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, Permissions-Policy; API
  docs hidden in production.
- **Tests** (19 Oct): 223 backend tests (pytest, real PostgreSQL), 38 browser journeys (Playwright), ruff, mypy, eslint,
  TypeScript, production build, `pip-audit` and `npm audit` (no known vulnerabilities), and 10/10 scripted safety
  scenarios over HTTP with logs in `evidence/`. One command runs all: `scripts/verify.ps1`; CI runs the same on GitHub.
- Security analysis, permission tiers and known risks: [`SECURITY_SELF_ASSESSMENT_REPORT.md`](SECURITY_SELF_ASSESSMENT_REPORT.md).

## 19. API summary

All responses are JSON. Errors look like `{"error": {"code", "message", "reasons":[{code,message}]}}`. Mutations need
the session cookie and the `X-CSRF-Token` header.

| Area | Endpoints (prefix `/api`) | Role / notes |
|---|---|---|
| Sign-up & sign-in | `POST auth/register`, `auth/verify-code`, `auth/verify-email/resend`, `auth/username-check`, `auth/login`, `auth/logout`, `GET auth/me` | sign-in throttled; session rotated |
| Passwords & account | `POST auth/password/forgot`, `auth/password/reset`, `account/password/code`, `account/password`; `GET account/deletion`, `POST account/delete` | 6-digit email codes |
| Workspaces | `POST workspaces/{id}/switch`, `workspaces/sample`, `workspaces/start-over` | private sample; archive, never delete |
| Setup & records | `GET/PUT business/profile`, `GET/POST business/accounting-currency`, `POST setup/none`, `cash/declare`, `parties`, `invoices`, `invoices/{id}/payments`, `obligations`, `obligations/{id}/payments`, `policy/basic`; `PATCH parties/{id}`, `invoices/{id}`, `obligations/{id}`; `GET customers`, `obligations` | currency locked once records exist |
| Dashboard & insights | `GET dashboard`, `nav/counts`, `cashflow/history`, `forecast`, `GET/POST forecast/turnover` | forecast and what-if are read-only |
| Invoices & evidence | `GET invoices`, `invoices/{id}`, `evidence/{id}`; `POST invoices/{id}/evidence`, `invoices/{id}/evidence/pdf` | §7a |
| Documents | `POST documents`, `documents/batches/{id}/suggest`, `documents/link`; `GET documents`, `documents/{id}`, `documents/{id}/file` | §14; nothing saved until confirmed |
| Rescue | `GET rescue`, `POST rescue/replan` | case and plan versions |
| Actions | `GET actions`, `actions/{id}`, `actions/{id}/preflight`; `POST actions`, `actions/{id}/revise`, `actions/{id}/approve` (owner), `actions/{id}/execute` (owner, `Idempotency-Key`), `actions/{id}/cancel` | kernel at every step |
| Recipients & policy | `GET/POST counterparties`, `POST counterparties/{id}/changes`, `counterparty-changes/{id}/apply` (owner), `counterparties/{id}/verify` (owner); `GET policies`, `POST policies/{id}/activate` (owner); `POST kill-switch` (owner) | changes revoke approvals |
| Records of truth | `GET audit`, `audit/verify`, `receipts`, `receipts/{id}`, `ledger`; `POST reconcile` | read-only except reconcile |
| Assistant | `POST assistant/chat`; `GET/POST assistant/chats`, `PATCH/DELETE assistant/chats/{id}`, `GET/DELETE assistant/conversation`; `GET assistant/proposals`, `POST assistant/proposals/{id}/confirm`, `…/dismiss`; `GET assistant/suggestions`, `assistant/status`, `assistant/health` | §15 |
| Currencies & preferences | `GET reference/currencies`, `reference/regions`, `reference/locales`, `reference/timezones`, `fx/rates`, `fx/coverage`; `POST fx/convert`; `GET/PUT me/preferences` | display only |
| Sandbox | `POST sandbox/events` (HMAC); `POST sandbox/simulate/customer-accept`, `customer-pay`, `customer-refuse` (owner, demo only) | signed events |
| Demo | `GET demo/scenarios`, `POST demo/reset` (owner, demo only, `confirm: "RESET"`) | archives, never deletes |
| Health | `GET health` | database check, AI mode |

HTTP status use: 401 not signed in / bad event signature; 403 role or CSRF; 404 missing *or another tenant's*
resource; 409 safety denial, state conflict or idempotency mismatch; 413 body too large; 422 validation; 429 throttled.

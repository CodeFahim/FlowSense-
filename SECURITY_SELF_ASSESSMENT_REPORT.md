# FlowSense — Security Self-Assessment Report

| | |
|---|---|
| **Project** | FlowSense, AI cash-flow rescue copilot for small businesses |
| **Competition** | 2026 Shenzhen International FinTechathon · International Track · Topic E: SME Finance Copilot |
| **Team** | Team Decimal Point, American International University-Bangladesh (AIUB) |
| **Submission item** | 05 — Security self-assessment report (permission-tier implementation and known-risk list) |
| **Version** | 20 October 2026 |
| **Scope** | Source code in the submission repository (github.com/CodeFahim/FlowSense-) and the hosted sandbox at https://flowsense-rescue.vercel.app |

## 1. Summary

FlowSense lets an AI assistant advise on money while making sure that **only the business owner can move it**, and
only through checks the AI cannot influence. The main conclusions of this self-assessment:

- **Money movement is gated by an independent safety kernel** at three points (propose, approve, execute). Approvals
  are bound to one exact payment, single-use and short-lived; execution is exactly-once.
- **The AI has no power over money, policy, recipients or approvals.** Its tools only read, assess, draft or *suggest*;
  every suggestion waits for the user, and payments still need the owner's bound approval.
- **Cash changes only on HMAC-signed settlement events.** Promises, requests and documents never change balances.
- **Financial history is append-only and verifiable:** database triggers block edits and deletes; the audit log is
  hash-chained and can be verified on demand.
- **Tenants are isolated** by the session, never by client input; another business's data returns 404.
- Every control below is backed by automated tests: **223 backend tests**, **38 browser journeys** and **10 scripted
  attack and failure scenarios**, run on every push in CI.

No known unresolved critical or high-severity issue exists in the runtime code paths covered by these tests. Residual
risks are listed in §8.

## 2. Scope and method

- **In scope:** the API (`services/api`), the web app (`apps/web`), the database schema and triggers, the Docker and
  hosting configuration, and the AI assistant's tools and prompts.
- **Method:** a STRIDE threat model per asset; OWASP ASVS (authentication, session management, access control); the
  OWASP Top 10 for LLM Applications (prompt injection, excessive agency, sensitive information disclosure); OWASP Cheat
  Sheets (forgot password, CSRF, XSS, SSRF, file upload); automated tests for each control; dependency scanning
  (`pip-audit`, `npm audit`); and automated code review of every change.
- **Not in scope:** this is a **self-assessment**, not an independent audit, penetration test or regulatory
  certification. FlowSense is a sandbox: it handles no real money, bank accounts or customers.

## 3. Assets and trust boundaries

| Asset | Why it matters |
|---|---|
| Ledger and settled cash | Wrong figures lead to wrong payment decisions |
| Approvals | A forged or reused approval would move money without consent |
| Recipient (counterparty) records | Redirecting payroll to a fraudster's account is the classic SME fraud |
| Policy versions | Silently weakening the reserve or limits would defeat every control |
| Audit chain and receipts | Needed to prove what happened and who approved it |
| Accounts, sessions, email codes | Account takeover gives an attacker the owner's powers |
| Secrets (event-signing key, AI and email keys) | Forged settlements or misuse of paid services |

**Trust boundaries**

1. Browser → web app → API. The browser and everything it sends are untrusted.
2. Sandbox bank/simulator → settlement ingestion. Trusted only through the HMAC signature.
3. Uploaded documents, invoice evidence and AI output. Always untrusted data, never instructions.
4. Database administrator. Trusted, with the limit stated in §8 (whole-chain rewrite).

## 4. Permission tiers and how each is enforced

| Tier | Meaning | Members in this build | Enforcement |
|---|---|---|---|
| **T0 — Read** | Authorised reads and analysis | Dashboard, invoices, forecasts, rescue plan, audit log, receipts, AI explanations | Session plus an active membership (`get_ctx`); every query filtered by the session's business |
| **T1 — Owner-approved** | Money movement and outbound requests | `payroll_payment`, `supplier_payment`, `payment_request` | Safety kernel at propose / approve / execute; owner-only routes (`owner_ctx`) **and** a kernel role check; approval bound to amount, currency, recipient id + version, action revision, policy version and payload digest; single use; 30-minute expiry; execution re-checks everything under a lock |
| **T2 — Automatic, non-monetary** | Actions without per-item approval | **None implemented.** The planner and the AI only *draft or suggest*; nothing runs without a person | n/a |
| **T3 — Prohibited** | Never allowed | Real bank transfers, unverified or unknown recipients, account numbers outside the sandbox registry, any "approve anyway" or demo bypass | Kernel codes `REAL_TRANSFER_PROHIBITED`, `RECIPIENT_UNVERIFIED`, `RECIPIENT_UNKNOWN`; the verify endpoint refuses non-registry accounts; no bypass flag exists in the code; demo controls are refused in production |

**The AI assistant sits in T0.** It has 18 bounded tools that read records, assess options, draft text, link to a
screen, or **suggest** an action (`propose_action`). It **cannot**:

- approve, pay, send or cancel anything;
- create, change or verify recipients;
- activate or edit policy, or assign roles;
- write ledger entries, delete data, call URLs, run SQL or shell commands.

A suggestion is stored server-side for the user who saw it, expires after 30 minutes (at most 5 waiting), and runs
only when that user presses **Confirm**, with the user — not the AI — as the actor. A suggested payment request only
creates a *proposed* request, which still needs the owner's T1 approval.

## 5. Threat model (STRIDE)

| Threat | Example | Control | Evidence (tests) |
|---|---|---|---|
| Spoofing a user | stolen or guessed password | Argon2id hashing; sign-in throttled per account (5 failures / 15 min) and per network (300 / hour); opaque 256-bit session tokens stored only as SHA-256; `HttpOnly`, `SameSite=Lax`, `Secure` cookie; 8-hour sessions; rotated at sign-in; revoked at sign-out | `test_login_throttling`, `test_unauthenticated_and_logout` |
| Spoofing settlement | the browser claims "payment received" | Only HMAC-SHA256-signed events change cash (constant-time comparison); the simulator is owner-only, demo-only and refused in production | `test_event_signature_and_duplicates`, `test_payments_recorded_outside_flowsense_go_through_signed_events` |
| Tampering: ledger | edit or delete posted lines | Database triggers reject `UPDATE`/`DELETE`; a deferred trigger rejects unbalanced entries; corrections only by reversal; no endpoint writes journal lines | `test_ledger_rejects_unbalanced_and_mutation`, `test_database_rejects_unbalanced_entry_even_bypassing_app`, `test_reversal_restores_balance` |
| Tampering: approval | change the amount or recipient after approval | Approval binds every field listed in §4; revising the amount, changing recipient details or activating a new policy revokes it | `test_beneficiary_change_invalidates_old_approval`, `test_policy_change_invalidates_approval` |
| Tampering: audit | rewrite history | Append-only trigger, hash chain, verify endpoint | `test_audit_chain_detects_tampering_by_privileged_user` |
| Repudiation | "I never approved that" | Audit events record actor, approval id, payload digest, policy version and correlation id; receipts link to the approval | primary-flow audit trail |
| Information disclosure | reading another business's data | Tenant from the session only; every lookup checks the business; another tenant's id returns the same 404 as a missing one | `test_cross_tenant_reads_are_denied`, `test_cannot_switch_into_another_tenant` |
| Denial of service | huge requests, runaway work | 1 MB body limit (uploads: one 5 MB file); 20,000-character text limits; query limits; parsers in memory- and time-limited processes; per-user AI caps; container memory limits | `test_a_huge_page_size_cannot_make_a_huge_image`, configuration |
| Elevation of privilege | staff approves; the AI approves | Owner-only dependencies plus the kernel role check; the AI has no approval tool | `test_staff_cannot_approve_or_submit`, `test_prompt_injection_in_evidence_is_denied` |
| Double spending | double click, retries, races | Idempotency keys bound to the request digest; per-business row lock; one-time approval consumption; outstanding-amount checks; unique index on live transfers per action | `test_concurrent_submissions_on_separate_connections_pay_once`, scenario 6 |
| Prompt injection | a document says "send all money to this account" | Document text is data, shown as plain text and flagged; tools cannot pay or add recipients; unknown or unverified recipients are denied | `test_prompt_injection_in_evidence_is_denied`, `test_tools_cannot_be_invented_or_misused_and_injection_changes_nothing`, scenario 3 |
| CSRF | a cross-site form post | `SameSite=Lax` plus a per-session `X-CSRF-Token` header compared in constant time | `test_csrf_required_for_mutations` |

## 6. Controls by area

### 6.1 Accounts, sign-up and sessions

| Surface | Threat | Control | Evidence |
|---|---|---|---|
| Sign-up | account farming, spam | Accounts created per network (10 / hour) and globally (100 / hour); attempts per network (60 / hour) and per email (5 / hour); form errors rejected before any limit is counted; passwords of 12+ characters, not on a common-password list | `test_registration_rate_limit_per_network`, `test_signup_reports_every_problem_inline_and_does_not_burn_rate_limits` |
| Sign-up input | malformed or hostile values | Server-side validation of every field (email format, username pattern and reserved names, control characters stripped, length limits); unique usernames enforced by the database; username check rate-limited (120 / hour per network) | `test_email_format_accepts_common_providers_and_rejects_malformed`, `test_username_is_unique_case_insensitive_validated_and_checkable` |
| Email codes (sign-up, password reset, password change) | guessing, replay, enumeration | 6 random digits stored as an HMAC with a per-code salt (a stolen database alone cannot be brute-forced); purpose-bound; single use; 10-minute expiry; older codes cancelled on resend; 5 wrong tries per email per network in 10 minutes and 10 per code; 20 wrong tries per email per hour and 60 per day; resend once a minute and 5 times an hour; identical answers for unknown accounts, wrong codes and wrong passwords | `test_email_codes.py`, `test_accounts.py` |
| Password reset | account takeover | Reset requires the emailed code and a new valid password; every session is revoked after a reset; other sessions are revoked after a password change; a notice email follows any password change | `test_reset_flow_generic_single_use_expiring_and_revokes_sessions`, `test_reset_answers_are_the_same_for_unknown_addresses` |
| Account deletion | deleting someone else's data | Own account only; password re-entry and typed confirmation; refused for shared demo accounts; sessions revoked; personal data removed; append-only financial history archived, not rewritten | `test_delete_account_*`, `test_designated_and_fixture_accounts_cannot_be_deleted` |
| Workspaces | entering another tenant; samples touching real data | Switch only to active memberships (404 otherwise); a sample is a separate private business; demo reset refuses real workspaces and archives instead of deleting | `test_cannot_switch_into_another_tenant`, `test_sample_workspace_is_private_*`, `test_demo_reset_archives_not_deletes` |
| Records | mass assignment, cross-tenant ids | Explicit request models (no ORM binding); every id checked against the session's business; amounts parsed as exact decimals; edits post adjustments and revoke approvals bound to them | `test_records_are_tenant_isolated_*`, `test_edits_adjust_*` |

### 6.2 AI assistant

| Threat | Control | Evidence |
|---|---|---|
| Prompt injection | 18 tools with strict schemas (`extra="forbid"`, so the model cannot pass tenant or user ids); unknown tools refused; no tool can pay, approve, send, change policy or recipients, delete, fetch URLs or run SQL; document text returned only as untrusted text; every tool call audited | `test_tools_cannot_be_invented_or_misused_and_injection_changes_nothing` |
| Excessive agency | Suggestions only (see §4); Confirm re-validates against current records and the user's role (recording cash is owner-only); a double confirm runs once; another user's suggestion returns 404 | `test_live_suggestion_does_nothing_until_confirmed_then_is_verified`, `test_payment_request_suggestion_still_needs_owner_approval`, `test_staff_cannot_get_cash_suggestions_and_injection_cannot_act` |
| Unsafe advice | Option classes (feasible / conditional / prohibited by your rules) are decided by code, not the model; a rule guard checks every live answer and relabels any line that recommends lowering the reserve or borrowing against the owner's rules; flags are audited | `test_options.py`, `test_constraints.py` |
| Wrong-tenant context | The records snapshot is built by server code from the session's business only | `test_snapshot_never_crosses_tenants` |
| Key exposure | AI keys stay on the server, sent only in request headers to a fixed provider origin; never in URLs, logs, responses or the browser | `test_gemini_answers_from_the_users_records` |
| Cost abuse, runaway loops | Per-user daily cap (200) reserved under a row lock; 30 messages a minute; at most 3 tool rounds and a 45-second budget per answer | `test_tool_round_budget_stops_the_loop`, `test_daily_ai_limit_gives_quick_answers_instead_of_blocking` |
| Fallback mistaken for AI | When the AI is unavailable, FlowSense's own calculations answer, labelled "Quick answer (from your records)" with the reason — never shown as AI | `test_gemini_failures_quick_answer_or_report`, `test_quota_refusal_switches_to_the_next_model_and_remembers_it` |
| Cross-user chat leakage | Chats stored per user and business on the server, never in shared browser storage; 30-day retention; user can delete | `test_history_is_per_user_and_workspace_and_clearable` |

### 6.3 Document uploads and reading

| Threat | Control | Evidence |
|---|---|---|
| Malicious or disguised files | Type decided from the file's bytes, never its name; only PDF, PNG, JPEG, DOCX and XLSX; 5 MB per file, 10 files and 20 MB per batch | `test_documents.py` |
| Parser exploits and bombs | Image dimensions checked before decoding (40 MP); zip member size and ratio limits; DOCX/XLSX parsed with `defusedxml`; every parser runs in a separate memory-limited (512 MB) process with a time limit | `test_documents.py` |
| Huge scanned pages | Pages rendered by Poppler with fixed arguments (no shell), scaled to at most 2,200 px, under memory (768 MB) and file-size (40 MB) limits and a 30-second timeout; oversized images skipped | `test_a_huge_page_size_cannot_make_a_huge_image` |
| Slow documents | PDF text extraction limited to 20 seconds; a document that takes longer is read by OCR instead, within one 35-second budget for its first 10 pages | `test_slow_text_layer_falls_back_to_text_recognition` |
| Pathological text (regex denial of service) | Field rules use bounded, linear-time checks; the label-continuation check splits text instead of using a nested pattern and caps line length | `test_licence_with_slash_labels_and_combined_dates` |
| Stored XSS, cross-tenant access | Files stored per workspace and served only as downloads with the detected type and `nosniff`; other workspaces get 404; business-licence batches are owner-only | `test_business_batches_are_owner_only_and_purposes_cannot_be_mixed` |
| Prompt injection via documents | Document text is untrusted data; instruction-like wording is flagged; the AI reader has no tools and its output is validated; nothing is saved without the user's review; uploads never create cash | `test_documents.py` |

### 6.4 Platform

- **Security headers:** the API sends `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`,
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and
  `Cache-Control: no-store`. The web app sends a CSP (`default-src 'self'`, `frame-ancestors 'none'`,
  `object-src 'none'`), `nosniff`, `DENY`, `no-referrer` and a Permissions-Policy (camera, microphone and location
  off); the hosting platform adds HSTS with preload. All were confirmed on the live site.
- **Same-origin routing:** the browser talks only to the web app, which proxies `/api`; no CORS is enabled.
- **No unsafe rendering:** documents and AI text render as React text nodes; the assistant renderer interprets only
  bold text and bullet lines, and only internal links become buttons.
- **Secrets:** kept in `.env` (never committed) or the hosting dashboards; generated randomly by `setup.ps1`; audit
  payloads redact keys containing password, token, secret, csrf, cookie or api_key.
- **Production guards** (`config.py`): startup fails if demo controls are on, cookies are not `Secure`, a demo
  password is set, the event secret is under 32 characters, or live AI lacks a key and model; API docs are disabled.
- **Client IP for rate limits:** only the right-most proxy hop is trusted, or the web proxy's header with a shared
  secret, so `X-Forwarded-For` cannot be spoofed (`test_client_ip_uses_secret_header_or_rightmost_hop`).
- **Exchange rates:** fixed provider URLs, no redirects, size and time limits, exact decimal parsing; failures never
  produce invented rates; rates never change stored records (`test_fx.py`).
- **Infrastructure:** local ports bind to 127.0.0.1 only; containers run as non-root users with memory limits.
- **Local development exception:** `FLOWSENSE_COOKIE_SECURE=false` is allowed only for `http://localhost`; demo
  controls work in development and are refused in production.

## 7. Recent security changes (18–20 October 2026)

| Change | Security effect |
|---|---|
| Rule guard on AI answers | Advice can no longer present cutting the reserve or disallowed borrowing as a compliant option; flags are audited |
| Blocked approvals have no approve button | The interface no longer invites a bypass attempt; the API still refuses with `409 RESERVE_BREACH` |
| Duplicate payment-request guard covers partly settled requests | Prevents a second open request on an invoice while money is still arriving |
| Bounded scanned-PDF rendering | A crafted PDF with a huge page size can no longer produce a huge image (found by automated review, fixed and tested) |
| Slow-PDF fallback | A PDF whose text takes too long is read by OCR within a fixed budget instead of failing or running unbounded |
| Linear-time label check in the document reader | A possible regular-expression backtracking issue (found by automated review) replaced by a split-based check with a length cap |
| Database `connect_timeout=10` | Bounds resource use when the database is slow |

## 8. Known risks and residual issues

| # | Risk | Severity (self-rated) | Mitigation / status |
|---|---|---|---|
| R1 | A database superuser can rewrite the whole audit chain and its head consistently | Medium | The verify endpoint detects edits, gaps and truncation, but not a consistent full rewrite. Mitigation: export the head hash to an independent location; automated checkpoint export is future work |
| R2 | The app connects as the database owner role, not a least-privilege role | Medium | Triggers still block `UPDATE`/`DELETE` on immutable tables for that role; a restricted runtime role is future work |
| R3 | No multi-factor authentication | Medium (for a real deployment) | Out of scope for the sandbox; planned with real payment rails |
| R4 | Anyone who knows an email address can use up its code-guessing limits and delay the owner | Low | Accepted trade-off: higher limits would raise guessing odds. The account cannot be taken over, only delayed; a CAPTCHA or device-bound codes would reduce this |
| R5 | No CAPTCHA; a distributed attacker could use up the global sign-up cap | Low | The cap stops runaway account creation; it never blocks sign-in for existing users |
| R6 | Without email delivery configured, sign-up reveals that an address already has an account | Low | Rate-limited; configuring an email provider switches to the enumeration-safe flow |
| R7 | The web CSP allows inline scripts (`'unsafe-inline'`) for the Next.js bootstrap | Low | No user or AI content is rendered as HTML; a nonce-based CSP is future work |
| R8 | Free AI quotas are shared by all users | Low | When exhausted, users get labelled quick answers from FlowSense's own calculations; a paid key removes the limit |
| R9 | No malware scanning of uploads | Low | Files are type-checked by content, size-limited, parsed in isolated limited processes, never executed, and served only as downloads |
| R10 | Document reading can misread a value | Low | Values are suggestions with their source line; nothing is saved until the user checks and confirms |
| R11 | The sandbox bank shares the database (`sim_bank_ops`) | Info | Simulation convenience; FlowSense never trusts it directly — only signed events change state |
| R12 | The instruction-like text detector is a heuristic | Info | A labelling aid only; safety does not depend on it, because no tool can act on document text |
| R13 | Free hosting sleeps when idle | Info (availability) | The first request after a quiet period can take up to a minute; the app shows a "waking up" screen and retries |

## 9. Verification evidence

| Check | Result (19–20 October 2026) |
|---|---|
| Backend tests (pytest, real PostgreSQL) | 223 passed |
| Browser journeys (Playwright) | 38 passed |
| Scripted scenarios over HTTP (`scripts/run_scenarios.py`) | 10 / 10 passed: full rescue, partial payment, prompt injection, bank details changed after approval, reserve breach, duplicate payroll (concurrent and double-click), bank timeout with API restart, staff / cross-tenant / missing CSRF, customer refusal, rescue in USD |
| Static checks | ruff, mypy, eslint and TypeScript: no issues |
| Dependency scanning | `pip-audit`: no known vulnerabilities; `npm audit --omit=dev` (runtime): 0 vulnerabilities |
| Live site | Double-click on "pay" paid exactly once; three simultaneous submissions produced one payment; the audit chain verified as valid |
| Continuous integration | All of the above run on every push (GitHub Actions) with throwaway secrets |

## 10. Limits of this assessment

This report covers the controls and test cases listed above. It is not a claim of complete security: prompt-injection
tests demonstrate specific cases, not universal immunity, and no independent penetration test has been performed.
Before handling real money, FlowSense would need an independent security review, multi-factor authentication, a
least-privilege database role, external audit checkpoints and a licensed payment provider.

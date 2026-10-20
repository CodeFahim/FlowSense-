# FlowSense

[![CI](https://github.com/CodeFahim/FlowSense-/actions/workflows/ci.yml/badge.svg)](https://github.com/CodeFahim/FlowSense-/actions/workflows/ci.yml)

FlowSense is a cash-flow copilot for small businesses. It calculates the cash gap before a payment deadline such as
payroll, compares the ways to close it against the owner's own rules, and executes payments only after the owner
approves each one. An AI assistant answers questions from the business's records and suggests actions, but cannot move
money. FlowSense runs in sandbox mode: the bank and customers are simulated and all data is synthetic.

- **Live sandbox:** https://flowsense-rescue.vercel.app — create an account, then on the welcome screen choose
  **Try the demo → Open sample** for a private sample business. The first visit after an idle period can take up to a
  minute to load.
- **Documentation:** [Technical documentation](TECHNICAL_DOC.md) ·
  [Security self-assessment report](SECURITY_SELF_ASSESSMENT_REPORT.md)

## How it works

```mermaid
flowchart LR
    O([Owner]) --> UI[Next.js web app]
    UI -->|same-origin /api| API[FastAPI]
    API --> AI[AI assistant<br/>read-only tools,<br/>suggestions only]
    API --> P[Rescue planner<br/>and forecast]
    API --> K{{Safety kernel}}
    AI -. suggestion .-> O
    O -->|approves one exact payment| K
    K -->|allow| X[Pending transfer<br/>exactly once]
    K -->|deny + reasons| O
    X --> B[(Sandbox bank)]
    B -->|HMAC-signed settlement| R[Reconciliation]
    R --> L[(Double-entry ledger,<br/>receipts, audit chain)]
```

1. **Gap calculation.** The funding gap is calculated from settled cash only:
   payments due + protected reserve − settled cash. Promises, accepted requests and unpaid invoices are not counted.
2. **Planning.** The planner builds the possible components (collecting part of an invoice, a financing quote, a
   supplier extension), rejects those that break the owner's policy (for example a loan above the cost cap) or don't
   arrive in time, and recommends the cheapest combination that covers the gap.
3. **Approval.** Every money action goes through `domain/actions.py` → `safety/kernel.py` at propose, approve and
   execute. An approval is bound to the exact amount, recipient version and policy version, is single-use and expires
   after 30 minutes.
4. **Execution and settlement.** Execution is exactly-once (idempotency keys, a per-business lock, one-time approval
   consumption). Cash changes only when an HMAC-signed settlement event arrives; then receipts are issued.
5. **Record keeping.** The ledger, receipts and audit log are append-only, enforced by database triggers; the audit
   log is hash-chained and can be verified on demand.

**AI assistant.** The assistant works from a snapshot of the business's records, so the user doesn't type figures.
Its tools only read, assess, draft or *suggest*; a suggestion runs only after the user confirms it, and a suggested
payment request still needs owner approval. Options in its answers (feasible, conditional, prohibited by the owner's
rules) are classified by code, not by the model, and a rule guard checks every answer against the owner's limits.

**Security.** Tenant isolation (another business's data returns 404), CSRF protection, Argon2id passwords, email
codes for sign-up and password reset, rate limits, and documents treated as data, never as instructions. Details are
in the [security self-assessment report](SECURITY_SELF_ASSESSMENT_REPORT.md).

## Features

- **Dashboard** — current situation in one sentence, settled cash, essential payments, money owed and the shortage.
- **Rescue plan** — options compared against the owner's rules, with evidence and reasons; replans after a partial
  payment or a customer's refusal.
- **AI assistant** — available on every screen; live AI (Google Gemini or Anthropic) with labelled quick answers from
  FlowSense's own calculations as a fallback.
- **Approvals** — exact-amount confirmation, single-use approvals, simulated sending and settlement.
- **Activity and receipts** — full timeline, a receipt for every settled payment, one-click integrity check.
- **Money in and out** — invoices with evidence, bills and payroll, customers, cash history.
- **Forecasts** — 7 to 60-day cash forecast with what-if scenarios, and a 12-month sales forecast.
- **Document reader** — reads PDFs, scans and photos (OCR), Word and Excel files and fills in forms for the user to
  confirm.
- **Currencies** — 155 ISO 4217 currencies with correct decimals; exchange rates for display and conversion.
- **Accounts** — sign-up with email codes, private workspaces, owner and staff roles, resettable sample scenarios.

## Screenshots

| Dashboard | Rescue plan |
|---|---|
| ![Dashboard](docs/images/dashboard.png) | ![Rescue plan](docs/images/rescue-plan.png) |
| **Blocked payment** | **AI assistant** |
| ![Payroll payment blocked by the safety kernel](docs/images/says-no.png) | ![AI assistant answer with option cards](docs/images/ai-assistant.png) |

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 16, React 19, TypeScript, Tailwind CSS 4, Recharts |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic 2 |
| Database | PostgreSQL 16 (immutability and balance enforced by triggers) |
| AI | Google Gemini or Anthropic; offline demo assistant for local runs |
| Documents | Tesseract OCR, Poppler, pypdf — parsed in memory- and time-limited processes |
| Testing and CI | pytest, Playwright, ruff, mypy, eslint, tsc, pip-audit, npm audit — GitHub Actions |
| Hosting | Vercel (web) and Render (API and database) |

## Run it locally

**Prerequisites:** Docker Desktop (or Docker Engine with Compose v2), about 2 GB of free memory, and ports 3000,
8000 and 5432 free. PostgreSQL runs inside Docker; nothing else is required.

**Windows (PowerShell, in the project folder):**

```powershell
Set-ExecutionPolicy -Scope Process Bypass   # allow the project's scripts in this window
.\scripts\setup.ps1                         # creates .env with random local secrets (never overwrites one)
.\scripts\start.ps1                         # builds and starts database, API and web (first run: a few minutes)
.\scripts\demo-credentials.ps1              # prints the demo sign-in details
```

**macOS / Linux:**

```bash
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD, FLOWSENSE_SANDBOX_EVENT_SECRET (32+ characters),
# FLOWSENSE_DEMO_PASSWORD and FLOWSENSE_FRESH_ACCOUNT_PASSWORD (12+ characters each) to your own random values.
docker compose up -d --build --wait
```

The API runs the database migrations and seeds the synthetic demo data automatically. Then open
**http://localhost:3000** (API health: http://localhost:8000/api/health).

### First steps

1. Sign in as **demo@flowsense.example** with `FLOWSENSE_DEMO_PASSWORD` from `.env`
   (`fresh@flowsense.example` starts an empty workspace).
2. **Dashboard:** 50,000 in the bank, payroll 80,000 due Friday, reserve 10,000 → 40,000 short.
3. **Ask AI:** "What are my options to cover payroll?"
4. **Rescue plan:** the recommended collection and the rejected 39.70% loan.
5. **Approvals:** approving payroll first is blocked. Approve and send the 40,000 request, then use the sandbox
   buttons **Customer accepts** (cash unchanged) and **Customer pays**. Now approve and submit payroll.
6. **Activity and receipts:** two receipts and **Check record integrity**.
7. To start again: **Demo scenarios → Standard rescue → Reset** (a reset archives data; nothing is deleted).

### Configuration

All settings are documented in [`.env.example`](.env.example). Locally, the assistant runs offline
(`FLOWSENSE_AI_MODE=demo`); set `FLOWSENSE_AI_MODE=live` with your own Anthropic or Gemini key for the live AI.
Sign-up emails need an email provider (`FLOWSENSE_EMAIL_PROVIDER`). Never commit `.env`.

Stop the stack with `.\scripts\stop.ps1` or `docker compose down`.

## Tests

With the stack running (Windows):

```powershell
.\scripts\test.ps1       # backend tests (pytest) inside the API container, separate test database
.\scripts\evidence.ps1   # 10 scripted scenarios over HTTP; writes evidence/
.\scripts\e2e.ps1        # browser tests; first run: cd apps\web; npm ci; npx playwright install chromium
.\scripts\verify.ps1     # everything: ruff, mypy, pytest, pip-audit, eslint, tsc, build, npm audit, e2e, evidence
```

On macOS/Linux, run the backend tests with `docker compose exec -T api python -m pytest -q`. The same checks run in
[GitHub Actions](.github/workflows/ci.yml) on every push: 223 backend tests on PostgreSQL, 38 browser tests and the
10 scripted scenarios, with throwaway secrets (no repository secrets needed).

## Repository layout

| Path | Contents |
|---|---|
| `services/api/app` | API: `safety/` kernel, `domain/` actions and position, `planning/` planner and forecast, `reconciliation/`, `ledger/`, `audit/`, `agents/` (AI), `documents/`, `fx/` |
| `services/api/alembic` | Database migrations |
| `services/api/tests` | Backend tests |
| `apps/web/src` | Web app (Next.js App Router) |
| `apps/web/e2e` | Browser tests (Playwright) |
| `scripts` | Setup, start, stop, test and verification helpers |
| `samples` | Synthetic documents used by the tests |
| `docs/images` | Screenshots used in this README |
| `compose.yaml`, `render.yaml` | Local Docker stack; hosted deployment (Render and Vercel) |

## Team

**Team Decimal Point** — American International University-Bangladesh (AIUB)

| Name | Contact |
|---|---|
| Tahsin Ahmed Fahim | 23-52735-2@student.aiub.edu |
| Sheikh Akid Bari Rafee | 23-54440-3@student.aiub.edu |
| Faysal Mahmud | 23-54032-3@student.aiub.edu |
| Nahian Islam Rishad | 23-54466-3@student.aiub.edu |

*The sample business, people and documents are fictional.*

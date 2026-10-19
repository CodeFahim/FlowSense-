# FlowSense — AI cash-flow rescue copilot (sandbox)

FlowSense helps a small-business owner get through a cash crunch, for example *payroll is due Friday and the money
isn't there*. It:

- finds the exact funding gap from **settled cash only** (promises and unpaid invoices don't count);
- compares the ways out (collect an invoice, a loan, delay a supplier) against the owner's own rules and recommends
  the cheapest safe one;
- lets an **AI assistant** answer questions from the business's records, with every option labelled by code
  (feasible, conditional, prohibited by your rules);
- moves money **only after the owner approves one exact payment**, re-checked by a safety engine, paid exactly once,
  with receipts and a tamper-evident audit trail;
- reads documents (PDF, scans, photos, Word, Excel) to fill in forms for the owner to confirm.

Built for the 2026 Shenzhen International FinTechathon (International Track, Topic E: SME Finance Copilot).
Code name in the repository: *Lifeline*.

## What's inside

| Folder | Contents |
|---|---|
| `services/api` | Backend: Python 3.12, FastAPI, SQLAlchemy, Alembic migrations (`alembic/versions`), tests (`tests/`) |
| `apps/web` | Frontend: Next.js 16, React 19, Tailwind CSS; browser tests (`e2e/`, Playwright) |
| `infra/db-init` | Creates the test database on first start |
| `scripts` | PowerShell helpers to set up, start, stop, test and verify |
| `samples` | Synthetic documents used by the browser tests |
| `compose.yaml` | Runs PostgreSQL 16 + API + web in Docker |
| `render.yaml`, `apps/web/vercel.json` | Optional hosted deployment (Render + Vercel) |

## Prerequisites

- **Docker Desktop** (Windows/macOS) or Docker Engine with Compose v2 (Linux), running. This is all you need to run
  the app; PostgreSQL runs inside Docker.
- About 2 GB of free memory and ports **3000, 8000 and 5432** free on `127.0.0.1`.
- Optional, for the browser tests only: **Node.js 24**.
- Optional: an AI key (Anthropic or Google Gemini) for the live assistant, and an email service (Resend, Brevo or
  SMTP) for sign-up codes. Without them the app still runs (see Limitations).

## Install and run

### Windows (PowerShell, from the project folder)

```powershell
Set-ExecutionPolicy -Scope Process Bypass   # allow the project's scripts in this window
.\scripts\setup.ps1                         # creates .env with random local secrets (never overwrites one)
.\scripts\start.ps1                         # builds and starts database, API and web; first run takes a few minutes
.\scripts\demo-credentials.ps1              # prints the demo sign-in details
```

### macOS / Linux

```bash
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD, LIFELINE_SANDBOX_EVENT_SECRET (32+ characters),
# LIFELINE_DEMO_PASSWORD and LIFELINE_FRESH_ACCOUNT_PASSWORD (12+ characters each) to your own random values.
docker compose up -d --build --wait
```

**Database setup is automatic:** when the API starts it runs the migrations (`alembic upgrade head`) and seeds the
synthetic demo data if it is missing. Data is kept in the Docker volume `lifeline_pgdata`.

Stop: `.\scripts\stop.ps1` or `docker compose down`. Check health: `.\scripts\health.ps1`.

## Open it

- App: **http://localhost:3000**
- API health: http://localhost:8000/api/health · API docs (development only): http://localhost:8000/api/docs

## First use (about 5 minutes)

1. Sign in as **demo@lifeline.example** with the `LIFELINE_DEMO_PASSWORD` value from `.env`.
   Other synthetic accounts: `demo-staff@lifeline.example` (staff role), `owner@harbortea.example` (another
   business), and `fresh@lifeline.example` (empty workspace, password `LIFELINE_FRESH_ACCOUNT_PASSWORD`).
2. **Dashboard:** one sentence states the gap — payroll 80,000 due Friday, 50,000 in the bank, a 10,000 reserve to
   protect, a customer owing 60,000 → **40,000 short**.
3. **Ask AI** (top right): e.g. "What are my options to cover payroll?"
4. **Rescue plan:** the recommended fix (collect 40,000 of the overdue invoice) and the rejected loan (39.70% a year
   against an 8% limit).
5. **Approvals:** try to approve payroll first — it is blocked. Approve and send the 40,000 payment request, then use
   the sandbox buttons **Customer accepts** (no cash yet) and **Customer pays**. Now approve and submit payroll.
6. **Activity & Receipts:** receipts for both payments and **Check record integrity**.
7. To start again: **Demo scenarios** → *Standard rescue* → **Reset to this scenario** (type `RESET`). Resets
   archive the old data; nothing is deleted.

## Configuration (`.env`)

All settings are documented in `.env.example`. The important ones:

| Setting | Default | Meaning |
|---|---|---|
| `LIFELINE_AI_MODE` | `demo` | `demo` = offline assistant (no key, no network); `live` = real AI |
| `LIFELINE_AI_PROVIDER` + key + model | Anthropic | For live mode: `LIFELINE_ANTHROPIC_API_KEY` / `_MODEL`, or `LIFELINE_AI_PROVIDER=gemini` with `LIFELINE_GEMINI_API_KEY` / `_MODEL` |
| `LIFELINE_EMAIL_PROVIDER` | `off` | `resend`, `brevo` or `smtp` to send sign-up and password codes |
| `LIFELINE_FX_PROVIDER` | `erapi` | Exchange rates for display and the converter (needs internet); `off` disables them |
| `LIFELINE_DEMO_CONTROLS_ENABLED` | `true` | Demo reset and the sandbox customer simulator; refused in production |

Never commit `.env`. After changing it: `docker compose up -d --build --wait`.

## Tests and checks

With the stack running (Windows PowerShell):

```powershell
.\scripts\test.ps1       # backend tests (pytest) inside the API container, against a separate test database
.\scripts\evidence.ps1   # 10 scripted safety scenarios over HTTP; writes evidence/
.\scripts\e2e.ps1        # browser tests (Playwright); first run: cd apps\web; npm ci; npx playwright install chromium
.\scripts\verify.ps1     # everything: ruff, mypy, pytest, pip-audit, eslint, tsc, build, npm audit, e2e, evidence
```

On macOS/Linux the backend tests run with `docker compose exec -T api python -m pytest -q`.

## Limitations (please read)

- **Sandbox only.** There is no real bank, money or customer. Payments, bank settlement and customer replies are
  **simulated** (signed sandbox events; the "Customer accepts / pays / refuses" buttons). All demo data is synthetic.
- **AI:** by default the assistant is an **offline demo assistant** with prepared answers. Live AI needs your own API
  key. The AI can only read, explain and suggest; it can never approve or move money.
- **Email:** off by default, so new sign-ups can't receive their 6-digit code; use the seeded accounts or configure an
  email service.
- **Exchange rates** are daily reference rates for display and conversion only; they never change stored records.
- **Document reading:** OCR (Tesseract) runs inside the API container; long scans are read up to their first 10
  pages. Extracted values are suggestions the user must confirm.
- Planning is designed for small-business volumes and one accounting currency per business.

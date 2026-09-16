---
title: Payment Reminder Followup
emoji: 📬
colorFrom: gray
colorTo: blue
sdk: docker
app_port: 8501
pinned: false
short_description: Review and approve invoice payment reminders
---

# Payment follow-up

This page documents the preserved single-business Gmail workflow. Run
`legacy_app.py` for this interface. The default `app.py` now opens the authenticated
workspace described in the root README. The databases and cooldown histories are
separate; review earlier sends before moving live work between them.

Import Excel/CSV invoices, calculate outstanding balances, review each reminder,
and submit approved emails through Gmail. Both the Streamlit browser interface
and command-line interface use the same validation, approval and history service.
AI wording is optional; the app works with a built-in template without an AI key.

## Current implementation

This is the first reliability implementation following the project review:

- Strict import validation with row-level errors, explicit due dates, recognized
  statuses, valid single recipients and duplicate invoice detection.
- Decimal money calculations: `outstanding = Amount - Amount Paid`.
- SQLite persistence of imported invoices, exact reviewed messages, approvals,
  sending attempts and manual reconciliation events.
- Single-use approvals bound to the invoice snapshot, recipient, subject, body,
  sender and mode. Approvals expire after 30 minutes.
- A seven-day default cooldown per business and invoice, including across restarts
  and concurrent sends. Recent/uncertain attempts are excluded before drafting.
- Distinct `previewed`, `submitted`, `failed`, `unknown` and `blocked` outcomes.
- Separate settings per browser session, business-scoped queries/exports, and
  private temporary storage for each hosted demo session.
- Hosted demos force preview mode on the backend, regardless of the UI toggle.
- Complete, formula-safe CSV history exports. The previous CSV log is no longer
  used as the source of truth.

**Deployment boundary:** local mode is for one trusted business on a trusted
computer and binds to `127.0.0.1`. Business IDs scope records but are **not user
authentication**. Do not expose local mode publicly or advertise this release as
a production multi-business service. Hosted mode is a preview-only demo. User
accounts, roles, authorization, encrypted secret storage, retention controls and
production operations remain required for a shared live service.

## Local setup

Requires Python 3.12. From the project directory on Windows:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m streamlit run legacy_app.py
```

On macOS/Linux, use `.venv/bin/python` in place of `.venv\Scripts\python.exe`.
The direct dependency versions in `requirements.txt` are pinned; this is not a
full transitive dependency lockfile.

Start with dry run enabled. Download the sample workbook in the app, upload it,
and select **Find overdue invoices**. Select the checkbox on each reviewed
message, then choose **Approve selected and preview**. The sample has four
overdue invoices totaling INR 153,000 after partial receipts are subtracted.

Use **Business and email settings** to enter your business name and Gmail App
Password. Gmail connection testing authenticates only; it does not send mail.
The app can save settings in `.env` on this computer, or retain them only for the
current browser session. Start another batch preserves these session settings.
Protect `.env`, the computer account, and the `.data` directory. Saved settings
are plain text on disk; do not use this storage for a shared production service.
Restart the app after changing environment configuration externally.

For CLI configuration, copy `.env.example` to `.env` and supply your own values.
Never commit credentials, invoice files, exports or databases. These files are
covered by `.gitignore` and excluded from the Docker build context.

## Invoice format

The first worksheet of `.xlsx` files is read. CSV files are also accepted. Imports
are limited to 5 MB and 5,000 rows. An invalid row rejects the whole import, so
partially valid files cannot silently update balances.

| Column | Rule |
| --- | --- |
| Invoice No | Required, unique within the file, at most 100 characters. IDs are matched case-insensitively within a business. Keep the same ID when updating an invoice. |
| Client Name | Required, at most 200 characters. |
| Email | One address; required for unpaid/partially paid invoices. No recipient lists or display names. |
| Amount | Total invoice value, non-negative, at most two decimal places. Indian/international comma grouping is accepted. |
| Amount Paid | Optional receipts total. Required for partial payments. Cannot exceed Amount. |
| Due Date | Required. Excel date, `YYYY-MM-DD`, `DD/MM/YYYY`, or `DD-MM-YYYY`. Invoice Date is never substituted. |
| Status | Unpaid, Partially Paid, Paid, Cleared, Cancelled/Canceled, Disputed, or On Hold. Only the first two are eligible. |
| Currency | Optional three-letter code, default INR. Different currencies are displayed separately and never added together. |
| Notes | Optional. Not parsed as financial data and never sent to AI. Notes mentioning partial receipts require an explicit Amount Paid value. |

For Paid/Cleared rows, an omitted Amount Paid is treated as the full Amount. If
Amount Paid is explicitly provided, it must equal Amount. Zero outstanding
balances do not generate reminders. Currency conversion, credits, installments
and bank reconciliation are not implemented yet.

Every successful import upserts its invoices. Existing invoices absent from a
new file are retained; an omitted invoice is not assumed to be paid. Reimport
updated balances/statuses before sending. Changes invalidate outstanding
approvals. Changing an invoice's ID creates a new invoice and bypasses its old
history, so keep invoice IDs stable.

## Sending and recovery

The app approves only the selected messages, using the final edited subject and
body. The backend records a durable claim before SMTP submission. It checks
eligibility, changes since review, single-use approval, expiry, sender/mode and
duplicate protection again immediately before sending.

- **Previewed:** recorded locally; SMTP was never invoked. Previews do not block
  later live sending and their approvals cannot be reused for live mode.
- **Submitted:** Gmail accepted the submission. This does not prove inbox
  delivery, reading, or payment. Delivery webhooks are not implemented.
- **Failed:** a definite connection/authentication failure or provider rejection.
  Correct the issue and obtain a fresh approval before retrying.
- **Unknown:** the submission result or final history write is uncertain. Do not
  automatically retry: Gmail may already have accepted the message.
- **Blocked:** no send was initiated for this request; read the reason shown.

The UI's **Resolve an uncertain email attempt** section lets a trusted operator
record an outcome after checking Gmail Sent mail and active senders. It never
sends an email. A stuck in-progress claim must be at least one hour old before
it can be reconciled. Confirming “not sent” permits a fresh approval; confirming
“submitted” retains cooldown protection. Each resolution is audited. SMTP does
not offer exactly-once delivery; if the outcome cannot be verified, leave the
attempt blocked. Never delete history as a retry mechanism.

SQLite data lives at `.data/reminders.sqlite3` by default. To back it up, stop the
app and CLI so no sends are running, then copy the database to private backup
storage. Restore only while both are stopped. Old `reminders_log.csv` entries
are not automatically migrated and do not contribute to cooldown checks;
review recent sends before using an existing business's data in this release.

## Command-line use

```powershell
.venv\Scripts\python.exe setup_sample.py --output sample_invoices.xlsx
.venv\Scripts\python.exe agent.py --file sample_invoices.xlsx --dry-run
.venv\Scripts\python.exe agent.py --file invoices.xlsx --days 14 --no-dry-run
```

The CLI asks for approval on every eligible invoice. `--ai` opts into AI wording;
the deterministic template remains available on provider failure. The sample
generator refuses to overwrite an existing file.

The old model-directed send/log tools have been removed. `run_tool` and
`tool_schemas.py` expose only read/filter operations for compatibility. Sending
is available through the shared application service after human review, not as
an AI tool call.

## Configuration

| Variable | Default / meaning |
| --- | --- |
| `BUSINESS_ID` | `local-business`; stable workspace identity, not an authentication credential. |
| `BUSINESS_NAME` | `Your Company`; reminder sign-off. |
| `DRY_RUN` | `true`; only the explicit value `false` enables live mode. |
| `DEFAULT_OVERDUE_DAYS` | `7`; accepted range 1-365. |
| `REMINDER_COOLDOWN_DAYS` | `7`; accepted range 1-365. |
| `BUSINESS_TIMEZONE` | `Asia/Kolkata`; used for overdue calculations. Audit timestamps are UTC. |
| `DATABASE_PATH` | `.data/reminders.sqlite3` under the project by default. |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD` | Required only for live sending. |
| `GROQ_API_KEY`, `GROQ_MODEL` | Optional AI wording; default model `llama-3.3-70b-versatile`. |
| `APP_MODE` | `local` by default. `demo` forces previews. `SPACE_ID` also forces demo mode. |

## Hosted demo

The included Dockerfile runs the app on port 8501 as an unprivileged user with
`APP_MODE=demo`. Hugging Face metadata uses the Docker SDK. Each browser session
gets a temporary private database, discarded when its session resources are
released. Demo mode does not use host Gmail/Groq credentials, save user settings,
or expose a global history download. A public demonstration should use sample
data. Hosted persistence, authentication and live operation require further work.

## Verification

```powershell
.venv\Scripts\python.exe -m pytest
```

Tests use temporary files/databases, mocked SMTP and AI responses, and Streamlit's
AppTest interface. They cover import errors, partial balances, message approval,
concurrency, cooldown, recovery, business scoping, CSV safety, CLI mode handling,
UI settings and demo isolation. No real email or paid API calls are needed.
The included Docker deployment has not been validated against a live host.
The included GitHub Actions workflow runs the checks on Windows and Linux after
the project is committed and pushed to a GitHub repository.

## Code map and next work

| Module | Responsibility |
| --- | --- |
| `app.py` | Browser setup, import, review, results and reconciliation controls. |
| `agent.py` | Deterministic command-line orchestration. |
| `invoices.py` | Import validation, dates, decimal balances and eligibility. |
| `reminders.py` | SQLite persistence, approvals, send claims, SMTP, audit export and recovery. |
| `drafting.py` | Fixed invoice facts plus optional generic AI wording; no invoice data sent to the model. |
| `config.py` | Immutable sending settings and explicit local settings persistence. |
| `tools.py`, `tool_schemas.py` | Read-only legacy tools and terminal human review. |
| `setup_sample.py` | Safe sample workbook generation. |

Next milestones: authenticated business accounts and roles; secure credential
storage; automated scheduling and a durable background queue; payment/receipt
management; customer statements and aging reports; configurable templates and
languages; WhatsApp, payment links and accounting integrations. The original
review under `output/pdf/` describes the pre-implementation baseline.

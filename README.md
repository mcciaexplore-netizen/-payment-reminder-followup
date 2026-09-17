---
title: Payment Reminder Followup
emoji: 📬
colorFrom: gray
colorTo: blue
sdk: docker
app_port: 8501
pinned: false
short_description: Business collections, invoices and payment reminders
---

# Payment follow-up for small businesses

An authenticated business workspace for invoices, collections and payment
reminders. Import a spreadsheet, record contact preferences, track payments,
review a reminder or authorize a schedule, and see the result in the same ledger.
The default browser entry point is now the business workspace.

The app uses MCCIA's official logo and Candara-based typography. Asset sources
and font fallback behavior are documented in [Branding](docs/branding.md).

Optional [demo login buttons and sample data](docs/demo.md) provide a ready-to-use
workspace with fictional customers, invoices, payments and reminder previews.

## Implemented features

| Area | Available now |
| --- | --- |
| Accounts | First-owner setup, sign-in, salted password hashes, expiring sessions, persistent login throttling, password changes, one-time invitations, business switching, and owner/manager/collector/viewer permissions checked by backend services. |
| Invoice book | Excel/CSV imports, custom column mapping, atomic validation, persistent invoices, search, currency-separated balances, safe CSV exports, customer statements and assigned collectors. |
| Collections | Partial receipts, credits, idempotent payment references, audited reversals, disputes, holds, cancellations, customer promises, contact preferences and opt-outs. |
| Reminders | Exact-message approval, previews, email/SMS/WhatsApp gateway submission, cooldowns across channels, durable claims, concurrent-worker protection, delivery tracking, cancellation and uncertain-outcome reconciliation. |
| Schedules | Before/on/after-due stages, editable policies, business timezones, weekdays and quiet hours, review queues or explicitly authorized automatic processing, plus a separate persistent worker. Different stage policies/templates can provide progressively firmer follow-up. |
| Customization | Editable templates, English/Hindi built-ins, display name, contact email, terms, tone, import currency and INR UPI payment instructions. |
| Payments | Configurable link gateway, exact amount/currency matching, signed payment events, replay protection, receipt updates and payment-link reconciliation. |
| Accounting | Configurable invoice-fetch gateway, review before import, strict ledger conflict checks. |
| Customer access | Expiring/revocable private links, invoice balances, payment links, payment-promise submission and reminder opt-out. |
| Installments & reporting | Installment allocation, aging buckets, overdue/disputed/promised balances, receipt history, currency-separated cash-flow estimates with visible assumptions and audit exports. |
| Operations | Encrypted connector credentials, SQLite backups, business archives, worker activity indicators, regression tests and a Windows/Linux CI workflow. |

**Integration status:** the connector framework and its request/event handling are
implemented and tested with simulated HTTP providers. No vendor has been chosen.
A provider adapter implementing [the gateway contract](docs/connectors.md),
credentials and sandbox validation are still required for live WhatsApp, SMS,
email, payment and accounting operations. A vendor's ordinary API endpoint cannot
be pasted in and assumed to work. No live messages, payment requests or accounting
writes were used to verify this implementation.

## Run locally

Use Python 3.12. On Windows:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m streamlit run app.py --server.port 8502
```

Open `http://127.0.0.1:8502`. Create your first owner account with a password of at
least 12 characters. In **Invoices & customers**, download the sample workbook,
import it, select a customer and record their permitted channels. In **Reminders**,
prepare a draft, review its recipient/text, and process it in **Preview only** mode.
No provider account is needed for the ledger, reports or previews.

On Linux/macOS use `.venv/bin/python`. The direct dependency versions are pinned;
this is not a full transitive lockfile. The local environment is isolated from
global Python packages.

### Background schedules

Run this in a separate terminal or supervised service:

```powershell
.venv\Scripts\python.exe worker.py
```

The default poll interval is 30 seconds; `--once` performs one pass. A browser tab
does not need to stay open. The worker checks the database for active policies,
prepares the latest reached stage per invoice, and processes queued approvals.
Closing its terminal stops background processing. It is not automatically
installed as an operating-system startup service.

`worker_health.py` checks the separate worker's recent progress without changing
the database. Browser actions do not refresh this service check. The worker
handles shutdown signals between reminders and finishes its current submission.

### Customer access and webhooks

```powershell
.venv\Scripts\python.exe -m uvicorn webhooks:app --host 127.0.0.1 --port 8503 --no-access-log
```

This runs signed provider callbacks and private customer statement pages. Copy a
customer link from **Invoices & customers**. Localhost links work only on the
same machine; external customer access needs your own HTTPS deployment and
`PUBLIC_WEBHOOK_BASE_URL`. Access logging is disabled because private customer
tokens appear in URLs. Do not publish those URLs in logs or analytics.

### Configuration

For Vercel, configure [Neon PostgreSQL](docs/neon.md) (or [hosted libSQL](docs/hosted-sqlite.md)) using
`deploy/vercel.env.example`. Local SQLite files cannot persist on Vercel.
The Docker/server setup continues to use the existing local SQLite database.

Copy `.env.example` to `.env` and edit only the values you need. Restart services
after configuration changes. All three services must use the same database and
encryption key.

| Variable | Meaning |
| --- | --- |
| `WORKSPACE_DATABASE_PATH` | Defaults to `.data/workspace.sqlite3`. Shared by the authenticated browser, worker and webhook service. |
| `WORKSPACE_LIVE_ENABLED` | Defaults to false. Only `true` enables live **message** submissions. Policy/message approval and a configured connector are also required. |
| `CONNECTOR_ALLOWED_HOSTS` | Comma-separated gateway hostnames explicitly trusted by the server operator. Empty blocks gateway network operations. |
| `WORKSPACE_MASTER_KEY` | Optional Fernet encryption key supplied through a secret manager/environment. If absent, `.data/connector.key` is generated locally. |
| `PUBLIC_WEBHOOK_BASE_URL` | Default `http://127.0.0.1:8503`; replace with your HTTPS origin for shareable customer links. |
| `APP_MODE=demo` or `SPACE_ID` | Runs the isolated legacy preview demonstration; background workers and customer/webhook operations are disabled. |

The message switch does not block an explicitly requested accounting fetch or
payment-link creation. Those require a configured connector and a user action.
Connectors are configured by business owners under **Team & integrations**.

## Money and import rules

Required fields: Invoice No, Client Name, Email, Amount, Due Date and Status.
Optional fields: Amount Paid, Currency and Notes. Map other headers in the import
screen. Files are limited to 5 MB and 5,000 rows. A single invalid row rejects the
whole batch. Due dates accept ISO dates, day/month/year or Excel dates. Duplicate
invoice numbers are matched case-insensitively within a business.

Amounts use integer minor units/decimal parsing, with at most two decimal places.
There is no exchange-rate conversion, and currencies are never summed together.
Status is Unpaid, Partially Paid, Paid/Cleared, Cancelled, Disputed or On Hold.
Open invoices require an email address, even when another reminder channel is used.
The default import currency comes from business settings.
Payment dates, reversals, forecasts and customer promises use the business
timezone, regardless of the server's timezone.

Imported Amount Paid represents an opening **settled balance**. Later receipts and
credits reduce outstanding amounts. The displayed `amount_paid` is the settled
total, including credits; the receipts report distinguishes cash receipts, credits
and reversals. Reimported settlements must exactly match the current settled
total. This prevents a stale spreadsheet or accounting sync from erasing receipts
or counting them twice. Use receipt/reversal operations to resolve differences.
Rows missing from a new file are retained. Currency cannot change after import;
invoice totals cannot change while an installment plan exists.

An installment plan must sum to the original invoice total. Settlements allocate
to the oldest installments first. Reminders use the oldest remaining installment's
amount/date. Aging reports use contractual invoice due dates; the cash-flow
estimate uses installment dates where available. Credits reduce collections but
are not presented as cash received. Forecasts show whether a date came from a
promise, a median recorded payment delay, or a due date with no receipt history.
They are planning estimates, not payment guarantees.

## Sending, authorization and recovery

- Imported contacts start with reminders disabled until permission is recorded.
  Customer preferences are shared across invoices with the same email in a
  business. A portal opt-out stops all channels; channel callbacks remove that
  channel's permission.
- Changing a customer's email on an existing invoice clears the previous
  customer's phone, permissions and payment promise. Known contact preferences
  and opt-outs for the new email are retained.
- Manual approvals are single use and expire after 30 minutes. The queue binds
  the invoice snapshot, recipient, mode and edited content. An invoice/profile
  change invalidates queued work; review a fresh draft.
- Automatic processing requires an explicit policy authorization. Without it,
  scheduled messages wait for review. The worker rechecks the policy author's
  and approver's current roles, customer permission, payment state, promises,
  sending window and duplicate protection before submission.
- Cooldowns apply per invoice across channels. Concurrent processes cannot claim
  the same job twice. A restart never clears an uncertain claim. Missed stages
  are collapsed to the latest reached stage, avoiding a burst of backlogged
  reminders. Editing/pausing a policy cancels unsent work; a previously prepared
  stage is not automatically replayed. Use a fresh reviewed reminder if needed.
- `previewed` means no network submission; `submitted` means gateway acceptance.
  `delivered`, `read` and `bounced` require signed provider events. `failed` is a
  definite rejection; `unknown` or a stale `claimed` job requires reconciliation.
- Recovery requires a manager to check the provider and record evidence. A claimed
  job must be at least an hour old before release. There are no blind retries.
  Provider idempotency is part of the gateway contract; exactly-once delivery
  cannot be guaranteed across every external provider.
- Ledger checks occur immediately before submission is claimed. Payments or
  opt-outs received after an external request starts cannot recall that request.

## Backups and deployment boundaries

For the full app, use the [persistent-server deployment guide](docs/deployment.md).
`compose.yaml` runs the workspace and background worker with shared durable
storage; its optional public profile adds HTTPS. `Dockerfile.workspace` is the
full application image, while the original `Dockerfile` runs the legacy demo.

The app is suitable for local evaluation and a controlled pilot. Default services
bind to localhost. Before exposing it, initialize the owner account locally, use
HTTPS and a supervised process manager, configure private storage/backups,
restrict the gateway allowlist, and validate the chosen providers' sandbox flows.
The first-owner form is bootstrap setup, not a public invitation system. Shared
production hosting, MFA/SSO, password recovery email, managed monitoring and
provider-specific deployment have not been validated or included here. SQLite
assumes one host with durable storage; this is not a multi-region architecture.

Create a consistent database snapshot, without overwriting any previous backup:

```powershell
.venv\Scripts\python.exe manage_workspace.py --output .data\backups\workspace-backup.sqlite3
```

The command uses SQLite's backup API and checks integrity. Store the matching
connector encryption key separately and protect both. To restore, stop the UI,
worker and webhook services, preserve the current database, restore the chosen
backup and key, then reconcile any interrupted submissions before enabling live
work. A database backup alone cannot decrypt credentials without its key.
Business ZIP exports omit users, sessions, customer access tokens and connector
secrets; they are readable business archives, not full-server restore packages.
Encryption protects saved connector credentials, not the entire invoice database.

## Existing single-business email workflow

The original reviewed Gmail workflow remains available as `legacy_app.py` and
`agent.py`, with its own `.data/reminders.sqlite3` database. It is for a trusted
local operator and is **not** an authenticated route into the new workspace.
See [the legacy workflow documentation](docs/legacy-workflow.md).

Legacy invoice/history data is preserved. It is not automatically assigned to a
new account or silently merged with a business. Import a reviewed invoice export
into the new workspace and inspect recent legacy sends before activating new
live policies, because the two databases do not share cooldown history. The
original `Dockerfile` remains a preview-only demonstration.

## Verification and code map

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m pip check
```

Tests cover legacy workflows and the new services, account isolation, roles,
receipts, payment events, opt-outs, schedules, concurrency, quiet hours, account
revocation, customer access, backups and browser page flows. Network services are
simulated. The CI workflow runs on pushes and pull requests, including a Linux
container check for the persistent-server deployment.

| Files | Responsibility |
| --- | --- |
| `app.py`, `workspace_app.py`, `workspace_ui.py`, `app_pages/` | Authenticated browser entry, session context and eight product areas. |
| `accounts.py`, `workspace_store.py` | Authentication, membership authorization and additive SQLite schema. |
| `ledger.py`, `invoices.py` | Imports, validated money/date rules, receipts and invoice states. |
| `scheduling.py`, `worker.py` | Templates, policy planning, approvals, claims, submission and recovery. |
| `connectors.py`, `webhooks.py` | Encrypted gateway configuration, outbound contract, verified events and customer HTTP pages. |
| `asgi_app.py`, `Dockerfile.workspace`, `compose.yaml`, `deploy/` | Combined web entry point, full-app server image, persistent worker/storage and optional HTTPS. |
| `business_features.py`, `manage_workspace.py` | Branding, installments, forecasts, customer access, archives and backups. |
| `legacy_app.py`, `agent.py`, `reminders.py`, `drafting.py` | Preserved local Gmail workflow and optional AI wording. |

The two-page report in `output/pdf/` describes the **original pre-implementation
baseline**, not the current feature set.

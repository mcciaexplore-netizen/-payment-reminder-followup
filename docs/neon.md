# Deploy with Neon PostgreSQL

Neon stores the workspace outside Vercel's temporary filesystem. The app uses
PostgreSQL through `psycopg`; local SQLite remains available when no remote URL
is configured. Existing local records are not uploaded automatically.

## Configure

1. Create a dedicated Neon project and database (for example,
   `payment-reminder` / `payment_reminder`). Review the account's plan before
   provisioning. Select a region near the Vercel function region.
2. Open **Connect**, select the database, and enable **Connection pooling**.
   Copy the full `postgresql://` connection string, including the password,
   `sslmode=require` and `channel_binding=require` options.
3. Save it privately as `WORKSPACE_DATABASE_URL` in the app's Vercel environment.
   `DATABASE_URL` is also supported; an explicit `WORKSPACE_DATABASE_URL` takes
   precedence. Neon does not use `WORKSPACE_DATABASE_TOKEN`.
4. Set the remaining values in `deploy/vercel.env.example`:
   - `WORKSPACE_MASTER_KEY`: a permanent Fernet key for encrypted connector
     credentials. Back it up securely; reuse the old key if migrating records.
   - `WORKSPACE_SETUP_TOKEN`: an independent random string of at least 32
     characters, needed to create the first owner.
   - `CRON_SECRET`: a different random string of at least 32 characters.
   - `WORKSPACE_SCHEDULER_MODE=cron`, `APP_MODE=local` and the actual HTTPS
     origin in `PUBLIC_WEBHOOK_BASE_URL`.
   - Keep `WORKSPACE_LIVE_ENABLED=false` until providers and sending are tested.
5. Deploy the Neon-support branch/commit. Automatic Vercel Git deployments are
   currently disabled in `vercel.json`, so explicitly redeploy or deliberately
   enable them after configuring secrets. Use separate databases/branches and
   credentials for Preview and Production.
6. Open the site and use the private setup code to create its first owner.
   Normal email/password sign-in follows. Account creation is guarded even
   when two requests arrive at the same time.

For a local preview using a private `.env.neon` file:

```powershell
.\.venv\Scripts\python.exe -m uvicorn asgi_app:app --env-file .env.neon --host 127.0.0.1 --port 8502 --no-access-log
```

Do not paste the URL into chat, commands recorded in shell history, PRs or
screenshots. Store it in ignored environment files or hosting secret settings.
Never place a PostgreSQL URL in `WORKSPACE_DATABASE_PATH` (that setting is for
local SQLite files). No missing/invalid connection falls back to a local file.

[Neon connection guidance](https://neon.com/docs/connect/connect-intro) and
[Python guidance](https://neon.com/docs/guides/python).

## Transactions and reminders

The adapter uses bound parameters, PostgreSQL identities for generated audit
IDs and 64-bit integers for money in minor units. Shared queries use portable
`ON CONFLICT` updates. Failed writes and commits surface as errors with no
automatic retries; an uncertain reminder claim remains blocked from resend.

Each application transaction takes a database-scoped, transaction-level
advisory lock before reading records. This preserves the previous single-writer
behavior for owner setup, receipts, approvals and reminder claims, including
checks on empty tables. Locks end on commit/rollback and work with transaction
pooling. All writers must use this adapter. This intentionally serializes
application transactions; high-volume deployments should evolve to narrower
locks after revalidating their money and sending invariants.

Prepared-statement caching is disabled for pooled connections. Connection and
lock timeouts are 10 seconds; transaction statements have a 30-second timeout.
Remote connections require TLS (the explicit localhost-only test configuration
can disable it). Tests run against a real PostgreSQL 18 service in CI using
disposable schemas; no Neon credentials are stored in CI.

The existing authenticated `/api/reminders/tick` cron processes at most 100 jobs
per pass, once daily at 04:30 UTC (10:00 India time). The endpoint requires
`Authorization: Bearer <CRON_SECRET>` and scheduler mode `cron`. For frequent
reminders, use a suitable hosting plan or run `worker.py` continuously with the
same database URL/key and scheduler mode `service`.

## Existing data and backups

Keep a verified SQLite backup and its encryption key before any migration.
Neon is PostgreSQL, so uploading the SQLite file directly is not a migration.
Import with an explicit, reviewed data conversion, pause old writers during
cutover, preserve IDs and audit/job state, and reconcile balances and uncertain
deliveries before enabling sends. An empty Neon database starts a new workspace.

Use Neon's recovery tools and PostgreSQL exports for hosted backups. The local
SQLite backup command rejects remote URLs. Check the project's retention
settings and recovery procedure before storing real business records.

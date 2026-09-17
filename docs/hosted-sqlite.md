# Keep SQLite while using Vercel

Vercel does not provide the persistent local filesystem this app's SQLite file
needs. A `/tmp` database would be temporary and separate across instances.
[Vercel explains this storage limitation](https://vercel.com/kb/guide/is-sqlite-supported-in-vercel).

The app supports **SQLite Cloud** through its official `sqlitecloud` Python
driver, and **libSQL** (a SQLite fork) through the official `libsql` driver.
It connects directly to the remote database;
it never creates a replica, temporary payment database, or backup of your data
inside a function. The local and Docker paths still use Python's `sqlite3`.
See [SQLite Cloud's Python SDK](https://github.com/sqlitecloud/sqlitecloud-py).

## SQLite Cloud project

1. In the SQLite Cloud dashboard, select or create a project for payment
   follow-up. Review its plan before creating it; a second project may require
   a paid plan if the account's free-project allowance is already used.
2. Create a dedicated database named `payment_reminder`. Do not select the
   authentication database, sample database or another app's database.
3. Obtain the project's SQLite Cloud connection hostname and a server API key
   authorized for this database. The app needs to create its tables and indexes
   on first startup and read/write them afterwards. Keep the key server-side.
4. Split the connection string into these two Vercel environment variables:

   ```dotenv
   WORKSPACE_DATABASE_URL=sqlitecloud://YOUR-HOST.sqlite.cloud:8860/payment_reminder
   WORKSPACE_DATABASE_TOKEN=YOUR_API_KEY
   ```

   Replace the placeholders. A URL beginning `https://dashboard.sqlitecloud.io/`
   is the management website, not a database connection. If the provider's
   connection string ends in `?apikey=...`, put that key in the token variable
   and remove the query from the URL. Database names may contain letters,
   digits, underscores, dots and hyphens (up to 128 characters).

Connections use TLS with certificate verification and 10-second socket timeouts.
The adapter does not accept URL flags to disable TLS, create an in-memory
database or weaken consistency. It preserves the app's explicit transactions
and does not retry failed writes. It sends `COMMIT` and `ROLLBACK` explicitly
because SDK 0.0.84's helper methods can suppress operational errors. SQL batches
use `execute()` because this SDK does not implement `executescript()`.

## Configure the database and secrets

1. Prepare the SQLite Cloud database above, or use the libSQL alternative below.
2. In the Vercel project's settings, add the variables listed in
   `deploy/vercel.env.example`. Use separate databases and credentials for
   Preview and Production.
3. Set `WORKSPACE_MASTER_KEY` to a permanent Fernet encryption key. Keep a
   protected backup. If migrating existing data, reuse its `.data/connector.key`
   (or existing environment key); changing the key prevents decrypting saved
   integration credentials.
4. Set independent random `WORKSPACE_SETUP_TOKEN` and `CRON_SECRET` values,
   each at least 32 characters. Keep these in the server environment only.
5. Set `PUBLIC_WEBHOOK_BASE_URL` to the actual HTTPS website origin. A Windows
   file path in `WORKSPACE_DATABASE_PATH` cannot configure a Vercel database.
6. Redeploy a commit containing this feature. Automatic Vercel Git deployments
   remain disabled in `vercel.json`; explicitly deploy, or deliberately enable
   automatic deployments after setting the database and secrets.

The master key is required for remote storage so separate instances always use
the same encryption key. There is no fallback to a local file when a URL or token
is invalid or the hosted database is unavailable.

For a fresh database, open the site and enter the private setup code when
creating the first owner. Other visitors cannot claim the initial owner account
without that code. Once created, use the normal email/password login.

### libSQL alternative

An existing libSQL deployment remains supported. Use a libSQL-engine database
(not the newer Turso engine) with `WORKSPACE_DATABASE_URL=libsql://YOUR-HOST`
and its access token in `WORKSPACE_DATABASE_TOKEN`.
`TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` remain aliases for libSQL; a Turso
token is never used for a SQLite Cloud URL. Explicit workspace settings take
priority. See [the libSQL remote connection guide](https://docs.turso.tech/sdk/python/quickstart#remote-libsql-database-libsql).

## Background reminders

The configured Vercel cron calls `/api/reminders/tick` once a day at 04:30 UTC
(10:00 India time). Set `WORKSPACE_SCHEDULER_MODE=cron`. The endpoint requires
the `Authorization: Bearer <CRON_SECRET>` header and fails closed when the secret
is missing or short. Vercel supplies this header from the environment variable.
[Vercel cron authentication](https://vercel.com/docs/cron-jobs/manage-cron-jobs#securing-cron-jobs).

Each pass handles at most 100 reminders and stops claiming more after its time
budget. Remaining jobs stay queued for the next pass. Existing approvals,
consent, sending hours and live-message settings still apply. Unknown or
interrupted submissions stay blocked from automatic resend. Live sending stays
disabled until providers are deliberately configured and verified.

Cron is a daily pilot schedule, not the continuous worker. Adjust it to overlap
your businesses' sending hours. Vercel Hobby limits frequency to daily and does
not guarantee the precise minute; more frequent schedules depend on the plan.
[Cron limits](https://vercel.com/docs/cron-jobs/usage-and-pricing).
For larger queues or frequent follow-up, run `worker.py` separately with the
same database URL, token and master key, and use scheduler mode `service`.
The dashboard checks the relevant cron or service heartbeat separately.

## Existing data and validation

No existing data is uploaded or migrated automatically. Back up the current
SQLite database with `manage_workspace.py`, then follow the database host's
SQLite import procedure. Migrate the matching encryption key as well. Keep the
old deployment paused during cutover so two schedulers do not process an old
and new copy of the same queue. Compare users, invoice balances, payments,
audit records and uncertain reminders before enabling live sends.

For hosted backups use the database provider's export and recovery facilities;
the local backup command does not back up a remote database.

Local tests exercise SQLite, the real libSQL driver, and the SQLite Cloud
DB-API with a simulated server for transactions, row access, permissions,
payments and concurrent reminder claims. SQLite Cloud tests replace the
transport, not its SDK cursor/batch/result handling; they are not live network
tests. A real hosted database still needs connection,
login, payment, portal and scheduled-run verification with fictional records
before accepting real data. No database account or deployment is provisioned
by adding these files.

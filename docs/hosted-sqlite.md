# Keep SQLite while using Vercel

Vercel does not provide the persistent local filesystem this app's SQLite file
needs. A `/tmp` database would be temporary and separate across instances.
[Vercel explains this storage limitation](https://vercel.com/kb/guide/is-sqlite-supported-in-vercel).

The app now supports a hosted **libSQL** database, a SQLite fork, using the
official `libsql` Python driver. It connects directly to the remote database;
it never creates a replica, temporary payment database, or backup of your data
inside a function. The local and Docker paths still use Python's `sqlite3`.
See [the driver's remote connection guide](https://docs.turso.tech/sdk/python/quickstart#remote-libsql-database-libsql).

## Configure the database and secrets

1. Create a **libSQL** database with a host such as Turso. Choose the libSQL
   engine, not the newer Turso engine, which requires a different driver.
2. Obtain its database URL and access token. In the Vercel project's settings,
   add the variables listed in `deploy/vercel.env.example`. Use separate
   databases and credentials for Preview and Production.
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

`TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` are accepted as aliases for the two
workspace database settings. Explicit `WORKSPACE_DATABASE_*` values take priority.
The master key is required for remote storage so separate instances always use
the same encryption key. There is no fallback to a local file when a URL or token
is invalid or the hosted database is unavailable.

For a fresh database, open the site and enter the private setup code when
creating the first owner. Other visitors cannot claim the initial owner account
without that code. Once created, use the normal email/password login.

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

Local tests exercise the real libSQL driver for transactions, row access,
permissions, payments and concurrent reminder claims. The network boundary is
simulated in configuration tests. A real hosted database still needs connection,
login, payment, portal and scheduled-run verification with fictional records
before accepting real data. No database account or deployment is provisioned
by adding these files.

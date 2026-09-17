# Deploy the full workspace on one server

This guide keeps SQLite on a persistent local disk. For Vercel with a hosted
SQLite-compatible database, use [the hosted SQLite guide](hosted-sqlite.md).

This setup runs the authenticated app, customer portal and provider callbacks
behind one HTTPS address. A separate worker processes schedules when nobody has
the website open. Both services share a persistent Docker volume containing the
SQLite database and connector encryption key.

Use a Linux server with Docker Engine and the Compose plugin installed. This is
a single-host deployment: keep one web process and a local Docker volume, not a
network filesystem or replicas on separate machines. Keep an off-server backup.

## Why the Vercel build failed

`app.py` is a Streamlit UI script, not an exported Python HTTP application.
Vercel's automatic entry-point detection picks that filename before the separate
ASGI wrapper. `pyproject.toml` now explicitly selects `asgi_app:app` using
[Vercel's Python entry-point setting](https://vercel.com/docs/functions/runtimes/python#python-entrypoints).
Vercel reads the pinned runtime dependencies from `pyproject.toml`; local and
Docker installs use the matching `requirements.txt`. Deployment tests keep the
two dependency lists aligned. Both deployments use Python 3.12.

`asgi_app.py` exports Streamlit's ASGI application and also mounts the customer
and webhook routes. Selecting this entry point resolves the reported detection
error; it does not make the database durable or supervise a worker on a
serverless host. The local-file deployment therefore uses a persistent server;
the hosted SQLite option supplies remote storage and an authenticated scheduled
reminder route. Redeploying the old commit will still use its old
configuration; a build must include `pyproject.toml` to use the explicit setting.

### Connected Vercel projects

`vercel.json` disables automatic Git deployments to Vercel, following
[Vercel's Git configuration](https://vercel.com/docs/project-configuration/git-configuration#turning-off-all-automatic-deployments).
This repository's deployment target is the persistent-server stack below.
GitHub's application and container checks continue to run on pushes and PRs.

The setting applies to commits containing this file. An older deployment's
failed status remains in that commit's history; inspect the newest PR commit
after pushing this configuration. Merge the configuration into `main` to stop
automatic Vercel deployments from that branch as well. This does not delete the
connected Vercel projects or turn a manual Vercel deployment into a full-server
deployment.

`Dockerfile.workspace` runs the full app. The original `Dockerfile` remains the
isolated legacy preview used by the existing demo deployment.

## 1. Prepare the server

Clone the branch containing the full workspace and deployment files:

```sh
git clone --branch feature/mccia-payment-workspace https://github.com/mcciaexplore-netizen/-payment-reminder-followup.git payment-workspace
cd payment-workspace
cp deploy/server.env.example .env.server
chmod 600 .env.server
```

Edit `.env.server` with the real values:

```dotenv
APP_DOMAIN=payments.your-domain.example
PUBLIC_WEBHOOK_BASE_URL=https://payments.your-domain.example
```

Use the same hostname in both settings. Point the domain's DNS at the server.
Allow inbound TCP ports 80 and 443 for HTTPS, and restrict SSH to administrators.
Port 8501 is bound to the server's loopback address by Compose.

Leave demo login and live messaging disabled during setup. Provider adapters,
credentials and the gateway allowlist are configured separately; see
[the connector contract](connectors.md). The live-message switch does not disable
an explicitly requested payment-link creation or accounting fetch.

The image excludes local databases, `.env` files, backups and development demo
accounts. A new volume starts with no owner account. To migrate existing real
data instead, follow the restore procedure below before opening the app.

## 2. Create the first owner privately

Start only the internal app and worker:

```sh
docker compose config --quiet
docker compose up -d --build --wait web worker
docker compose ps
```

On your own computer, open an SSH tunnel to the server:

```sh
ssh -L 8501:127.0.0.1:8501 user@server-address
```

Open `http://127.0.0.1:8501` locally and create the business and first owner.
Use a unique password of at least 12 characters. Sign out and sign back in to
check the account. The first-owner form is bootstrap setup; complete it before
making the site public. The worker can run safely with an empty workspace.

## 3. Enable HTTPS

After owner setup and DNS are ready:

```sh
docker compose --profile public up -d --wait
docker compose --profile public ps
```

Caddy obtains and renews the TLS certificate and forwards WebSocket connections
to Streamlit. Open `https://your-actual-domain` and verify sign-in, navigation and
the worker status in the app. Test a customer link using fictional data before
sharing real links. Both routes use the same public address:

- Customer statements: `/portal/<private-token>`
- Signed provider events: `/webhooks/<business-id>/<event-kind>`
- Web health: `/_stcore/health`; HTTP route health: `/health`

HTTP health checks prove the web process responds; they do not prove provider
delivery. The worker has its own health check, based on service progress in the
last three minutes. Browser activity cannot refresh that service heartbeat.
Compose reports a stalled worker as unhealthy; its restart policy restarts exited
processes, but does not restart a process solely because it is unhealthy. Inspect
the cause before restarting an unhealthy worker:

```sh
docker compose logs --tail 100 web worker
docker compose exec -T worker python worker_health.py
docker compose --profile public logs --tail 100 https
```

Access logs are disabled because customer-link tokens appear in URLs. Protect
those URLs and avoid recording them in external analytics or proxy access logs.
The app trusts forwarded HTTPS headers from the private Docker network. Keep its
direct listener on loopback; public requests should enter through Caddy.

## 4. Back up and restore

Create a consistent SQLite snapshot while the app is running. Use a new timestamp
for each backup; the command refuses to overwrite an existing backup:

```sh
stamp=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p backups
chmod 700 backups
docker compose exec -T web python manage_workspace.py --output "/app/.data/backups/workspace-$stamp.sqlite3"
docker compose cp "web:/app/.data/backups/workspace-$stamp.sqlite3" "backups/workspace-$stamp.sqlite3"
chmod 600 "backups/workspace-$stamp.sqlite3"
```

Save the matching encryption key separately in protected off-server storage:

```sh
docker compose cp web:/app/.data/connector.key ./connector-key-backup
chmod 600 ./connector-key-backup
```

If `WORKSPACE_MASTER_KEY` is supplied by a secret manager, back up that secret
instead; a key file may not exist. Never commit a key, database or `.env.server`.
The database contains private customer/account data. Encrypt off-server backups
and set a retention schedule; the Compose stack does not automate backups.

To restore on a fresh server/volume:

1. Stop the public proxy, web and worker. Keep the source backup and key safe.
2. Prepare a new Compose project directory and a fresh `workspace-data` volume;
   do not overwrite the only copy of an existing database. An explicit
   `docker compose -p payment-workspace-restore ...` project name gives the
   restoration its own named volumes.
3. Copy the snapshot into that volume as `/app/.data/workspace.sqlite3` and its
   matching key as `/app/.data/connector.key` using a one-off maintenance
   container. Set ownership to UID/GID 1000 and file permissions to 600. If using
   an environment key, restore it in the secret manager instead.
4. Start only the web service, validate sign-in and balances through the SSH
   tunnel, and inspect queued/uncertain reminders. Keep live messaging disabled.
5. Reconcile interrupted submissions with the provider before starting the
   worker and enabling public HTTPS. Never copy an old WAL/SHM file alongside a
   SQLite backup snapshot.

The database and key must be restored together. Business ZIP exports are useful
archives, but omit credentials/accounts and cannot restore the whole server.

## 5. Updates and restarts

Back up first. Pull the deployment branch, then recreate services:

```sh
git pull --ff-only
docker compose --profile public up -d --build --wait
```

The named volume persists across container replacements. `docker compose down`
stops the stack without deleting it; **do not use `down -v`** for an ordinary
restart because that deletes the named volumes. Docker must start on boot;
Compose's restart policy then restarts these services after a server reboot.
The worker stops claiming new reminders after a termination signal and finishes
its current request. Compose allows two minutes for graceful shutdown. If the
host forcibly stops a submission, inspect uncertain jobs before any retry.

The stack provides no automated failover, managed monitoring, password recovery
email or off-server backup schedule. Validate provider sandbox flows before
turning on live operations. It is suitable for a controlled single-server pilot.

## Checks

`tests/test_deployment.py` loads the configured Vercel entry point and verifies
ASGI import, HTTP routes and first-owner UI
rendering over a WebSocket from an unrelated directory using temporary data.
The Linux CI container job builds the image, checks the internal services and
verifies that storage survives container replacement. A real server still needs
DNS, TLS, sign-in and backup/restore checks before a public launch.

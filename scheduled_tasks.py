"""Authenticated, bounded reminder passes for a hosted scheduler."""
import hmac
import os
import time

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

import config
from connectors import Connectors, Vault
from scheduling import Worker
from workspace_storage import database_location
from workspace_store import WorkspaceStore, utcnow


def _run_pass():
    deadline = time.monotonic() + 210
    store = WorkspaceStore(database_location())
    worker = Worker(store, Connectors(store, Vault(store.key_directory)))
    results = worker.tick(limit=100, stop_requested=lambda: time.monotonic() >= deadline)
    with store.transaction() as db:
        db.execute("INSERT INTO ws_worker VALUES('cron',?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat,detail=excluded.detail",
                   (utcnow().isoformat(), f"Processed {len(results)} reminders"))
    return {"processed": len(results)}


async def run_scheduled_tasks(request):
    headers = {"Cache-Control": "no-store"}
    secret = os.getenv("CRON_SECRET", "")
    supplied = request.headers.get("authorization", "")
    if (config.IS_DEMO or os.getenv("WORKSPACE_SCHEDULER_MODE") != "cron"
            or len(secret) < 32
            or not hmac.compare_digest(supplied.encode("utf-8"), ("Bearer " + secret).encode("utf-8"))):
        return JSONResponse({"error": "Unauthorized"}, status_code=401, headers=headers)
    try:
        result = await run_in_threadpool(_run_pass)
    except Exception:
        # An interrupted send remains claimed/unknown; never release it for retry.
        return JSONResponse({"error": "Reminder pass failed. Review worker history before retrying."}, status_code=503, headers=headers)
    return JSONResponse(result, headers=headers)

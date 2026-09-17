"""Run separately from the browser so schedules survive closed tabs."""
import argparse
import os
import signal
import threading
from pathlib import Path

import config
from connectors import Connectors, Vault
from scheduling import Worker
from workspace_store import WorkspaceStore, utcnow


def main(argv=None):
    parser=argparse.ArgumentParser(description="Process persistent payment reminder schedules")
    parser.add_argument("--once",action="store_true",help="Run one scheduler pass and exit")
    parser.add_argument("--interval",type=int,default=30)
    parser.add_argument("--database",type=Path,default=Path(os.getenv("WORKSPACE_DATABASE_PATH",str(config.ROOT/".data"/"workspace.sqlite3"))))
    args=parser.parse_args(argv)
    if config.IS_DEMO:
        parser.error("Background workers are disabled in hosted demo mode.")
    if not 5<=args.interval<=3600:
        parser.error("Interval must be between 5 and 3600 seconds.")
    store=WorkspaceStore(args.database)
    worker=Worker(store,Connectors(store,Vault(args.database.parent)))
    def record_progress(count):
        with store.transaction() as db:
            db.execute("INSERT OR REPLACE INTO ws_worker VALUES('service',?,?)",
                       (utcnow().isoformat(), f"Processed {count} reminders"))
    stopping = threading.Event()
    def request_stop(signum, frame):
        stopping.set()
    previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        while not stopping.is_set():
            try:
                results=worker.tick(stop_requested=stopping.is_set,on_progress=record_progress)
                print(f"Worker pass completed: {len(results)} reminders processed.",flush=True)
            except Exception:
                # Never log payloads or secrets, and never release uncertain claims.
                print("Worker pass failed. Inspect workspace history and storage before retrying uncertain attempts.",flush=True)
                if args.once:
                    return 1
            if args.once:
                return 0
            stopping.wait(args.interval)
        return 0
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__=="__main__":
    raise SystemExit(main())

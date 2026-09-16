"""Local server maintenance: consistent SQLite backup, with no secret output."""
import argparse
import os
import sqlite3
from pathlib import Path
from contextlib import closing
import config


def backup(database,destination):
    database,destination=Path(database).resolve(),Path(destination).resolve()
    if not database.is_file():
        raise ValueError("Workspace database does not exist.")
    if database==destination:
        raise ValueError("Backup must use a different file.")
    destination.parent.mkdir(parents=True,exist_ok=True)
    # Reserve a new path and refuse to overwrite an existing backup.
    fd=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(fd)
    with closing(sqlite3.connect(database)) as source,closing(sqlite3.connect(destination)) as target:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise ValueError("Backup integrity check failed.")
    return destination


def main():
    parser=argparse.ArgumentParser(description="Create a consistent backup of the business workspace")
    parser.add_argument("--database",type=Path,default=Path(os.getenv("WORKSPACE_DATABASE_PATH",str(config.ROOT/".data"/"workspace.sqlite3"))))
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    try:
        backup(args.database,args.output)
    except (ValueError,OSError,sqlite3.Error) as exc:
        parser.error(str(exc))
    print("Backup created and checked. Store the connector encryption key separately; it is required to restore saved credentials.")


if __name__=="__main__":
    main()

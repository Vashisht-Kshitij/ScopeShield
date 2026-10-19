"""Consistent SQLite backup and non-overwriting, quarantined restore.

The CLI accepts explicit paths only. It never chooses the workstation database.
Backups contain private data: keep mode 0600 and transfer encrypted off-host.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from app.storage_limits import MAX_DATABASE_BYTES


def file_sha256(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def inspect_database(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)] or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Database integrity validation failed")
        revision = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    return revision


def backup(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or not source.is_file():
        raise ValueError("Provide an existing source and a new backup path")
    if source.stat().st_size > MAX_DATABASE_BYTES:
        raise ValueError("Database exceeds the recoverable 1 GiB limit; preserve it for operator review")
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original, closing(sqlite3.connect(target)) as copy:
            original.backup(copy, pages=256, sleep=0.01)
        revision = inspect_database(target)
        with target.open("r+b") as handle:
            os.fsync(handle.fileno())
        sync_directory(target.parent)
        if target.stat().st_size > MAX_DATABASE_BYTES:
            raise ValueError("Consistent snapshot exceeds the recoverable database limit")
        return {"revision": revision, "sha256": file_sha256(target), "integrity": "ok"}
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def restore(source, target):
    """Never overwrite a database. A marker blocks application startup on failure."""
    source, target = Path(source).resolve(), Path(target).resolve()
    inspect_database(source)
    if target.exists() or any(target.parent.glob(target.name + "-*")):
        raise ValueError("Restore requires a new target without database sidecars")
    marker = target.parent / ".restore-pending"
    with marker.open("x", encoding="ascii") as handle:
        handle.write("Restored snapshot: keep provider actions disabled; revoke sessions and review lost reservations/budgets before release.\n")
        handle.flush()
        os.fsync(handle.fileno())
    sync_directory(marker.parent)
    result = backup(source, target)
    with closing(sqlite3.connect(target)) as db:
        # A recovered old session must not become valid again.
        db.execute("DELETE FROM auth_sessions")
        db.commit()
    inspect_database(target)
    with target.open("r+b") as handle:
        os.fsync(handle.fileno())
    sync_directory(target.parent)
    return {**result, "sha256": file_sha256(target), "quarantined": True, "sessions_revoked": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    result = (backup if args.action == "backup" else restore)(args.source, args.target)
    print(json.dumps(result))


if __name__ == "__main__":
    main()

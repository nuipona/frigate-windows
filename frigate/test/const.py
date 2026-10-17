"""Constants and shared helpers for testing."""

import gc
import os

from playhouse.sqliteq import SqliteQueueDatabase


def test_db_path(directory: str) -> str:
    """Database file for one test class, inside its own temp directory.

    Every harness used to share ``test.db`` in the working directory and
    delete it between classes. On Windows an open database file cannot be
    deleted (the queue writer and app threads hold connections), so the
    deletions silently failed and rows leaked into the next class. A
    per-class file makes leakage impossible even if removal lags.
    """
    return os.path.join(directory, "test.db")


def cleanup_test_db(db: SqliteQueueDatabase) -> None:
    """Stop a test queue database and delete its files.

    The queue writer thread owns a connection that ``close()`` does not
    release, so stop it first; connections held by dead app/test-client
    threads release on collection.
    """
    try:
        db.stop()
    except Exception:
        pass

    try:
        if not db.is_closed():
            db.close()
    except Exception:
        pass

    gc.collect()

    base = db.database
    for suffix in ("", "-shm", "-wal"):
        try:
            os.remove(f"{base}{suffix}")
        except OSError:
            pass

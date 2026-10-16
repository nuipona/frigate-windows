"""Constants and shared helpers for testing."""

import gc
import os

from playhouse.sqliteq import SqliteQueueDatabase

TEST_DB = "test.db"
TEST_DB_CLEANUPS = ["test.db", "test.db-shm", "test.db-wal"]


def cleanup_test_db(db: SqliteQueueDatabase) -> None:
    """Stop a test queue database and delete the shared ``test.db`` files.

    The queue writer thread owns a connection that ``close()`` does not
    release. On Windows an open database file cannot be deleted, so without
    ``stop()`` the cleanup removals fail and rows leak into the next test
    class, which then sees other tests' events and recordings (Linux
    unlinks open files happily, so this only ever bit on Windows).
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

    # connections held by dead app/test-client threads release on collection
    gc.collect()

    for file in TEST_DB_CLEANUPS:
        try:
            os.remove(file)
        except OSError:
            pass

"""Windows-specific loading tests for the sqlite-vec extension."""

import sys
import unittest
from types import ModuleType
from unittest.mock import MagicMock, patch

from frigate.db.sqlitevecq import SqliteVecQueueDatabase


class TestWindowsSqliteVecLoading(unittest.TestCase):
    def test_default_extension_name_resolves_to_package_dll(self):
        database = SqliteVecQueueDatabase.__new__(SqliteVecQueueDatabase)
        database.sqlite_vec_path = "vec0"
        database.load_vec_extension = True
        connection = MagicMock()
        sqlite_vec = ModuleType("sqlite_vec")

        def loadable_path() -> str:
            return r"C:\Python\Lib\site-packages\sqlite_vec\vec0.dll"

        sqlite_vec.loadable_path = loadable_path

        with patch.dict(sys.modules, {"sqlite_vec": sqlite_vec}):
            database._load_vec_extension(connection)

        connection.load_extension.assert_called_once_with(
            r"C:\Python\Lib\site-packages\sqlite_vec\vec0.dll"
        )
        connection.enable_load_extension.assert_any_call(True)
        connection.enable_load_extension.assert_any_call(False)


if __name__ == "__main__":
    unittest.main(verbosity=2)

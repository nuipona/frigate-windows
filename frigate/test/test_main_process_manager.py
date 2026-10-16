"""Process containment is installed before Frigate spawns its manager."""

import unittest
from unittest.mock import patch

from frigate import __main__ as frigate_main


class TestMainProcessManager(unittest.TestCase):
    def test_job_object_is_installed_before_manager_starts(self):
        calls = []

        with (
            patch.object(
                frigate_main,
                "install_job_object",
                side_effect=lambda: calls.append("job object"),
            ),
            patch.object(frigate_main, "supports_forkserver", return_value=False),
            patch.object(
                frigate_main.mp,
                "Manager",
                side_effect=lambda: calls.append("manager") or object(),
            ),
        ):
            manager = frigate_main._create_process_manager()

        self.assertEqual(calls, ["job object", "manager"])
        self.assertIsNotNone(manager)


if __name__ == "__main__":
    unittest.main()

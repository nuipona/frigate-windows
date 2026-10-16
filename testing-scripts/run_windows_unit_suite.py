"""Run the unit suite on Windows with a visible list of known failures.

The Windows CI job used to run a handful of hand picked tests, which meant
every new test was Linux only by default and a Windows regression was invisible
until a user hit it. This runs the whole suite instead.

Tests that cannot pass on Windows are listed in
``frigate/test/windows_suite_denylist.txt``, one entry per line::

    module.Class.test_name  # why it cannot run on Windows

An entry may also name a whole class or module. Anything that fails without an
entry is reported with its traceback and makes the run fail, so the denylist can
only shrink deliberately.

Run from the repository root::

    python testing-scripts/run_windows_unit_suite.py
"""

from __future__ import annotations

import faulthandler
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = REPO_ROOT / "frigate" / "test"
DENYLIST = TEST_ROOT / "windows_suite_denylist.txt"


def load_denylist(path: Path = DENYLIST) -> list[tuple[str, str]]:
    """Return (test id prefix, reason) for every denied test."""
    entries: list[tuple[str, str]] = []

    if not path.is_file():
        return entries

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()

        if not line or line.startswith("#"):
            continue

        test_id, _, reason = line.partition("#")
        entries.append((test_id.strip(), reason.strip() or "no reason given"))

    return entries


def is_denied(test_id: str, denylist: list[tuple[str, str]]) -> bool:
    """Whether a test id matches a denied module, class or test."""
    return any(
        test_id == prefix or test_id.startswith(f"{prefix}.") for prefix, _ in denylist
    )


def main() -> int:
    # Several tests load native inference and media libraries. If one faults,
    # ordinary unittest output ends at a progress dot and gives no clue which
    # module took the interpreter down. Dump every Python thread on a native
    # fault, and use verbose test names below so the final log line identifies
    # the test that was running.
    faulthandler.enable(all_threads=True)

    denylist = load_denylist()

    loader = unittest.TestLoader()
    suite = loader.discover(str(TEST_ROOT), top_level_dir=str(REPO_ROOT))

    denied_suite = unittest.TestSuite()
    allowed_suite = unittest.TestSuite()

    for test in iterate(suite):
        if is_denied(test.id(), denylist):
            denied_suite.addTest(test)
        else:
            allowed_suite.addTest(test)

    print(f"Running {allowed_suite.countTestCases()} tests on Windows")
    print(f"Skipping {denied_suite.countTestCases()} listed in {DENYLIST.name}")
    print()

    result = unittest.TextTestRunner(verbosity=2).run(allowed_suite)

    failures = [
        (test.id(), traceback_text)
        for test, traceback_text in result.failures + result.errors
    ]

    print()
    print(
        f"ran {result.testsRun}, "
        f"failures {len(result.failures)}, "
        f"errors {len(result.errors)}, "
        f"skipped {len(result.skipped)}"
    )

    if failures:
        print()
        print(f"{len(failures)} tests fail on Windows and are not in {DENYLIST.name}:")
        print()

        for test_id, traceback_text in failures:
            print(test_id)
            print(traceback_text.rstrip())
            print()

        return 1

    print("Every test that is not in the denylist passed on Windows")
    return 0


def iterate(suite: unittest.TestSuite | unittest.TestCase):
    """Yield every test case in a suite, however deeply it is nested."""
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from iterate(test)
        else:
            yield test


if __name__ == "__main__":
    sys.exit(main())

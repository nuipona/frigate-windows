"""Print Windows unit-suite failures from suite.log as GitHub annotations.

The Windows CI job tees the unit suite output to ``suite.log`` and runs this
on failure, so the failing tests and their tracebacks show up in the pull
request Checks view instead of being buried in the job log. It also leaves
the full log available as a workflow artifact (see windows-python.yml).

Run from the repository root after a suite run::

    python testing-scripts/ci_report_suite_log.py [suite.log]
"""

from __future__ import annotations

import sys
from pathlib import Path

#: GitHub renders at most 10 error annotations per step.
MAX_BLOCKS = 10


def _escape(line: str) -> str:
    return line.replace("%", "%25").replace("\r", "")


def failure_blocks(lines: list[str]) -> tuple[str, list[tuple[str, list[str]]]]:
    """Split the runner's failure section into (summary, [(test id, traceback)])."""
    summary = ""
    start = None

    for index, line in enumerate(lines):
        if line.startswith("ran ") and ", failures " in line:
            summary = line.strip()
        if "tests fail on Windows" in line:
            start = index + 1

    blocks: list[tuple[str, list[str]]] = []
    if start is None:
        return summary, blocks

    current: tuple[str, list[str]] | None = None
    for line in lines[start:]:
        if line.startswith("frigate.test."):
            current = (line.strip(), [])
            blocks.append(current)
        elif current is not None:
            current[1].append(line)

    return summary, blocks


def main(path: Path) -> int:
    if not path.exists():
        print("::error::suite.log was not created")
        return 0

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    summary, blocks = failure_blocks(lines)

    if summary:
        print(f"::error::{_escape(summary)}")

    if blocks:
        # one warning annotation (separate quota from the errors below) so
        # the full list of failing ids survives GitHub's per-step cap
        ids = "%0A".join(test_id for test_id, _ in blocks)
        print(f"::warning::all failing test ids:%0A{ids}")

    for test_id, traceback in blocks[: MAX_BLOCKS - 1]:
        # GitHub truncates annotation messages, and the root exception sits
        # at the END of a traceback (wrapped server errors especially), so
        # keep the tail of the block rather than the framework frames.
        kept: list[str] = []
        size = 0
        for line in reversed([line for line in traceback if line.strip()]):
            kept.append(line)
            size += len(line) + 3
            if size > 900:
                break
        kept.reverse()
        body = "%0A".join(_escape(line) for line in kept)
        print(f"::error::{test_id}%0A...%0A{body}")

    if len(blocks) > MAX_BLOCKS:
        remaining = ", ".join(test_id for test_id, _ in blocks[MAX_BLOCKS:])
        print(f"::error::and {len(blocks) - MAX_BLOCKS} more: {remaining}")

    if not blocks:
        # the suite died before its summary (a hard crash, not a test failure)
        tail = "%0A".join(_escape(line) for line in lines[-12:])
        print(f"::error::suite produced no failure summary, last lines:%0A{tail}")

    return 0


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("suite.log"))

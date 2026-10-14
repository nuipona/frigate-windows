"""Tests for testing-scripts/check_upstream_parity.py.

The checker is what keeps "this fork still carries upstream" honest, so its
own failure modes matter: it has to fail when upstream gains a file, when a
ported file diverges without a review, and when upstream edits code the port
replaced. These tests build throwaway repositories and exercise those paths
without touching the network or the real tree.
"""

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

UPSTREAM_LINUX_ONLY = "import resource\n\nresource.getrlimit(1)\n"
UPSTREAM_SHARED = "one\ntwo\nthree\n"


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_upstream_parity",
        REPO_ROOT / "testing-scripts" / "check_upstream_parity.py",
    )
    module = importlib.util.module_from_spec(spec)
    # the script uses `from __future__ import annotations`, so dataclasses
    # resolve its annotations through sys.modules while it is being executed
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parity = _load_checker()


class ParityRepo:
    """A repository whose ``upstream`` branch is the reference tree."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def write(self, name: str, content: str | bytes) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")

    def remove(self, name: str) -> None:
        (self.root / name).unlink()

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git(
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            message,
        )
        return self.sha("HEAD")

    def sha(self, rev: str) -> str:
        return self.git("rev-parse", "--verify", f"{rev}^{{commit}}").strip()


def build_upstream_repo(root: Path) -> ParityRepo:
    repo = ParityRepo(root)
    repo.git("init", "-b", "main")
    repo.git("config", "user.email", "test@example.com")
    repo.git("config", "user.name", "Test")

    repo.write("shared.py", UPSTREAM_SHARED)
    repo.write("linux_only.py", UPSTREAM_LINUX_ONLY)
    repo.write("untouched.py", "same in both trees\n")
    repo.write("blob.bin", b"\x00upstream bytes")
    repo.commit("upstream state")
    repo.git("branch", "upstream")
    return repo


class ParityTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo = build_upstream_repo(self.tmp / "repo")
        self.upstream_sha = self.repo.sha("upstream")
        self.baseline_path = self.tmp / "baseline.json"
        self.last_output = ""

    def make_fork(self) -> str:
        """Turn the working tree into the port and return its commit."""
        self.repo.write("shared.py", "one\nTWO (windows)\nthree\n")
        self.repo.write("windows_only.py", "import msvcrt\n")
        return self.repo.commit("port")

    def delete_upstream_module(self) -> str:
        """Drop an upstream module, the shape a lost feature takes."""
        self.repo.remove("linux_only.py")
        return self.repo.commit("drop the module the port believes it owns")

    def state(self, fork: str):
        return parity.build_state(self.repo.root, self.upstream_sha, fork)

    def baseline_for(self, state, reasons: dict[str, str]) -> dict:
        """A baseline for ``state`` with ``reasons`` filled in per path."""
        baseline = parity.baseline_from(state, {})
        for path, reason in reasons.items():
            if path in baseline["fork_only_files"]:
                baseline["fork_only_files"][path] = reason
            else:
                baseline["diverged_files"][path]["reason"] = reason
        return baseline

    def full_baseline(self, state) -> dict:
        return self.baseline_for(
            state,
            {"windows_only.py": "a Windows module", "shared.py": "replaced"},
        )

    def write_baseline(self, baseline: dict) -> None:
        self.baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    def run_main(self, *extra: str, upstream: str | None = None) -> int:
        """Run the checker against the throwaway repo, capturing its report."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = parity.main(
                [
                    "--repo",
                    str(self.repo.root),
                    "--upstream",
                    upstream or self.upstream_sha,
                    "--baseline",
                    str(self.baseline_path),
                    *extra,
                ]
            )
        self.last_output = buffer.getvalue()
        return code


class TestDivergence(ParityTestCase):
    def test_classifies_added_deleted_and_modified(self):
        self.make_fork()
        self.delete_upstream_module()
        state = self.state(self.repo.sha("HEAD"))

        self.assertEqual(state.fork_only_files, ["windows_only.py"])
        self.assertEqual(state.upstream_only_files, ["linux_only.py"])
        self.assertEqual(list(state.diverged), ["shared.py"])
        self.assertNotIn("untouched.py", state.diverged)

    def test_missing_lines_are_the_upstream_lines_not_carried(self):
        state = self.state(self.make_fork())

        self.assertEqual(state.diverged["shared.py"].missing_lines, ["two"])
        self.assertEqual(state.diverged["shared.py"].fork_only_lines, 1)


class TestCompare(ParityTestCase):
    def test_upstream_only_file_is_always_a_failure(self):
        self.make_fork()
        self.delete_upstream_module()
        state = self.state(self.repo.sha("HEAD"))
        baseline = self.full_baseline(state)
        baseline["diverged_files"]["linux_only.py"] = {
            "reason": "pretending this was reviewed does not help",
            "missing_lines": 0,
            "missing_digest": "sha256:x",
            "fork_only_lines": 0,
        }

        verdict = parity.compare(state, baseline)
        summaries = " ".join(problem.summary for problem in verdict.problems)
        details = " ".join(
            detail for problem in verdict.problems for detail in problem.detail
        )

        self.assertFalse(verdict.ok)
        self.assertIn("does not have", summaries)
        self.assertIn("linux_only.py", details)

    def test_reviewed_baseline_passes(self):
        state = self.state(self.make_fork())

        verdict = parity.compare(state, self.full_baseline(state))

        self.assertTrue(verdict.ok, verdict.problems)

    def test_new_divergence_without_a_reason_fails(self):
        state = self.state(self.make_fork())

        verdict = parity.compare(state, {"fork_only_files": {}, "diverged_files": {}})
        summaries = " ".join(problem.summary for problem in verdict.problems)

        self.assertFalse(verdict.ok)
        self.assertIn(
            "shared.py: diverges from upstream with no recorded reason", summaries
        )
        self.assertIn(
            "windows_only.py: fork-only file with no recorded reason", summaries
        )

    def test_empty_reason_is_a_failure(self):
        state = self.state(self.make_fork())
        baseline = self.baseline_for(state, {"windows_only.py": "", "shared.py": ""})

        verdict = parity.compare(state, baseline)

        self.assertFalse(verdict.ok)
        self.assertEqual(
            sorted(problem.summary for problem in verdict.problems),
            [
                "shared.py: reason is empty",
                "windows_only.py: fork-only file, reason is empty",
            ],
        )

    def test_upstream_editing_replaced_code_changes_the_digest(self):
        fork = self.make_fork()
        baseline = self.full_baseline(self.state(fork))

        # upstream now edits the very line the port replaced
        self.repo.git("checkout", "upstream")
        self.repo.write("shared.py", "one\ntwo, now different\nthree\n")
        self.repo.commit("upstream edit")
        moved = self.repo.sha("HEAD")

        verdict = parity.compare(
            parity.build_state(self.repo.root, moved, fork), baseline
        )
        summaries = " ".join(problem.summary for problem in verdict.problems)

        self.assertFalse(verdict.ok)
        self.assertIn("shared.py: divergence changed since the last review", summaries)

    def test_binary_files_are_noted_rather_than_failed(self):
        self.make_fork()
        self.repo.write("blob.bin", b"\x00fork bytes")
        fork = self.repo.commit("port with a rebuilt binary")

        state = self.state(fork)
        self.assertEqual(state.binary_paths, ["blob.bin"])
        self.assertNotIn("blob.bin", state.diverged)

        verdict = parity.compare(state, self.full_baseline(state))

        self.assertTrue(verdict.ok, verdict.problems)
        self.assertIn("blob.bin", " ".join(verdict.notes))

    def test_a_file_that_stops_diverging_is_a_failure(self):
        """A port that reverts to upstream code should not pass unnoticed."""
        fork = self.make_fork()
        baseline = self.full_baseline(self.state(fork))
        baseline["diverged_files"]["untouched.py"] = {
            "reason": "never diverged in this fixture",
            "missing_lines": 0,
            "missing_digest": "sha256:x",
            "fork_only_lines": 0,
        }

        verdict = parity.compare(self.state(fork), baseline)

        self.assertFalse(verdict.ok)
        self.assertIn(
            "untouched.py: recorded as diverged but the trees now agree",
            " ".join(problem.summary for problem in verdict.problems),
        )

    def test_a_removed_fork_only_file_is_a_failure(self):
        fork = self.make_fork()
        baseline = self.full_baseline(self.state(fork))
        baseline["fork_only_files"]["gone.py"] = "a Windows module"

        verdict = parity.compare(self.state(fork), baseline)

        self.assertFalse(verdict.ok)
        self.assertIn(
            "gone.py: recorded as fork-only but it is not present any more",
            " ".join(problem.summary for problem in verdict.problems),
        )


class TestMain(ParityTestCase):
    def test_missing_baseline_is_a_usage_error(self):
        self.make_fork()

        self.assertEqual(self.run_main(), parity.EXIT_USAGE)

    def test_update_then_fill_reasons_then_pass(self):
        self.make_fork()

        self.assertEqual(self.run_main("--update"), parity.EXIT_OK)
        self.assertIn("still need a reason", self.last_output)
        written = json.loads(self.baseline_path.read_text(encoding="utf-8"))
        self.assertEqual(written["upstream_commit"], self.upstream_sha)
        self.assertEqual(sorted(written["fork_only_files"]), ["windows_only.py"])
        self.assertEqual(sorted(written["diverged_files"]), ["shared.py"])

        # an entry without a reason is a failure, not a pass
        self.assertEqual(self.run_main(), parity.EXIT_PARITY_FAILURE)
        self.assertIn("reason is empty", self.last_output)
        self.assertNotIn("PASS", self.last_output)

        written["fork_only_files"]["windows_only.py"] = "a Windows module"
        written["diverged_files"]["shared.py"]["reason"] = "replaced the call"
        self.write_baseline(written)

        self.assertEqual(self.run_main(), parity.EXIT_OK)
        self.assertIn("PASS", self.last_output)

    def test_a_dropped_upstream_module_keeps_the_check_red(self):
        self.make_fork()
        state = self.state(self.repo.sha("HEAD"))
        self.write_baseline(self.full_baseline(state))

        self.assertEqual(self.run_main(), parity.EXIT_OK)

        self.delete_upstream_module()

        self.assertEqual(self.run_main(), parity.EXIT_PARITY_FAILURE)
        self.assertIn("linux_only.py is missing here", self.last_output)

        # restoring it, with upstream's content, clears the failure
        self.repo.write("linux_only.py", UPSTREAM_LINUX_ONLY)
        self.repo.commit("restore upstream module")

        self.assertEqual(self.run_main(), parity.EXIT_OK)

    def test_update_keeps_recorded_reasons(self):
        state = self.state(self.make_fork())
        self.write_baseline(self.full_baseline(state))

        self.assertEqual(self.run_main("--update"), parity.EXIT_OK)
        written = json.loads(self.baseline_path.read_text(encoding="utf-8"))

        self.assertEqual(
            written["fork_only_files"]["windows_only.py"], "a Windows module"
        )
        self.assertEqual(written["diverged_files"]["shared.py"]["reason"], "replaced")

    def test_drift_reports_upstream_moving_past_the_baseline(self):
        state = self.state(self.make_fork())
        self.write_baseline(self.full_baseline(state))

        self.assertEqual(self.run_main("--drift"), parity.EXIT_OK)

        self.repo.git("checkout", "upstream")
        self.repo.write("new_upstream_file.py", "new upstream feature\n")
        self.repo.commit("upstream moves on")
        moved = self.repo.sha("HEAD")

        code = self.run_main("--drift", upstream=moved)

        self.assertEqual(code, parity.EXIT_PARITY_FAILURE)
        self.assertIn("has moved past the commit", self.last_output)
        self.assertIn("upstream moves on", self.last_output)

    def test_show_prints_the_lines_this_fork_lacks(self):
        state = self.state(self.make_fork())
        self.write_baseline(self.full_baseline(state))

        self.assertEqual(self.run_main("--show", "shared.py"), parity.EXIT_OK)
        self.assertIn("two", self.last_output)

    def test_show_tolerates_a_path_in_only_one_tree(self):
        state = self.state(self.make_fork())
        self.write_baseline(self.full_baseline(state))

        self.assertEqual(self.run_main("--show", "not_here.py"), parity.EXIT_OK)
        self.assertIn("not present in both trees", self.last_output)


class TestCheckedInBaseline(unittest.TestCase):
    def test_every_entry_has_a_reason_and_a_digest(self):
        """The repository's own baseline is what the CI job will read."""
        path = REPO_ROOT / "testing-scripts" / "upstream_parity_baseline.json"
        baseline = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(baseline["upstream_commit"])
        self.assertTrue(baseline["fork_only_files"])
        self.assertTrue(baseline["diverged_files"])

        for entry, reason in baseline["fork_only_files"].items():
            self.assertTrue(reason.strip(), f"{entry} has no reason")

        for entry, record in baseline["diverged_files"].items():
            self.assertTrue(record["reason"].strip(), f"{entry} has no reason")
            self.assertTrue(record["missing_digest"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()

"""Check that this fork still carries everything upstream Frigate carries.

The Windows port is a *deliberate* divergence from
https://github.com/blakeblackshear/frigate: Linux-only code is replaced rather
than deleted, and whole modules are added that upstream does not have. That
makes "diff the trees and read every hunk" the only real parity check, and a
manual one, which is exactly the kind of check that rots.

This script turns the reviewed result of that reading into a checked-in
baseline (``upstream_parity_baseline.json``, next to this file) and fails when
the trees diverge in a way the baseline does not describe:

* a file upstream has that this fork does not have at all, unless the baseline
  records why it was dropped — an unrecorded removal is the shape a lost
  feature takes, and the reason is where the reviewer says it was deliberate;
* a file that diverges without an entry in the baseline, or with an entry
  whose reason is still empty — a new port needs a reviewer, and the reason is
  where the review is recorded;
* a file that diverges in a way the baseline does not describe any more: the
  digest of the upstream lines this fork deliberately lacks changed, so
  upstream has edited code this fork replaced. That is the case a plain
  "merge and forget" loses track of, so the new lines are printed for the
  reviewer to re-justify.

Everything else is allowed to move: this fork's own port work and upstream
edits to files whose replaced line set is unchanged do not need a re-audit.

Usage::

    python testing-scripts/check_upstream_parity.py                 # check
    python testing-scripts/check_upstream_parity.py --fetch          # check, fetching the synced commit
    python testing-scripts/check_upstream_parity.py --drift          # has upstream moved past the sync?
    python testing-scripts/check_upstream_parity.py --show PATH      # print the lines this fork lacks
    python testing-scripts/check_upstream_parity.py --update         # re-baseline, after reviewing

Standard library only, no third-party imports, Windows and POSIX.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
BASELINE_PATH = SCRIPT_DIR / "upstream_parity_baseline.json"
UPSTREAM_URL = "https://github.com/blakeblackshear/frigate.git"
UPSTREAM_BRANCH = "dev"
UPSTREAM_REF = "refs/remotes/upstream/dev"
PINNED_REF = "refs/remotes/upstream/pinned"

#: how many upstream-only lines get printed when one file needs re-review
SAMPLE_LINES = 12

EXIT_OK = 0
EXIT_PARITY_FAILURE = 1
EXIT_USAGE = 2


class ParityError(Exception):
    """A failure that makes the parity verdict unknowable."""


def run_git(repo: Path, *args: str, check: bool = True) -> str:
    """Run a git command in ``repo`` and return its stdout as text."""
    env = dict(os.environ)
    env["GIT_PAGER"] = "cat"
    env["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if check and result.returncode != 0:
        raise ParityError(
            f"git {' '.join(args)} failed with {result.returncode}: "
            f"{result.stderr.strip()}"
        )
    return result.stdout


def run_git_bytes(repo: Path, *args: str) -> bytes:
    """Run a git command and return its stdout as bytes (for blob content)."""
    env = dict(os.environ)
    env["GIT_PAGER"] = "cat"
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        env=env,
    )
    if result.returncode != 0:
        raise ParityError(
            f"git {' '.join(args)} failed with {result.returncode}: "
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )
    return result.stdout


def find_repo(start: Path | None = None) -> Path:
    """Locate the repository root, defaulting to this script's tree."""
    start = start or SCRIPT_DIR
    try:
        top = run_git(start, "rev-parse", "--show-toplevel").strip()
    except ParityError as err:
        raise ParityError(f"{start} is not inside a git repository") from err
    return Path(top)


def resolve(repo: Path, rev: str) -> str:
    """Resolve a revision to a commit sha, or raise."""
    try:
        return run_git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").strip()
    except ParityError as err:
        raise ParityError(f"{rev} does not resolve to a commit in {repo}") from err


def fetch_upstream(
    repo: Path,
    *,
    url: str,
    ref: str,
    branch: str | None = None,
    commit: str | None = None,
    depth: int = 1,
) -> str:
    """Fetch one upstream commit into ``ref`` and return its sha.

    Fetching an exact commit is what makes the pull request check
    deterministic: it compares against the commit the baseline documents, not
    against whatever upstream's branch tip happens to be that day.
    """
    source = commit or branch
    if source is None:
        raise ParityError("fetch_upstream needs a branch or a commit")

    if commit:
        # a sha cannot be a refspec source on every server, so fetch it into
        # FETCH_HEAD and copy that into the ref this script compares against
        run_git(repo, "fetch", "--depth", str(depth), "--no-tags", url, commit)
        head = run_git(repo, "rev-parse", "FETCH_HEAD").strip()
        run_git(repo, "update-ref", ref, head)
        return head

    run_git(
        repo,
        "fetch",
        "--depth",
        str(depth),
        "--no-tags",
        url,
        f"+refs/heads/{branch}:{ref}",
    )
    return resolve(repo, ref)


def name_status(repo: Path, upstream: str, fork: str) -> dict[str, str]:
    """Map path -> status for every path that differs between the two trees.

    ``A`` means the fork has a file upstream does not (a Windows addition),
    ``D`` means upstream has a file the fork does not (a lost feature), and
    ``M`` means both have it and the content differs (the ported files).
    """
    raw = run_git(
        repo,
        "diff",
        "--name-status",
        "-z",
        "--no-renames",
        upstream,
        fork,
    )
    fields = raw.split("\0")
    if fields and fields[-1] == "":
        fields.pop()

    status: dict[str, str] = {}
    for index in range(0, len(fields) - 1, 2):
        code = fields[index]
        path = fields[index + 1]
        status[path] = code[:1]
    return status


def blob(repo: Path, rev: str, path: str) -> bytes:
    """Read one file's bytes at one revision, empty when it is absent."""
    out = run_git_bytes(repo, "show", f"{rev}:{path}")
    return out


def to_lines(data: bytes) -> list[str] | None:
    """Split a blob into lines, or None when it is not text."""
    if b"\0" in data:
        return None
    return data.decode("utf-8", "replace").splitlines()


def missing_upstream_lines(
    upstream_lines: list[str], fork_lines: list[str]
) -> list[str]:
    """The upstream lines this fork does not carry.

    A ``replace`` or ``delete`` opcode is upstream code that this fork wrote
    differently or dropped, which is the whole population a parity review has
    to justify.
    """
    matcher = difflib.SequenceMatcher(None, upstream_lines, fork_lines, autojunk=False)
    missing: list[str] = []
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            missing.extend(upstream_lines[i1:i2])
    return missing


def normalize(lines: list[str]) -> str:
    """Stable text for hashing, insensitive to line endings only."""
    return "\n".join(line.rstrip("\r\n") for line in lines)


def digest(lines: list[str]) -> str:
    return "sha256:" + hashlib.sha256(normalize(lines).encode("utf-8")).hexdigest()


@dataclass
class DivergedFile:
    """What one ported file looks like against upstream right now."""

    path: str
    missing_lines: list[str]
    fork_only_lines: int
    binary: bool = False

    @property
    def missing_count(self) -> int:
        return len(self.missing_lines)

    @property
    def missing_digest(self) -> str:
        return digest(self.missing_lines)


@dataclass
class State:
    """The fork's divergence from one upstream commit."""

    upstream_commit: str
    fork_commit: str
    fork_only_files: list[str] = field(default_factory=list)
    diverged: dict[str, DivergedFile] = field(default_factory=dict)
    upstream_only_files: list[str] = field(default_factory=list)
    binary_paths: list[str] = field(default_factory=list)


def build_state(repo: Path, upstream: str, fork: str) -> State:
    """Compute the fork's divergence from ``upstream``."""
    state = State(upstream_commit=upstream, fork_commit=fork)
    statuses = name_status(repo, upstream, fork)

    for path, code in sorted(statuses.items()):
        if code == "A":
            state.fork_only_files.append(path)
        elif code == "D":
            state.upstream_only_files.append(path)
        elif code == "M":
            up_lines = to_lines(blob(repo, upstream, path))
            fork_lines = to_lines(blob(repo, fork, path))

            if up_lines is None or fork_lines is None:
                state.binary_paths.append(path)
                continue

            missing = missing_upstream_lines(up_lines, fork_lines)
            state.diverged[path] = DivergedFile(
                path=path,
                missing_lines=missing,
                fork_only_lines=len(missing_upstream_lines(fork_lines, up_lines)),
            )

    return state


def load_baseline(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ParityError(
            f"{path} does not exist; run --update once, review the result, then commit it"
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise ParityError(f"cannot read {path}: {err}") from err


@dataclass
class Problem:
    """One reason parity cannot be claimed."""

    summary: str
    detail: list[str] = field(default_factory=list)


@dataclass
class Verdict:
    problems: list[Problem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def compare(state: State, baseline: dict[str, Any]) -> Verdict:
    """Decide whether this divergence matches the reviewed baseline."""
    verdict = Verdict()
    recorded_files: dict[str, str] = baseline.get("fork_only_files") or {}
    recorded_diverged: dict[str, dict[str, Any]] = baseline.get("diverged_files") or {}

    recorded_removed: dict[str, str] = baseline.get("removed_upstream_files") or {}
    unreviewed_removals = [
        path
        for path in state.upstream_only_files
        if not (recorded_removed.get(path) or "").strip()
    ]

    if unreviewed_removals:
        verdict.problems.append(
            Problem(
                f"upstream has {len(unreviewed_removals)} file(s) this fork does not have",
                [
                    f"  {path} is missing here; upstream features live in it"
                    for path in unreviewed_removals[:SAMPLE_LINES]
                ]
                + (
                    [f"  ... and {len(unreviewed_removals) - SAMPLE_LINES} more"]
                    if len(unreviewed_removals) > SAMPLE_LINES
                    else []
                )
                + [
                    "  record why it was removed in removed_upstream_files with "
                    "--update, or restore it"
                ],
            )
        )

    for path in state.fork_only_files:
        reason = recorded_files.get(path)
        if reason is None:
            verdict.problems.append(
                Problem(
                    f"{path}: fork-only file with no recorded reason",
                    ["  review it, then record why in the baseline with --update"],
                )
            )
        elif not reason.strip():
            verdict.problems.append(
                Problem(f"{path}: fork-only file, reason is empty", [])
            )

    for path, diverged in sorted(state.diverged.items()):
        entry = recorded_diverged.get(path)
        if entry is None:
            details = [
                f"  upstream lines not carried here: {diverged.missing_count}",
                *[
                    f"    upstream: {line}"
                    for line in diverged.missing_lines[:SAMPLE_LINES]
                ],
                "  audit the lines above, then record the reason with --update",
            ]
            verdict.problems.append(
                Problem(
                    f"{path}: diverges from upstream with no recorded reason", details
                )
            )
            continue

        reason = entry.get("reason") or ""
        if not reason.strip():
            verdict.problems.append(Problem(f"{path}: reason is empty", []))

        if entry.get("missing_digest") != diverged.missing_digest:
            details = [
                "  upstream edited code this fork replaces, so the replaced set changed",
                f"  upstream lines not carried here: {diverged.missing_count}",
                *[
                    f"    upstream now has: {line}"
                    for line in diverged.missing_lines[:SAMPLE_LINES]
                ],
                "  re-justify the replacements, then re-baseline with --update",
            ]
            verdict.problems.append(
                Problem(f"{path}: divergence changed since the last review", details)
            )

    for path in recorded_diverged:
        if path not in state.diverged:
            # a ported file matching upstream again means the Windows
            # behaviour was dropped, or upstream adopted it and the entry is
            # stale; both are worth a human look
            verdict.problems.append(
                Problem(
                    f"{path}: recorded as diverged but the trees now agree",
                    [
                        "  the port that justified the entry is gone, or upstream "
                        "adopted it",
                        "  confirm which, then re-baseline with --update",
                    ],
                )
            )

    for path in recorded_files:
        if path not in state.fork_only_files:
            verdict.problems.append(
                Problem(
                    f"{path}: recorded as fork-only but it is not present any more",
                    ["  a Windows module was removed; re-baseline with --update"],
                )
            )

    for path in state.binary_paths:
        verdict.notes.append(f"{path}: binary content differs; audited by hand")

    return verdict


def baseline_from(state: State, previous: dict[str, Any]) -> dict[str, Any]:
    """Build the new baseline, keeping reviewed reasons and flagging new files."""
    previous_files: dict[str, str] = previous.get("fork_only_files") or {}
    previous_diverged: dict[str, dict[str, Any]] = previous.get("diverged_files") or {}
    previous_removed: dict[str, str] = previous.get("removed_upstream_files") or {}

    return {
        "comment": (
            "Reviewed record of how this fork differs from upstream. "
            "Regenerate with testing-scripts/check_upstream_parity.py --update "
            "after auditing, and write a reason for every entry: the checker "
            "fails on an empty reason on purpose."
        ),
        "upstream_repo": previous.get("upstream_repo", UPSTREAM_URL),
        "upstream_commit": state.upstream_commit,
        "fork_only_files": {
            path: previous_files.get(path, "") for path in state.fork_only_files
        },
        "diverged_files": {
            path: {
                "reason": (previous_diverged.get(path) or {}).get("reason", ""),
                "missing_lines": diverged.missing_count,
                "missing_digest": diverged.missing_digest,
                "fork_only_lines": diverged.fork_only_lines,
            }
            for path, diverged in sorted(state.diverged.items())
        },
        "removed_upstream_files": {
            path: previous_removed.get(path, "") for path in state.upstream_only_files
        },
    }


def report(state: State, verdict: Verdict, baseline: dict[str, Any]) -> str:
    lines = [
        f"upstream commit: {state.upstream_commit[:12]}",
        f"fork commit:     {state.fork_commit[:12]}",
        f"baseline:        {(baseline.get('upstream_commit') or 'none')[:12]}",
        "",
        f"fork-only files:   {len(state.fork_only_files)}",
        f"diverged files:    {len(state.diverged)}",
        f"upstream-only files: {len(state.upstream_only_files)}",
        "",
    ]

    for note in verdict.notes:
        lines.append(f"note: {note}")

    if verdict.ok:
        lines.append("")
        lines.append(
            "PASS: every difference from upstream is a reviewed, recorded entry."
        )
        return "\n".join(lines)

    for index, problem in enumerate(verdict.problems, start=1):
        lines.append("")
        lines.append(f"{index}. {problem.summary}")
        lines.extend(problem.detail)

    lines.append("")
    lines.append(
        f"FAIL: {len(verdict.problems)} unreviewed difference(s) from upstream."
    )
    return "\n".join(lines)


def show_file(
    repo: Path, state: State, upstream: str, fork: str, path: str, limit: int
) -> int:
    """Print the upstream lines this fork does not carry for one path."""
    if path in state.diverged:
        missing = state.diverged[path].missing_lines
    else:
        try:
            up_lines = to_lines(blob(repo, upstream, path))
            fork_lines = to_lines(blob(repo, fork, path))
        except ParityError:
            print(f"{path}: not present in both trees")
            return EXIT_OK

        if up_lines is None or fork_lines is None:
            print(f"{path}: no text divergence recorded")
            return EXIT_OK

        missing = missing_upstream_lines(up_lines, fork_lines)

    print(f"{path}: {len(missing)} upstream line(s) not carried here")
    for line in missing[:limit]:
        print(f"  {line}")
    if len(missing) > limit:
        print(f"  ... and {len(missing) - limit} more")
    if not missing:
        print("  (none: the trees differ only in lines this fork adds)")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check this fork's feature parity against upstream Frigate."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="repository root (defaults to the tree this script lives in)",
    )
    parser.add_argument(
        "--upstream",
        default=None,
        help=f"git revision to compare against (default {UPSTREAM_REF})",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="fetch upstream first: the baseline commit normally, the branch tip with --drift",
    )
    parser.add_argument(
        "--drift",
        action="store_true",
        help="fail when upstream's branch has moved past the baseline commit",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="write the baseline from the current trees (review first)",
    )
    parser.add_argument(
        "--show",
        metavar="PATH",
        action="append",
        default=[],
        help="print the upstream lines this fork does not carry (may repeat)",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help="baseline file (defaults to the one next to this script)",
    )
    parser.add_argument(
        "--upstream-url", default=UPSTREAM_URL, help="upstream repository URL"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=SAMPLE_LINES,
        help="how many lines to print per problem",
    )
    args = parser.parse_args(argv)

    try:
        repo = find_repo(args.repo) if args.repo else find_repo()
    except ParityError as err:
        print(f"error: {err}", file=sys.stderr)
        return EXIT_USAGE

    try:
        baseline = (
            load_baseline(args.baseline)
            if args.baseline.exists()
            else ({} if args.update else load_baseline(args.baseline))
        )
    except ParityError as err:
        print(f"error: {err}", file=sys.stderr)
        return EXIT_USAGE

    baseline_commit = baseline.get("upstream_commit")

    try:
        if args.upstream:
            upstream = resolve(repo, args.upstream)
        elif args.fetch and args.drift:
            upstream = fetch_upstream(
                repo,
                url=args.upstream_url,
                ref=UPSTREAM_REF,
                branch=UPSTREAM_BRANCH,
                depth=100,
            )
        elif args.fetch and baseline_commit:
            upstream = fetch_upstream(
                repo,
                url=args.upstream_url,
                ref=PINNED_REF,
                commit=baseline_commit,
                depth=1,
            )
        elif args.fetch:
            upstream = fetch_upstream(
                repo,
                url=args.upstream_url,
                ref=UPSTREAM_REF,
                branch=UPSTREAM_BRANCH,
                depth=1,
            )
        else:
            upstream = resolve(repo, UPSTREAM_REF)
        fork = resolve(repo, "HEAD")
    except ParityError as err:
        print(f"error: {err}", file=sys.stderr)
        return EXIT_USAGE

    if args.drift and baseline_commit and upstream != baseline_commit:
        print(
            "upstream has moved past the commit this fork was verified against:\n"
            f"  verified: {baseline_commit}\n"
            f"  upstream: {upstream}\n"
        )
        log = run_git(
            repo,
            "log",
            "--oneline",
            "--no-decorate",
            f"{baseline_commit}..{upstream}",
            check=False,
        ).strip()
        if log:
            carried = "\n".join(f"  {line}" for line in log.splitlines())
            print(f"commits not carried yet:\n{carried}")
        else:
            print("  (the new commit is not in this clone; fetch the range to list it)")
        print("\nSync upstream, then re-run with --update after reviewing the result.")
        return EXIT_PARITY_FAILURE

    try:
        state = build_state(repo, upstream, fork)
    except ParityError as err:
        print(f"error: {err}", file=sys.stderr)
        return EXIT_USAGE

    if args.show:
        for path in args.show:
            show_file(repo, state, upstream, fork, path, args.limit)
        return EXIT_OK

    if args.update:
        updated = baseline_from(state, baseline)
        args.baseline.write_text(
            json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        empty = (
            [
                path
                for path, reason in updated["fork_only_files"].items()
                if not reason.strip()
            ]
            + [
                path
                for path, entry in updated["diverged_files"].items()
                if not (entry.get("reason") or "").strip()
            ]
            + [
                path
                for path, reason in updated["removed_upstream_files"].items()
                if not reason.strip()
            ]
        )
        print(
            f"wrote {args.baseline} against upstream {state.upstream_commit[:12]} "
            f"({len(updated['fork_only_files'])} fork-only, "
            f"{len(updated['diverged_files'])} diverged)"
        )
        if empty:
            print(
                f"\n{len(empty)} entr(y/ies) still need a reason before the check passes:"
            )
            for path in empty[: args.limit]:
                print(f"  {path}")
            if len(empty) > args.limit:
                print(f"  ... and {len(empty) - args.limit} more")
        return EXIT_OK

    verdict = compare(state, baseline)
    print(report(state, verdict, baseline))
    return EXIT_OK if verdict.ok else EXIT_PARITY_FAILURE


if __name__ == "__main__":
    sys.exit(main())

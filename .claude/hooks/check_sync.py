"""Check that the repo's documents agree with the code and with each other.

Run by `/finish` before the pull request opens, and by CI on every pull request:

    python .claude/hooks/check_sync.py                     # the tree on its own
    python .claude/hooks/check_sync.py --base origin/main  # also: version raised

Checks:

- `pyproject.toml` has a `X.Y.Z` version.
- `README.md` states that version on its `**Version:**` line.
- `DEVLOG.md`'s newest entry is that version, and its entries are unique and
  newest first.
- `STRUCTURE.md` names every Python file under `src/`, `tests/` and
  `.claude/hooks/`, and names no Python file that does not exist.
- Every `brief.json`, `round.json` and diagnosis `state.json` under `development/`
  parses and uses known
  statuses.
- With `--base`, the version is above the base's: by exactly the bump this
  branch's brief names, or by any raise on a branch with no brief.

It is not a Stop hook on purpose: during a feature, `STRUCTURE.md`, the README and
the DEVLOG are written only at the end, by `/finish`, so mid-feature drift is
expected. Exit status 1 and one line per problem when anything fails. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from dev_state import (
    DEV_DIR,
    DIAGNOSIS_DIR,
    FEATURE_STATUSES,
    KINDS,
    ROUND_STATUSES,
    STEP_STATUSES,
    Brief,
    all_briefs,
    current_branch,
)

VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
README_VERSION_RE = re.compile(r"^\*\*Version:\*\*\s*`?([0-9.]+)`?\s*$", re.MULTILINE)
DEVLOG_ENTRY_RE = re.compile(r"^## ([0-9][0-9.]*) — \d{4}-\d{2}-\d{2}\b", re.MULTILINE)
STRUCTURE_PATH_RE = re.compile(r"`([\w./-]+\.py)`")
CODE_DIRS = ("src", "tests", ".claude/hooks")
BUMPS = ("major", "minor", "patch")

Version = tuple[int, int, int]


def parse_version(text: str) -> Version | None:
    """Parse a `X.Y.Z` version.

    Args:
        text: The version string.

    Returns:
        The three numbers, or None if `text` is not exactly `X.Y.Z`.
    """
    match = VERSION_RE.match(text.strip())
    if match is None:
        return None
    major, minor, patch = (int(g) for g in match.groups())
    return (major, minor, patch)


def bumped(version: Version, bump: str) -> Version:
    """Raise a version by one bump.

    Args:
        version: The version to raise.
        bump: `major`, `minor` or `patch`.

    Returns:
        The raised version.

    Raises:
        ValueError: If `bump` is not one of `BUMPS`.
    """
    major, minor, patch = version
    if bump == "major":
        return (major + 1, 0, 0)
    if bump == "minor":
        return (major, minor + 1, 0)
    if bump == "patch":
        return (major, minor, patch + 1)
    raise ValueError(f"bump must be one of {BUMPS}, got {bump!r}")


def project_version(pyproject_text: str) -> str:
    """Read `[project] version` from the text of a `pyproject.toml`.

    Args:
        pyproject_text: The file's contents.

    Returns:
        The version string, or "" if it is missing or the file is not TOML.
    """
    try:
        data = tomllib.loads(pyproject_text)
    except tomllib.TOMLDecodeError:
        return ""
    project = data.get("project")
    version = project.get("version") if isinstance(project, dict) else None
    return version if isinstance(version, str) else ""


def version_problems(project_dir: Path) -> list[str]:
    """Check the version agrees across pyproject, README and DEVLOG.

    Args:
        project_dir: Repository root.

    Returns:
        One line per problem; empty when they agree.
    """
    try:
        version = project_version((project_dir / "pyproject.toml").read_text("utf-8"))
    except OSError:
        return ["pyproject.toml is missing"]
    if parse_version(version) is None:
        return [f"pyproject.toml version must be X.Y.Z, got {version!r}"]
    problems: list[str] = []

    readme = _read(project_dir / "README.md")
    stated = README_VERSION_RE.findall(readme)
    if not stated:
        problems.append("README.md has no '**Version:** X.Y.Z' line")
    elif stated[0] != version:
        problems.append(f"README.md says version {stated[0]}, pyproject.toml {version}")

    entries = DEVLOG_ENTRY_RE.findall(_read(project_dir / "DEVLOG.md"))
    if not entries:
        problems.append("DEVLOG.md has no '## X.Y.Z — YYYY-MM-DD' entry")
        return problems
    if entries[0] != version:
        problems.append(
            f"DEVLOG.md's newest entry is {entries[0]}, pyproject.toml {version}"
        )
    parsed = [v for v in (parse_version(e) for e in entries) if v is not None]
    if len(parsed) != len(entries):
        problems.append("DEVLOG.md has an entry whose version is not X.Y.Z")
    elif len(set(parsed)) != len(parsed):
        problems.append("DEVLOG.md has the same version twice")
    elif parsed != sorted(parsed, reverse=True):
        problems.append("DEVLOG.md entries are not newest first")
    return problems


def structure_problems(project_dir: Path) -> list[str]:
    """Check `STRUCTURE.md` names exactly the Python files on disk.

    Args:
        project_dir: Repository root.

    Returns:
        One line per file missing from it or named in it but absent.
    """
    text = _read(project_dir / "STRUCTURE.md")
    if not text:
        return ["STRUCTURE.md is missing"]
    named = {
        p
        for p in STRUCTURE_PATH_RE.findall(text)
        if p.startswith(tuple(f"{d}/" for d in CODE_DIRS))
    }
    on_disk = {
        p.relative_to(project_dir).as_posix()
        for d in CODE_DIRS
        for p in (project_dir / d).rglob("*.py")
        if "__pycache__" not in p.parts
    }
    problems = [f"STRUCTURE.md does not describe {p}" for p in sorted(on_disk - named)]
    problems += [
        f"STRUCTURE.md describes {p}, which does not exist"
        for p in sorted(named - on_disk)
    ]
    return problems


def state_problems(project_dir: Path) -> list[str]:
    """Check every state file under `development/` parses with known statuses.

    Args:
        project_dir: Repository root.

    Returns:
        One line per broken file.
    """
    root = project_dir / DEV_DIR
    if not root.is_dir():
        return []
    problems: list[str] = []
    for path in sorted(root.rglob("*.json")):
        rel = path.relative_to(project_dir).as_posix()
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            problems.append(f"{rel} is not valid JSON: {exc}")
            continue
        if not isinstance(data, dict):
            problems.append(f"{rel} must hold a JSON object")
        elif path.name == "brief.json":
            problems += _brief_problems(rel, data)
        elif path.name == "round.json" or (
            path.name == "state.json" and path.parent.name == DIAGNOSIS_DIR
        ):
            if data.get("status") not in STEP_STATUSES:
                problems.append(f"{rel} has status {data.get('status')!r}")
            if not isinstance(data.get("step"), int):
                problems.append(f"{rel} has no integer step")
    return problems


def bump_problems(project_dir: Path, base: str) -> list[str]:
    """Check the version was raised above the base's by the brief's bump.

    The bump comes from this branch's own brief (see `branch_brief`). With none,
    any raise passes: a `chore/` branch is not a feature. Briefs of features merged
    earlier stay on `main` at status `review` and must never decide it.

    Args:
        project_dir: Repository root.
        base: A git ref to compare with, e.g. `origin/main`.

    Returns:
        One line per problem.
    """
    try:
        ours = project_version((project_dir / "pyproject.toml").read_text("utf-8"))
    except OSError:
        return []
    shown = subprocess.run(
        ["git", "show", f"{base}:pyproject.toml"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if shown.returncode != 0:
        return [f"cannot read pyproject.toml at {base}"]
    theirs = project_version(shown.stdout)
    new, old = parse_version(ours), parse_version(theirs)
    if new is None or old is None:
        return []  # version_problems reports a malformed version
    brief = branch_brief(project_dir, current_branch(project_dir))
    if brief is not None and brief.bump in BUMPS:
        expected = bumped(old, brief.bump)
        if new != expected:
            want = ".".join(map(str, expected))
            return [
                f"version is {ours}; a {brief.bump} bump from {base}'s {theirs} is {want}"
            ]
        return []
    if new <= old:
        return [f"version {ours} is not above {base}'s {theirs}"]
    return []


def branch_brief(project_dir: Path, branch: str) -> Brief | None:
    """The brief of the feature whose branch, or one of whose rounds, is `branch`.

    Args:
        project_dir: Repository root.
        branch: The checked-out branch.

    Returns:
        That brief, or None when no brief belongs to the branch.
    """
    if not branch:
        return None
    for brief in all_briefs(project_dir):
        if brief.branch == branch or any(r.branch == branch for r in brief.rounds):
            return brief
    return None


def problems(project_dir: Path, base: str = "") -> list[str]:
    """Run every check.

    Args:
        project_dir: Repository root.
        base: A git ref for the version-raise check, or "" to skip it.

    Returns:
        Every problem found.
    """
    found = version_problems(project_dir)
    found += structure_problems(project_dir)
    found += state_problems(project_dir)
    if base:
        found += bump_problems(project_dir, base)
    return found


def _read(path: Path) -> str:
    try:
        return path.read_text("utf-8")
    except OSError:
        return ""


def _brief_problems(rel: str, data: dict[str, object]) -> list[str]:
    found: list[str] = []
    if not isinstance(data.get("feature"), str) or not data.get("feature"):
        found.append(f"{rel} has no feature name")
    if data.get("status") not in FEATURE_STATUSES:
        found.append(f"{rel} has status {data.get('status')!r}")
    if data.get("kind", "feature") not in KINDS:
        found.append(f"{rel} has kind {data.get('kind')!r}")
    if data.get("bump") not in BUMPS:
        found.append(f"{rel} has bump {data.get('bump')!r}")
    rounds = data.get("rounds")
    if not isinstance(rounds, list):
        found.append(f"{rel} has no rounds list")
        return found
    for entry in rounds:
        if not isinstance(entry, dict) or entry.get("status") not in ROUND_STATUSES:
            found.append(f"{rel} has a round with a bad status: {entry!r}")
    return found


def main() -> None:
    """Entry point: print each problem and exit 1, or exit 0 silently."""
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else ""
    )
    parser.add_argument(
        "--base", default="", help="git ref whose version must be exceeded"
    )
    parser.add_argument("--root", default=".", help="repository root")
    args = parser.parse_args()
    found = problems(Path(args.root).resolve(), args.base)
    for line in found:
        print(line)
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()

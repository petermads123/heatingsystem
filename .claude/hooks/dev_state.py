"""Read the feature pipeline's state files: `brief.json` and `round.json`.

Every feature branch keeps its state under `development/<branch>/`:

    development/feat/csv-export/
      BRIEF.md                  the feature in prose, written by the main session
      brief.json                the feature's status and its rounds, for hooks and mods
      diagnosis/                a fix only: written by /fix's diagnose workflow
        log.md                  reproduction, candidate causes, root cause, critique
        state.json              the diagnosis's status and step, round.json's shape
      r1-writer-core/
        log.md                  the round's notepad: concept, plan, step notes
        round.json              the round's status and step, for hooks and mods

`brief.json` has exactly one writer, the main session, and `round.json` exactly one,
the round's own steps, so the two never conflict when a round branch merges back.
The schemas are documented in `development/README.md`; this module reads them
leniently, because a hook that crashes on a half-written file is worse than one
that reports nothing.

Shared by the other hooks, which import it by module name: Python puts a script's
own directory on `sys.path`. Stdlib only.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEV_DIR = "development"
SCHEMA = 1
GIT_TIMEOUT_SECONDS = 10

# Feature statuses in `brief.json`. Only the live ones are shown by mods and hooks;
# `review` is set in the commit that opens the pull request, so `main` never
# carries a live marker once it merges.
LIVE_STATUSES = ("diagnosis", "concept", "rounds", "final")
FEATURE_STATUSES = (*LIVE_STATUSES, "review", "parked")
ROUND_STATUSES = ("planned", "running", "failed", "merged")
STEP_STATUSES = ("running", "blocked", "failed", "done")
KINDS = ("feature", "fix")
DIAGNOSIS_DIR = "diagnosis"

# A fix's diagnosis steps, in order, repeated in its `state.json`.
DIAGNOSIS_STEPS = ("Reproduce", "Hunt", "Root cause", "Critique")

# The round's steps, in order. `round.json` repeats them so a mod needs no copy.
ROUND_STEPS = (
    "Conceptualize",
    "Plan",
    "Implement",
    "Verify",
    "Test design",
    "Write tests",
    "Run tests",
    "Concept check",
    "Close",
)


@dataclass(frozen=True)
class RoundEntry:
    """One round as `brief.json` lists it.

    Attributes:
        number: The round's number, from 1.
        name: Short display name, shown by mods.
        slug: Kebab-case name used in the round's folder and branch.
        branch: The round's own branch, e.g. `feat/csv-export--r1`.
        status: One of `ROUND_STATUSES`.
        summary: One line on what the round delivers.
    """

    number: int
    name: str
    slug: str
    branch: str
    status: str
    summary: str

    @property
    def folder(self) -> str:
        """The round's folder name inside the feature folder, e.g. `r1-writer-core`."""
        return f"r{self.number}-{self.slug}"


@dataclass(frozen=True)
class Brief:
    """A feature's `brief.json`.

    Attributes:
        path: The file it was read from.
        feature: Kebab-case feature name.
        title: Display name.
        branch: The feature branch, e.g. `feat/csv-export`.
        bump: `major`, `minor` or `patch`.
        status: One of `FEATURE_STATUSES`.
        summary: One line on what the feature is.
        current_round: The round in progress or next up, 0 before the first.
        rounds: Every planned round, in order.
        pr: The pull request URL once opened, else "".
        kind: `feature`, or `fix` for one `/fix` started; `feature` when absent.
    """

    path: Path
    feature: str
    title: str
    branch: str
    bump: str
    status: str
    summary: str
    current_round: int
    rounds: tuple[RoundEntry, ...]
    pr: str
    kind: str = "feature"

    @property
    def folder(self) -> Path:
        """The feature's folder, the one holding `brief.json`."""
        return self.path.parent

    @property
    def live(self) -> bool:
        """Whether the feature is still being built."""
        return self.status in LIVE_STATUSES

    def round_entry(self, number: int) -> RoundEntry | None:
        """Find a round by its number.

        Args:
            number: The round's number.

        Returns:
            The round, or None if the brief lists no such round.
        """
        return next((r for r in self.rounds if r.number == number), None)


@dataclass(frozen=True)
class RoundState:
    """A round's `round.json`.

    Attributes:
        path: The file it was read from.
        round: The round's number.
        name: Display name.
        branch: The round's branch.
        status: One of `STEP_STATUSES`: the state of the current step.
        step: The current step, 1-based into `steps`.
        steps: The step names, normally `ROUND_STEPS`.
        attempt: Fix attempts spent on the current gate, from 0.
        question: The open question when `status` is `blocked`, else "".
        note: One line on what the step is doing or just did.
    """

    path: Path
    round: int
    name: str
    branch: str
    status: str
    step: int
    steps: tuple[str, ...]
    attempt: int
    question: str
    note: str

    @property
    def step_name(self) -> str:
        """The current step's name, or "" if `step` is out of range."""
        return self.steps[self.step - 1] if 1 <= self.step <= len(self.steps) else ""


def _load(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) else ""


def _int(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _round_entry(data: object) -> RoundEntry | None:
    if not isinstance(data, dict):
        return None
    number = _int(data, "n")
    if number < 1:
        return None
    return RoundEntry(
        number=number,
        name=_str(data, "name"),
        slug=_str(data, "slug"),
        branch=_str(data, "branch"),
        status=_str(data, "status"),
        summary=_str(data, "summary"),
    )


def read_brief(path: Path) -> Brief | None:
    """Parse one `brief.json`.

    Args:
        path: The file to read.

    Returns:
        The brief, or None if the file is missing, not JSON, or has no feature name.
    """
    data = _load(path)
    if data is None or not _str(data, "feature"):
        return None
    raw_rounds = data.get("rounds")
    rounds = tuple(
        entry
        for entry in (
            _round_entry(r)
            for r in (raw_rounds if isinstance(raw_rounds, list) else [])
        )
        if entry is not None
    )
    return Brief(
        path=path,
        feature=_str(data, "feature"),
        title=_str(data, "title"),
        branch=_str(data, "branch"),
        bump=_str(data, "bump"),
        status=_str(data, "status"),
        summary=_str(data, "summary"),
        current_round=_int(data, "current_round"),
        rounds=rounds,
        pr=_str(data, "pr"),
        kind=_str(data, "kind") or "feature",
    )


def read_round(path: Path) -> RoundState | None:
    """Parse one `round.json`.

    Args:
        path: The file to read.

    Returns:
        The round's state, or None if the file is missing, not JSON, or has no
        round number.
    """
    data = _load(path)
    if data is None or _int(data, "round") < 1:
        return None
    return _state(path, data, ROUND_STEPS)


def read_diagnosis(path: Path) -> RoundState | None:
    """Parse a fix's diagnosis `state.json`, which has `round.json`'s shape.

    Args:
        path: The file to read.

    Returns:
        The diagnosis's state with `round` 0, or None if the file is missing or
        not a JSON object.
    """
    data = _load(path)
    if data is None:
        return None
    return _state(path, {**data, "round": 0}, DIAGNOSIS_STEPS)


def _state(
    path: Path, data: dict[str, object], default_steps: tuple[str, ...]
) -> RoundState:
    raw_steps = data.get("steps")
    steps = (
        tuple(s for s in raw_steps if isinstance(s, str))
        if isinstance(raw_steps, list)
        else default_steps
    )
    return RoundState(
        path=path,
        round=_int(data, "round"),
        name=_str(data, "name"),
        branch=_str(data, "branch"),
        status=_str(data, "status"),
        step=_int(data, "step"),
        steps=steps or default_steps,
        attempt=_int(data, "attempt"),
        question=_str(data, "question"),
        note=_str(data, "note"),
    )


def all_briefs(project_dir: Path) -> list[Brief]:
    """Every parseable `brief.json` under `development/`.

    Args:
        project_dir: Repository root.

    Returns:
        The briefs, most recently modified first.
    """
    root = project_dir / DEV_DIR
    if not root.is_dir():
        return []
    paths = sorted(
        root.rglob("brief.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return [b for b in (read_brief(p) for p in paths) if b is not None]


def active_brief(project_dir: Path, branch: str = "") -> Brief | None:
    """The feature being built in this checkout.

    A checkout normally holds one live brief: its own feature's. When it holds
    more, the one whose feature branch or one of whose round branches is checked
    out wins, then the most recently modified.

    Args:
        project_dir: Repository root.
        branch: The checked-out branch, or "" to ask git.

    Returns:
        The live brief, or None when nothing is being built.
    """
    live = [b for b in all_briefs(project_dir) if b.live]
    if not live:
        return None
    branch = branch or current_branch(project_dir)
    for brief in live:
        if branch and (
            branch == brief.branch or any(r.branch == branch for r in brief.rounds)
        ):
            return brief
    return live[0]


def round_state(brief: Brief, number: int) -> RoundState | None:
    """Read the `round.json` of one round of a feature.

    Args:
        brief: The feature.
        number: The round's number.

    Returns:
        The round's state, or None if the round or its file does not exist yet.
    """
    entry = brief.round_entry(number)
    if entry is None:
        return None
    return read_round(brief.folder / entry.folder / "round.json")


def diagnosis_state(brief: Brief) -> RoundState | None:
    """Read a fix's diagnosis state.

    Args:
        brief: The feature.

    Returns:
        The diagnosis's state, or None when the feature has none (any feature
        that is not a fix, or a fix before its diagnosis starts).
    """
    return read_diagnosis(brief.folder / DIAGNOSIS_DIR / "state.json")


def git_lines(project_dir: Path, args: list[str]) -> list[str]:
    """Run a git command and return its non-empty output lines.

    Args:
        project_dir: Repository root.
        args: Git arguments, without the leading "git".

    Returns:
        Output lines, or an empty list if git failed or was unavailable.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def current_branch(project_dir: Path) -> str:
    """Name the branch currently checked out.

    Args:
        project_dir: Repository root.

    Returns:
        The branch name, or "" on a detached HEAD or if git could not say.
    """
    lines = git_lines(project_dir, ["branch", "--show-current"])
    return lines[0].strip() if lines else ""


def main() -> None:
    """Entry point: print the live feature and its current round."""
    project_dir = Path.cwd()
    brief = active_brief(project_dir)
    if brief is None:
        print("No feature in flight.")
        return
    print(f"{brief.title} [{brief.branch}] {brief.kind} status={brief.status}")
    diagnosis = diagnosis_state(brief)
    if diagnosis is not None and brief.status == "diagnosis":
        print(
            f"diagnosis step {diagnosis.step}/{len(diagnosis.steps)}"
            f" {diagnosis.step_name} ({diagnosis.status})"
        )
    for entry in brief.rounds:
        print(f"  round {entry.number}: {entry.name} ({entry.status})")
    state = round_state(brief, brief.current_round)
    if state is not None:
        print(
            f"current: round {state.round} step {state.step}/{len(state.steps)}"
            f" {state.step_name} ({state.status})"
        )


if __name__ == "__main__":
    main()

"""Pytest plugin: writes a run's live progress to a file a mod can draw as a bar.

Loaded for every pytest run by `-p pytest_progress` in `pyproject.toml`'s `addopts`. While tests run it keeps
`.pytest_cache/progress.json` under the root directory current: how many tests were
collected, how many are done, passed, failed and skipped, the test running now, and whether
the run has finished. The `feature-band` mod polls that file and shows the bar above the
prompt. The file is never committed: `.pytest_cache/` is ignored.

Writing is best-effort. A file that cannot be written never fails or slows a test run.

Stdlib only at runtime; pytest is imported for type checking alone.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

PROGRESS_FILE = ".pytest_cache/progress.json"
"""Where the progress is written, relative to pytest's root directory."""

WRITE_INTERVAL = 0.2
"""Seconds between writes while tests run; the start and the end are always written."""

_PLUGIN_NAME = "pytest_progress_recorder"


@dataclass
class Progress:
    """One run's counts, as written to the progress file.

    Attributes:
        pid: The pytest process.
        started: When the run started, in epoch seconds.
        updated: When this state was written, in epoch seconds.
        finished: Whether the run has ended.
        total: Tests collected for the run.
        done: Tests that have finished, whatever their outcome.
        passed: Tests that passed.
        failed: Tests that failed, including errors in setup or teardown.
        skipped: Tests skipped or expected to fail.
        current: The id of the test running now, or the last one run.
        exitstatus: Pytest's exit status once finished, else None.
    """

    pid: int = 0
    started: float = 0.0
    updated: float = 0.0
    finished: bool = False
    total: int = 0
    done: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    current: str = ""
    exitstatus: int | None = None


@dataclass
class Recorder:
    """Counts a run's outcomes and writes them to the progress file.

    Registered as a pytest plugin by `pytest_configure`; its `pytest_*` methods are the
    hooks.

    Attributes:
        path: The progress file.
        progress: The current counts.
    """

    path: Path
    progress: Progress = field(default_factory=Progress)
    _outcomes: dict[str, str] = field(default_factory=dict)
    _written: float = 0.0

    def record(self, nodeid: str, outcome: str, *, call: bool) -> None:
        """Count one phase report of one test; a test's worst outcome wins.

        Args:
            nodeid: The test's id.
            outcome: The report's outcome: `passed`, `failed` or `skipped`.
            call: Whether the report is for the test's call phase rather than its setup
                or teardown. A passing setup or teardown says nothing about the test.
        """
        before = self._outcomes.get(nodeid, "")
        worse = outcome == "failed" or (outcome == "skipped" and before != "failed")
        first_pass = outcome == "passed" and call and not before
        if worse or first_pass:
            self._outcomes[nodeid] = outcome
        values = list(self._outcomes.values())
        self.progress.passed = values.count("passed")
        self.progress.failed = values.count("failed")
        self.progress.skipped = values.count("skipped")

    def write(self, *, force: bool = False) -> None:
        """Write the progress file, at most once per `WRITE_INTERVAL` unless forced.

        Args:
            force: Write now, whatever was written last.
        """
        now = time.time()
        if not force and now - self._written < WRITE_INTERVAL:
            return
        self._written = now
        self.progress.updated = now
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(asdict(self.progress)), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            # Progress is a courtesy: a read-only tree or a reader holding the file open
            # (Windows) must never fail the run.
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        """Start the run's record once pytest knows how many tests it holds."""
        self._outcomes.clear()
        self.progress = Progress(
            pid=os.getpid(), started=time.time(), total=len(session.items)
        )
        self.write(force=True)

    def pytest_runtest_logstart(self, nodeid: str) -> None:
        """Name the test that is starting."""
        self.progress.current = nodeid
        self.write()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        """Count one phase of one test."""
        self.record(report.nodeid, report.outcome, call=report.when == "call")

    def pytest_runtest_logfinish(self, nodeid: str) -> None:
        """Count a test as done."""
        self.progress.current = nodeid
        self.progress.done = min(self.progress.done + 1, self.progress.total)
        self.write()

    def pytest_sessionfinish(self, exitstatus: int) -> None:
        """Mark the run finished."""
        self.progress.finished = True
        self.progress.exitstatus = int(exitstatus)
        self.write(force=True)


def read_progress(path: Path) -> Progress | None:
    """Read a progress file leniently.

    Args:
        path: The progress file.

    Returns:
        The progress, or None when the file is missing or unusable.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Progress(**raw) if isinstance(raw, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def pytest_configure(config: pytest.Config) -> None:
    """Register the recorder for a real test run.

    Not for `--collect-only`, which runs nothing, and not in an xdist worker: the
    controller sees every worker's reports and writes the one file.
    """
    if config.getoption("collectonly", default=False) or hasattr(config, "workerinput"):
        return
    if config.pluginmanager.has_plugin(_PLUGIN_NAME):
        return
    config.pluginmanager.register(
        Recorder(path=config.rootpath / PROGRESS_FILE), _PLUGIN_NAME
    )


def main() -> None:
    """Print the last run's progress in this directory, or that there is none."""
    progress = read_progress(Path.cwd() / PROGRESS_FILE)
    if progress is None:
        print(f"no progress file at {PROGRESS_FILE}", file=sys.stderr)
        return
    state = "finished" if progress.finished else "running"
    print(
        f"{state}: {progress.done}/{progress.total} done, {progress.passed} passed, "
        f"{progress.failed} failed, {progress.skipped} skipped"
    )


if __name__ == "__main__":
    main()

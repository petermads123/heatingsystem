import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_progress
from pytest_progress import PROGRESS_FILE, Progress, Recorder, read_progress

HOOKS = Path(pytest_progress.__file__).parent

SUITE = """
import pytest


def test_passes():
    assert True


def test_fails():
    assert 1 == 2


@pytest.mark.skip(reason="not today")
def test_skipped():
    assert True


@pytest.fixture
def broken():
    raise RuntimeError("setup broke")


def test_errors_in_setup(broken):
    assert True


@pytest.mark.xfail
def test_expected_to_fail():
    assert 1 == 2
"""


def run_pytest(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run pytest in a throwaway project with the plugin loaded as `-p`."""
    env = {**os.environ, "PYTHONPATH": str(HOOKS)}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "pytest_progress",
            "--rootdir",
            str(root),
            *args,
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def project(tmp_path: Path, suite: str = SUITE) -> Path:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (tmp_path / "test_sample.py").write_text(suite)
    return tmp_path


def test_record_counts_each_test_once_by_its_worst_outcome(tmp_path: Path) -> None:
    recorder = Recorder(path=tmp_path / "progress.json")
    recorder.record("a", "passed", call=False)  # setup
    assert recorder.progress.passed == 0
    recorder.record("a", "passed", call=True)
    recorder.record("a", "failed", call=False)  # teardown fails after a passing call
    recorder.record("b", "passed", call=True)
    recorder.record("c", "skipped", call=False)
    recorder.record("d", "failed", call=True)
    recorder.record("d", "skipped", call=False)  # never downgrades a failure
    assert (
        recorder.progress.passed,
        recorder.progress.failed,
        recorder.progress.skipped,
    ) == (1, 2, 1)


def test_write_is_throttled_unless_forced(tmp_path: Path) -> None:
    recorder = Recorder(path=tmp_path / "deep" / "progress.json")
    recorder.progress.total = 3
    recorder.write(force=True)
    recorder.progress.done = 1
    recorder.write()
    first = read_progress(recorder.path)
    assert first is not None
    assert first.done == 0
    recorder.write(force=True)
    second = read_progress(recorder.path)
    assert second is not None
    assert second.done == 1
    assert [p.name for p in recorder.path.parent.iterdir()] == ["progress.json"]


def test_write_never_raises_when_the_file_cannot_be_written(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the folder should be")
    recorder = Recorder(path=blocker / "progress.json")
    recorder.write(force=True)
    assert read_progress(recorder.path) is None


@pytest.mark.parametrize("content", ["", "{", "[1, 2]", '{"unknown": 1}'])
def test_read_progress_is_none_for_an_unusable_file(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "progress.json"
    path.write_text(content)
    assert read_progress(path) is None


def test_read_progress_is_none_for_a_missing_file(tmp_path: Path) -> None:
    assert read_progress(tmp_path / "absent.json") is None


def test_a_run_ends_with_its_counts_in_the_file(tmp_path: Path) -> None:
    root = project(tmp_path)
    result = run_pytest(root)
    assert result.returncode == 1, result.stdout + result.stderr
    progress = read_progress(root / PROGRESS_FILE)
    assert progress == Progress(
        pid=progress.pid if progress else 0,
        started=progress.started if progress else 0,
        updated=progress.updated if progress else 0,
        finished=True,
        total=5,
        done=5,
        passed=1,
        failed=2,
        skipped=2,
        current="test_sample.py::test_expected_to_fail",
        exitstatus=1,
    )
    assert progress is not None
    assert progress.pid > 0
    assert progress.started <= progress.updated


def test_a_green_run_exits_zero_in_the_file(tmp_path: Path) -> None:
    root = project(tmp_path, "def test_ok():\n    assert True\n")
    assert run_pytest(root).returncode == 0
    progress = read_progress(root / PROGRESS_FILE)
    assert progress is not None
    assert (
        progress.finished,
        progress.total,
        progress.passed,
        progress.exitstatus,
    ) == (
        True,
        1,
        1,
        0,
    )


def test_the_file_is_live_while_tests_run(tmp_path: Path) -> None:
    # The second test reads the file the plugin wrote when the first one finished.
    suite = (
        "import json, time\n"
        "from pathlib import Path\n\n"
        "def test_first():\n"
        f"    time.sleep({pytest_progress.WRITE_INTERVAL * 2})\n\n"
        "def test_second():\n"
        f"    seen = json.loads(Path({PROGRESS_FILE!r}).read_text())\n"
        "    Path('seen.json').write_text(json.dumps(seen))\n"
    )
    root = project(tmp_path, suite)
    result = run_pytest(root)
    assert result.returncode == 0, result.stdout + result.stderr
    seen = json.loads((root / "seen.json").read_text())
    assert (seen["finished"], seen["total"], seen["done"], seen["passed"]) == (
        False,
        2,
        1,
        1,
    )


def test_collect_only_writes_nothing(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert run_pytest(root, "--collect-only").returncode == 0
    assert not (root / PROGRESS_FILE).exists()


def test_a_deselected_test_is_not_in_the_total(tmp_path: Path) -> None:
    root = project(tmp_path)
    run_pytest(root, "-k", "passes or skipped")
    progress = read_progress(root / PROGRESS_FILE)
    assert progress is not None
    assert (progress.total, progress.done, progress.passed, progress.skipped) == (
        2,
        2,
        1,
        1,
    )


def test_every_run_in_this_repo_loads_the_plugin(
    pytestconfig: pytest.Config,
) -> None:
    pyproject = (HOOKS.parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert "-p pytest_progress" in pyproject
    assert pytestconfig.pluginmanager.has_plugin("pytest_progress")


def test_main_reports_the_last_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    pytest_progress.main()
    assert "no progress file" in capsys.readouterr().err
    recorder = Recorder(path=tmp_path / PROGRESS_FILE)
    recorder.progress = Progress(total=4, done=3, passed=2, failed=1)
    recorder.write(force=True)
    pytest_progress.main()
    assert (
        capsys.readouterr().out.strip()
        == "running: 3/4 done, 2 passed, 1 failed, 0 skipped"
    )

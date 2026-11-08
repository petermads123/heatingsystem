import json
import subprocess
from pathlib import Path

import pytest
from check_sync import (
    bump_problems,
    bumped,
    parse_version,
    problems,
    project_version,
    state_problems,
    structure_problems,
    version_problems,
)

PYPROJECT = '[project]\nname = "pkg"\nversion = "{v}"\n'


def make_repo(root: Path, version: str = "1.2.3", devlog: str | None = None) -> Path:
    (root / "pyproject.toml").write_text(PYPROJECT.format(v=version), encoding="utf-8")
    (root / "README.md").write_text(
        f"# pkg\n\n**Version:** {version}\n", encoding="utf-8"
    )
    if devlog is None:
        devlog = f"# Devlog\n\n## {version} — 2026-11-04\n\nStuff.\n\n## 1.0.0 — 2026-01-01\n"
    (root / "DEVLOG.md").write_text(devlog, encoding="utf-8")
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "src" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "STRUCTURE.md").write_text("### `src/pkg/__init__.py`\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1.2.3", (1, 2, 3)), ("0.0.0", (0, 0, 0)), (" 10.20.30 ", (10, 20, 30))],
)
def test_parse_version_accepts_x_y_z(text: str, expected: tuple[int, int, int]) -> None:
    assert parse_version(text) == expected


@pytest.mark.parametrize(
    "text", ["", "1.2", "1.2.3.4", "v1.2.3", "01.2.3", "1.2.3-rc1", "a.b.c"]
)
def test_parse_version_rejects_other_shapes(text: str) -> None:
    assert parse_version(text) is None


@pytest.mark.parametrize(
    ("bump", "expected"),
    [("major", (2, 0, 0)), ("minor", (1, 3, 0)), ("patch", (1, 2, 4))],
)
def test_bumped(bump: str, expected: tuple[int, int, int]) -> None:
    assert bumped((1, 2, 3), bump) == expected


def test_bumped_rejects_unknown_bump() -> None:
    with pytest.raises(ValueError, match="'huge'"):
        bumped((1, 2, 3), "huge")


def test_project_version_reads_and_tolerates_bad_toml() -> None:
    assert project_version(PYPROJECT.format(v="0.4.0")) == "0.4.0"
    assert project_version("not = [toml") == ""
    assert project_version("[tool.x]\n") == ""


def test_a_consistent_repo_has_no_problems(tmp_path: Path) -> None:
    assert problems(make_repo(tmp_path)) == []


def test_readme_version_mismatch(tmp_path: Path) -> None:
    make_repo(tmp_path)
    (tmp_path / "README.md").write_text("**Version:** 1.2.2\n", encoding="utf-8")
    assert version_problems(tmp_path) == [
        "README.md says version 1.2.2, pyproject.toml 1.2.3"
    ]


def test_readme_without_version_line(tmp_path: Path) -> None:
    make_repo(tmp_path)
    (tmp_path / "README.md").write_text("# pkg\n", encoding="utf-8")
    assert any("no '**Version:**" in p for p in version_problems(tmp_path))


@pytest.mark.parametrize(
    ("devlog", "needle"),
    [
        ("# Devlog\n", "no '## X.Y.Z"),
        ("## 1.2.2 — 2026-11-04\n", "newest entry is 1.2.2"),
        ("## 1.2.3 — 2026-11-04\n## 1.2.3 — 2026-11-03\n", "same version twice"),
        ("## 1.2.3 — 2026-11-04\n## 1.3.0 — 2026-11-03\n", "not newest first"),
    ],
)
def test_devlog_problems(tmp_path: Path, devlog: str, needle: str) -> None:
    make_repo(tmp_path, devlog=devlog)
    assert any(needle in p for p in version_problems(tmp_path))


def test_malformed_project_version(tmp_path: Path) -> None:
    make_repo(tmp_path, version="1.2")
    assert version_problems(tmp_path) == [
        "pyproject.toml version must be X.Y.Z, got '1.2'"
    ]


def test_missing_pyproject(tmp_path: Path) -> None:
    assert version_problems(tmp_path) == ["pyproject.toml is missing"]


def test_structure_drift_both_ways(tmp_path: Path) -> None:
    make_repo(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_new.py").write_text("", encoding="utf-8")
    (tmp_path / "STRUCTURE.md").write_text(
        "`src/pkg/__init__.py` and `src/pkg/gone.py`, also `docs/ignored.py`\n",
        encoding="utf-8",
    )
    assert structure_problems(tmp_path) == [
        "STRUCTURE.md does not describe tests/test_new.py",
        "STRUCTURE.md describes src/pkg/gone.py, which does not exist",
    ]


def test_structure_ignores_pycache(tmp_path: Path) -> None:
    make_repo(tmp_path)
    cache = tmp_path / "src" / "pkg" / "__pycache__"
    cache.mkdir()
    (cache / "junk.py").write_text("", encoding="utf-8")
    assert structure_problems(tmp_path) == []


def test_state_problems_flags_bad_files(tmp_path: Path) -> None:
    folder = tmp_path / "development" / "feat" / "x"
    (folder / "r1-a").mkdir(parents=True)
    (folder / "brief.json").write_text(
        json.dumps(
            {
                "feature": "x",
                "status": "weird",
                "bump": "minor",
                "rounds": [{"status": "nope"}],
            }
        ),
        encoding="utf-8",
    )
    (folder / "r1-a" / "round.json").write_text("{", encoding="utf-8")
    found = state_problems(tmp_path)
    assert any("status 'weird'" in p for p in found)
    assert any("bad status" in p for p in found)
    assert any("not valid JSON" in p for p in found)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo_with_base(tmp_path: Path) -> Path:
    make_repo(tmp_path)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "-c", "user.email=a@b", "-c", "user.name=a", "add", ".")
    _git(
        tmp_path,
        "-c",
        "user.email=a@b",
        "-c",
        "user.name=a",
        "commit",
        "-q",
        "-m",
        "base",
    )
    return tmp_path


def test_bump_unchanged_version_is_a_problem(repo_with_base: Path) -> None:
    assert bump_problems(repo_with_base, "main") == [
        "version 1.2.3 is not above main's 1.2.3"
    ]


def _brief(root: Path, branch: str, bump: str) -> None:
    folder = root / "development" / branch
    folder.mkdir(parents=True)
    data = {
        "feature": branch.split("/")[-1],
        "branch": branch,
        "status": "review",
        "bump": bump,
        "rounds": [{"n": 1, "branch": f"{branch}--r1", "status": "merged"}],
    }
    (folder / "brief.json").write_text(json.dumps(data), encoding="utf-8")


def _set_version(root: Path, version: str) -> None:
    (root / "pyproject.toml").write_text(PYPROJECT.format(v=version), encoding="utf-8")


def test_bump_must_match_this_branchs_brief(repo_with_base: Path) -> None:
    _git(repo_with_base, "checkout", "-q", "-b", "feat/x")
    _brief(repo_with_base, "feat/x", "minor")
    _set_version(repo_with_base, "1.2.4")
    assert bump_problems(repo_with_base, "main") == [
        "version is 1.2.4; a minor bump from main's 1.2.3 is 1.3.0"
    ]
    _set_version(repo_with_base, "1.3.0")
    assert bump_problems(repo_with_base, "main") == []


def test_bump_follows_the_brief_from_a_round_branch(repo_with_base: Path) -> None:
    _git(repo_with_base, "checkout", "-q", "-b", "feat/x--r1")
    _brief(repo_with_base, "feat/x", "major")
    _set_version(repo_with_base, "2.0.0")
    assert bump_problems(repo_with_base, "main") == []


def test_bump_ignores_other_features_briefs(repo_with_base: Path) -> None:
    _git(repo_with_base, "checkout", "-q", "-b", "chore/tidy")
    _brief(repo_with_base, "feat/old", "major")
    _set_version(repo_with_base, "1.2.4")
    assert bump_problems(repo_with_base, "main") == []


def test_bump_with_unknown_base(repo_with_base: Path) -> None:
    assert bump_problems(repo_with_base, "nope") == [
        "cannot read pyproject.toml at nope"
    ]


def test_state_problems_checks_kind_and_diagnosis_state(tmp_path: Path) -> None:
    folder = tmp_path / "development" / "fix" / "x"
    (folder / "diagnosis").mkdir(parents=True)
    (folder / "brief.json").write_text(
        json.dumps(
            {
                "feature": "x",
                "status": "diagnosis",
                "bump": "patch",
                "kind": "bug",
                "rounds": [],
            }
        ),
        encoding="utf-8",
    )
    (folder / "diagnosis" / "state.json").write_text(
        json.dumps({"status": "thinking", "step": 1}), encoding="utf-8"
    )
    found = state_problems(tmp_path)
    assert any("kind 'bug'" in p for p in found)
    assert any("state.json has status 'thinking'" in p for p in found)

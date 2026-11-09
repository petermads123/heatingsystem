import subprocess
import sys
from pathlib import Path

import pytest
from select_tests import Selection, changed_files, select_tests, selection

HOOK = Path(__file__).parents[1] / ".claude" / "hooks" / "select_tests.py"

PYPROJECT = """\
[project]
name = "pkg"
version = "1.0.0"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src", ".claude/hooks"]
"""

# A small repo in the template's layout: a package with a subpackage, tests named after
# their modules, a helper chain (cli -> core -> layout), and two hooks where one imports
# the other.
FILES = {
    "pyproject.toml": PYPROJECT,
    "README.md": "# pkg\n",
    "src/pkg/__init__.py": '"""The package."""\n',
    "src/pkg/layout.py": '"""Layout."""\n\nWIDTH = 3\n',
    "src/pkg/core.py": '"""Core."""\n\nfrom pkg.layout import WIDTH\n',
    "src/pkg/cli.py": '"""CLI."""\n\nfrom . import core\n',
    "src/pkg/lonely.py": '"""Nobody imports this."""\n',
    "src/pkg/reading/__init__.py": '"""Reading."""\n',
    "src/pkg/reading/loader.py": '"""Loader."""\n\nfrom ..layout import WIDTH\n',
    "tests/test_layout.py": "from pkg.layout import WIDTH\n",
    "tests/test_core.py": "import pkg.core\n",
    "tests/test_cli.py": "from pkg import cli\n",
    "tests/test_reading_loader.py": "from pkg.reading import loader\n",
    "tests/test_lonely.py": "x = 1\n",
    "tests/test_unrelated.py": "x = 1\n",
    ".claude/hooks/base_hook.py": '"""Base."""\n',
    ".claude/hooks/top_hook.py": '"""Top."""\n\nfrom base_hook import main\n',
    "tests/test_base_hook.py": "import base_hook\n",
    "tests/test_top_hook.py": "import top_hook\n",
}
TOTAL = sum(1 for name in FILES if name.startswith("tests/test_"))


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout


def write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write(tmp_path, FILES)
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "T")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "checkout", "-q", "-b", "feat/x")
    return tmp_path


def commit(root: Path, files: dict[str, str], message: str = "change") -> None:
    write(root, files)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


# --- Rule 2 and 3: changed tests, and tests named after a module -------------------


def test_changed_test_file_is_selected(repo: Path) -> None:
    assert select_tests(repo, ["tests/test_unrelated.py"]) == [
        "tests/test_unrelated.py"
    ]


def test_deleted_test_file_selects_nothing(repo: Path) -> None:
    (repo / "tests" / "test_unrelated.py").unlink()
    assert select_tests(repo, ["tests/test_unrelated.py"]) == []


def test_module_selects_its_named_test_file(repo: Path) -> None:
    assert select_tests(repo, ["src/pkg/lonely.py"]) == ["tests/test_lonely.py"]


def test_module_selects_a_package_prefixed_test_file(repo: Path) -> None:
    assert select_tests(repo, ["src/pkg/reading/loader.py"]) == [
        "tests/test_reading_loader.py"
    ]


def test_suffix_test_pattern_counts_as_a_test(repo: Path) -> None:
    write(repo, {"tests/layout_test.py": "import pkg.layout\n"})
    assert "tests/layout_test.py" in (select_tests(repo, ["src/pkg/layout.py"]) or [])


# --- Rule 4: importers, transitively -----------------------------------------------


def test_direct_importer_is_selected(repo: Path) -> None:
    assert select_tests(repo, ["src/pkg/core.py"]) == [
        "tests/test_cli.py",
        "tests/test_core.py",
    ]


def test_transitive_importer_is_selected(repo: Path) -> None:
    # layout <- core <- cli <- test_cli; layout <- reading.loader <- test_reading_loader
    assert select_tests(repo, ["src/pkg/layout.py"]) == [
        "tests/test_cli.py",
        "tests/test_core.py",
        "tests/test_layout.py",
        "tests/test_reading_loader.py",
    ]


def test_relative_import_two_levels_up_is_resolved(repo: Path) -> None:
    chosen = select_tests(repo, ["src/pkg/layout.py"]) or []
    assert "tests/test_reading_loader.py" in chosen


def test_import_inside_a_function_counts(repo: Path) -> None:
    write(repo, {"tests/test_unrelated.py": "def f():\n    import pkg.lonely\n"})
    assert "tests/test_unrelated.py" in (
        select_tests(repo, ["src/pkg/lonely.py"]) or []
    )


@pytest.mark.parametrize(
    "source",
    [
        'import importlib\nimportlib.import_module("pkg.lonely")\n',
        'import runpy\nrunpy.run_module("pkg.lonely")\n',
        'MODULES = ["pkg.lonely"]\n',
        'TARGET = "pkg.lonely.main"\n',
        'PATH = "src/pkg/lonely.py"\n',
    ],
)
def test_module_named_in_a_string_counts(repo: Path, source: str) -> None:
    write(repo, {"tests/test_unrelated.py": source})
    assert "tests/test_unrelated.py" in (
        select_tests(repo, ["src/pkg/lonely.py"]) or []
    )


def test_prose_naming_a_module_does_not_count(repo: Path) -> None:
    write(repo, {"tests/test_unrelated.py": 'DOC = "see pkg.lonely for more"\n'})
    assert select_tests(repo, ["src/pkg/lonely.py"]) == ["tests/test_lonely.py"]


def test_a_test_importing_another_test_follows_it(repo: Path) -> None:
    write(repo, {"tests/test_unrelated.py": "from test_layout import WIDTH\n"})
    chosen = select_tests(repo, ["src/pkg/layout.py"]) or []
    assert "tests/test_unrelated.py" in chosen


def test_import_cycle_terminates(repo: Path) -> None:
    write(
        repo,
        {
            "src/pkg/a.py": "from pkg import b\n",
            "src/pkg/b.py": "from pkg import a\n",
            "tests/test_unrelated.py": "import pkg.b\n",
        },
    )
    assert select_tests(repo, ["src/pkg/a.py"]) == ["tests/test_unrelated.py"]


def test_deleted_module_selects_its_importers(repo: Path) -> None:
    (repo / "src" / "pkg" / "layout.py").unlink()
    chosen = select_tests(repo, ["src/pkg/layout.py"]) or []
    assert {"tests/test_layout.py", "tests/test_core.py"} <= set(chosen)


def test_renamed_module_selects_importers_of_both_names(repo: Path) -> None:
    git(repo, "mv", "src/pkg/layout.py", "src/pkg/grid.py")
    write(repo, {"tests/test_unrelated.py": "import pkg.grid\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "rename")
    changed = changed_files(repo, "main")
    assert {"src/pkg/layout.py", "src/pkg/grid.py"} <= set(changed)
    chosen = select_tests(repo, changed) or []
    assert {"tests/test_layout.py", "tests/test_unrelated.py"} <= set(chosen)


def test_unrelated_tests_are_not_selected(repo: Path) -> None:
    chosen = select_tests(repo, ["src/pkg/layout.py"]) or []
    assert "tests/test_unrelated.py" not in chosen
    assert "tests/test_base_hook.py" not in chosen


# --- Rule 5 (hooks) -----------------------------------------------------------------


def test_hook_selects_its_test_and_the_tests_of_hooks_importing_it(repo: Path) -> None:
    assert select_tests(repo, [".claude/hooks/base_hook.py"]) == [
        "tests/test_base_hook.py",
        "tests/test_top_hook.py",
    ]


def test_top_hook_does_not_select_the_hook_it_imports(repo: Path) -> None:
    assert select_tests(repo, [".claude/hooks/top_hook.py"]) == [
        "tests/test_top_hook.py"
    ]


# --- Rule 1: the full suite ---------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "tests/conftest.py",
        "conftest.py",
        "tests/helpers.py",
        "tests/fixtures/data.json",
        "assets/table.csv",
        "src/pkg/py.typed",
        "script.py",
    ],
)
def test_full_suite_for_files_that_reach_everything(repo: Path, path: str) -> None:
    write(repo, {path: "x = 1\n"})
    assert select_tests(repo, [path]) is None
    assert selection(repo, [path]).reason == f"{path} changed"


def test_full_suite_wins_over_a_narrow_change(repo: Path) -> None:
    assert select_tests(repo, ["src/pkg/lonely.py", "pyproject.toml"]) is None


def test_reexporting_init_gives_the_full_suite(repo: Path) -> None:
    write(repo, {"src/pkg/__init__.py": "from pkg.layout import WIDTH\n"})
    chosen = selection(repo, ["src/pkg/__init__.py"])
    assert chosen.tests is None
    assert "re-exports" in chosen.reason


def test_init_change_reaches_the_submodules_that_run_it(repo: Path) -> None:
    write(repo, {"tests/test_unrelated.py": "import pkg.reading.loader\n"})
    chosen = select_tests(repo, ["src/pkg/reading/__init__.py"]) or []
    assert "tests/test_unrelated.py" in chosen


def test_docstring_only_init_selects_its_importers(repo: Path) -> None:
    chosen = select_tests(repo, ["src/pkg/reading/__init__.py"])
    assert chosen == ["tests/test_reading_loader.py"]


def test_unparseable_changed_file_gives_the_full_suite(repo: Path) -> None:
    write(repo, {"src/pkg/lonely.py": "def broken(:\n"})
    chosen = selection(repo, ["src/pkg/lonely.py"])
    assert chosen.tests is None
    assert chosen.reason == "src/pkg/lonely.py cannot be parsed"


def test_unparseable_unchanged_file_gives_the_full_suite(repo: Path) -> None:
    # It may import the changed module; it cannot be read, so it cannot be ruled out.
    write(repo, {"tests/test_unrelated.py": "def broken(:\n"})
    assert select_tests(repo, ["src/pkg/lonely.py"]) is None


def test_undecodable_file_gives_the_full_suite(repo: Path) -> None:
    (repo / "src" / "pkg" / "lonely.py").write_bytes(b"x = '\xff\xfe'\n")
    assert select_tests(repo, ["src/pkg/lonely.py"]) is None


# --- Rule 6: prose and pipeline files select nothing --------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/notes.rst",
        "LICENSE",
        "development/feat/x/BRIEF.md",
        "development/feat/x/r1-a/round.json",
        "development/feat/x/diagnosis/test_reproduction.py",
        ".claude/skills/round/SKILL.md",
        ".claude/agents/test-runner.md",
        ".claude/workflows/round.js",
        ".claude/rules/python.md",
    ],
)
def test_prose_and_pipeline_files_select_nothing(repo: Path, path: str) -> None:
    chosen = selection(repo, [path])
    assert chosen.tests == ()
    assert chosen.summary() == "no tests apply: no test depends on the changed files"


def test_config_file_selects_the_tests_that_name_it(repo: Path) -> None:
    write(repo, {"tests/test_unrelated.py": 'NAME = ".claude/settings.json"\n'})
    assert select_tests(repo, [".claude/settings.json"]) == ["tests/test_unrelated.py"]
    assert select_tests(repo, [".github/workflows/check.yml"]) == []
    assert select_tests(repo, [".gitignore"]) == []


def test_a_changed_test_is_reported_as_changed_first(repo: Path) -> None:
    write(repo, {"tests/test_unrelated.py": 'NAME = "settings.json"\n'})
    chosen = selection(repo, [".claude/settings.json", "tests/test_unrelated.py"])
    assert chosen.reason == "tests/test_unrelated.py (changed)"


def test_nothing_changed_selects_nothing(repo: Path) -> None:
    assert selection(repo, []).summary() == "no tests apply: nothing changed"


def test_ignore_setting_silences_a_folder(repo: Path) -> None:
    text = PYPROJECT + '\n[tool.select_tests]\nignore = ["report and sources"]\n'
    write(repo, {"pyproject.toml": text})
    assert select_tests(repo, ["report and sources/main.tex"]) == []
    assert select_tests(repo, ["report and sourcesX/main.tex"]) is None


def test_default_roots_without_pytest_config(repo: Path) -> None:
    write(repo, {"pyproject.toml": '[project]\nname = "pkg"\n'})
    # pyproject.toml itself changed in the tree, but the selection is asked about core.
    assert select_tests(repo, ["src/pkg/core.py"]) == [
        "tests/test_cli.py",
        "tests/test_core.py",
    ]


@pytest.mark.parametrize(
    "raw", ["./src/pkg/lonely.py", "src\\pkg\\lonely.py", "/src/pkg/lonely.py"]
)
def test_paths_are_normalised(repo: Path, raw: str) -> None:
    assert select_tests(repo, [raw]) == ["tests/test_lonely.py"]


def test_selection_is_deterministic_and_does_not_touch_the_input(repo: Path) -> None:
    changed = ["src/pkg/layout.py", "tests/test_unrelated.py"]
    first = selection(repo, changed)
    assert selection(repo, list(reversed(changed))) == first
    assert changed == ["src/pkg/layout.py", "tests/test_unrelated.py"]
    assert first.total == TOTAL


def test_summary_forms() -> None:
    assert Selection(None, "x changed", 3).summary() == "full suite: x changed"
    assert Selection((), "why", 3).summary() == "no tests apply: why"
    assert Selection(("a", "b"), "why", 9).summary() == "selected 2 of 9: why"


# --- changed_files --------------------------------------------------------------------


def test_changed_files_sees_commits_staged_unstaged_and_untracked(repo: Path) -> None:
    commit(repo, {"src/pkg/core.py": '"""Core 2."""\n'})
    write(repo, {"src/pkg/cli.py": '"""CLI 2."""\n'})
    write(repo, {"src/pkg/lonely.py": '"""Lonely 2."""\n'})
    git(repo, "add", "src/pkg/lonely.py")
    write(repo, {"src/pkg/fresh.py": '"""New."""\n'})
    assert changed_files(repo, "main") == [
        "src/pkg/cli.py",
        "src/pkg/core.py",
        "src/pkg/fresh.py",
        "src/pkg/lonely.py",
    ]


def test_changed_files_ignores_what_main_did_after_the_branch(repo: Path) -> None:
    git(repo, "checkout", "-q", "main")
    commit(repo, {"src/pkg/lonely.py": '"""On main."""\n'})
    git(repo, "checkout", "-q", "feat/x")
    commit(repo, {"src/pkg/core.py": '"""Core 2."""\n'})
    assert changed_files(repo, "main") == ["src/pkg/core.py"]


def test_changed_files_skips_ignored_files(repo: Path) -> None:
    write(repo, {".gitignore": "*.log\n", "out.log": "x\n"})
    assert "out.log" not in changed_files(repo, "main")


def test_changed_files_keeps_unusual_names(repo: Path) -> None:
    commit(repo, {"report and sources/æøå.md": "x\n"})
    assert changed_files(repo, "main") == ["report and sources/æøå.md"]


def test_changed_files_is_empty_on_a_fresh_branch(repo: Path) -> None:
    assert changed_files(repo, "main") == []


def test_changed_files_refuses_an_unknown_base(repo: Path) -> None:
    with pytest.raises(ValueError, match="no-such-branch"):
        changed_files(repo, "no-such-branch")


# --- main -------------------------------------------------------------------------------


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(HOOK), "--root", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def test_main_prints_one_test_per_line_and_the_reason(repo: Path) -> None:
    commit(repo, {"src/pkg/core.py": '"""Core 2."""\n'})
    result = run_cli(repo, "--base", "main")
    assert result.stdout.splitlines() == ["tests/test_cli.py", "tests/test_core.py"]
    assert result.stderr.startswith(
        f"selected 2 of {TOTAL}: tests/test_cli.py (imports pkg.core)"
    )


def test_main_prints_all_for_the_full_suite(repo: Path) -> None:
    commit(repo, {"pyproject.toml": PYPROJECT + "\n"})
    result = run_cli(repo, "--base", "main")
    assert result.stdout == "ALL\n"
    assert result.stderr == "full suite: pyproject.toml changed\n"


def test_main_prints_nothing_when_no_test_applies(repo: Path) -> None:
    commit(repo, {"README.md": "# pkg, reworded\n"})
    result = run_cli(repo, "--base", "main")
    assert result.stdout == ""
    assert result.stderr.startswith("no tests apply")


def test_main_falls_back_to_all_on_an_unknown_base(repo: Path) -> None:
    result = run_cli(repo, "--base", "nope")
    assert result.stdout == "ALL\n"
    assert "cannot list changes against 'nope'" in result.stderr


def test_main_on_this_repo_runs() -> None:
    result = run_cli(HOOK.parents[2], "--base", "HEAD")
    assert result.returncode == 0

"""Select the test files a change can affect, so a round runs those and not the whole suite.

The full suite runs in CI on every pull request. A round only needs the tests that can
see what it changed:

    python .claude/hooks/select_tests.py --base feat/csv-export   # a round
    python .claude/hooks/select_tests.py --base origin/main       # the whole feature

The changed files are `git diff --name-only <base>...HEAD`, plus uncommitted and
untracked files. Stdout gets one test path per line, `ALL` for the full suite, or
nothing when no test applies; stderr gets one line saying why, for the round's log.

The rule, in order:

1. The full suite when any of these changed: `pyproject.toml`, a `conftest.py`, a
   file under the test directory that is not a test file (a shared helper, a
   fixture), a package `__init__.py` that imports anything (re-exports), any Python
   file that cannot be parsed, or a file the rule cannot place. When unsure, run
   everything: a needless full run costs time, a missed test lets a bug merge.
2. Every changed test file (`test_*.py`, `*_test.py`) that still exists.
3. Every changed module selects `test_<module>.py`, and `test_<pkg>_<module>.py` for
   the packages above it.
4. Every test file that depends on a changed module, directly or through other
   modules, by `ast` and never by importing the code: an `import`, a
   `from ... import`, `importlib.import_module("...")`, or a string that names the
   module (`"pkg.mod"`, `"pkg.mod.func"`) or its path (`"src/pkg/mod.py"`). A hook in
   `.claude/hooks/` is a module like any other, imported by its bare name.
5. Prose (`.md`, `.rst`, `.txt`, `LICENSE`), `development/`, and `.claude/` outside
   `hooks/` select nothing. Other configuration (`.claude/settings.json`,
   `.github/`, `.vscode/`, files at the root starting with a dot) selects the test
   files that name it in a string, usually none. `[tool.select_tests] ignore` in
   `pyproject.toml` lists more path prefixes that select nothing (a thesis folder).

Modules are named from the roots `pytest` imports them from: `pythonpath` and
`testpaths` in `[tool.pytest.ini_options]` (`src` and `tests` by default). Stdlib only.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import fnmatch
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

GIT_TIMEOUT_SECONDS = 30
ALL = "ALL"
FULL_SUITE_FILES = ("pyproject.toml",)
PROSE_SUFFIXES = (".md", ".rst", ".txt")
PROSE_NAMES = ("LICENSE", "LICENCE", "NOTICE", "AUTHORS")
QUIET_DIRS = ("development/",)
CONFIG_DIRS = (".github/", ".vscode/")
TEST_PATTERNS = ("test_*.py", "*_test.py")
SKIP_DIRS = frozenset(
    {
        ".venv",
        "venv",
        ".git",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)
IMPORT_CALLS = frozenset({"import_module", "__import__", "run_module", "importorskip"})


@dataclass(frozen=True)
class Selection:
    """The outcome of a selection.

    Attributes:
        tests: Test files to run, repo-relative and sorted; None for the full suite.
        reason: One line for the log: why these, or why the full suite.
        total: Test files in the suite, for "selected 4 of 61".
    """

    tests: tuple[str, ...] | None
    reason: str
    total: int

    def summary(self) -> str:
        """Describe the selection in one line.

        Returns:
            `full suite: <reason>`, `no tests apply: <reason>`, or
            `selected <n> of <total>: <reason>`.
        """
        if self.tests is None:
            return f"full suite: {self.reason}"
        if not self.tests:
            return f"no tests apply: {self.reason}"
        return f"selected {len(self.tests)} of {self.total}: {self.reason}"


@dataclass(frozen=True)
class _Config:
    module_roots: tuple[str, ...]
    test_roots: tuple[str, ...]
    ignore: tuple[str, ...]


def _git(project_dir: Path, args: list[str]) -> list[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(project_dir),
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ValueError(f"git {' '.join(args)} failed: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise ValueError(f"git {' '.join(args)} failed: {message}")
    text = result.stdout.decode("utf-8", "surrogateescape")
    return [part for part in text.split("\0") if part]


def changed_files(project_dir: Path, base: str) -> list[str]:
    """List the files changed against a base ref, committed or not.

    Args:
        project_dir: Repository root.
        base: The ref the branch is compared with, e.g. `feat/x` or `origin/main`;
            the comparison starts at the merge base (`base...HEAD`).

    Returns:
        Repo-relative paths with `/`, sorted and unique: the branch's commits,
        staged and unstaged changes, and untracked files that are not ignored.
        Renames appear as the old and the new path.

    Raises:
        ValueError: If git fails, for example because `base` does not exist.
    """
    names: set[str] = set()
    names.update(
        _git(
            project_dir, ["diff", "--name-only", "--no-renames", "-z", f"{base}...HEAD"]
        )
    )
    names.update(
        _git(project_dir, ["diff", "--name-only", "--no-renames", "-z", "HEAD"])
    )
    names.update(
        _git(project_dir, ["ls-files", "--others", "--exclude-standard", "-z"])
    )
    return sorted(names)


def _config(project_dir: Path) -> _Config:
    pyproject = project_dir / "pyproject.toml"
    data: dict[str, object] = {}
    with contextlib.suppress(OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    tool = data.get("tool")
    tool = tool if isinstance(tool, dict) else {}
    pytest_cfg = tool.get("pytest", {})
    ini = pytest_cfg.get("ini_options", {}) if isinstance(pytest_cfg, dict) else {}
    ini = ini if isinstance(ini, dict) else {}
    own = tool.get("select_tests", {})
    own = own if isinstance(own, dict) else {}

    def strings(value: object, default: tuple[str, ...]) -> tuple[str, ...]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return default
        cleaned = [_clean(v) for v in value if isinstance(v, str) and _clean(v)]
        return tuple(cleaned) if cleaned else default

    tests = strings(ini.get("testpaths"), ("tests",))
    paths = strings(ini.get("pythonpath"), ("src",))
    roots = tuple(dict.fromkeys([*paths, *tests]))
    return _Config(
        module_roots=roots, test_roots=tests, ignore=strings(own.get("ignore"), ())
    )


def _clean(path: str) -> str:
    text = path.replace("\\", "/").strip().strip("/")
    while text.startswith("./"):
        text = text[2:]
    return "" if text == "." else text


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _is_test_file(path: str, config: _Config) -> bool:
    name = PurePosixPath(path).name
    in_tests = any(_under(path, root) for root in config.test_roots)
    return in_tests and any(fnmatch.fnmatch(name, p) for p in TEST_PATTERNS)


def _module_names(path: str, config: _Config) -> list[str]:
    """Name a `.py` file the way pytest's import paths would."""
    names = []
    for root in config.module_roots:
        if not _under(path, root) or path == root:
            continue
        parts = list(
            PurePosixPath(path[len(root.rstrip("/")) + 1 :]).with_suffix("").parts
        )
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if parts and all(p.isidentifier() for p in parts):
            names.append(".".join(parts))
    return names


def _walk(project_dir: Path, roots: Iterable[str]) -> Iterator[str]:
    for root in roots:
        base = project_dir / root
        if not base.is_dir():
            continue
        stack = [base]
        while stack:
            folder = stack.pop()
            try:
                entries = sorted(folder.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir():
                    if entry.name not in SKIP_DIRS and not entry.name.endswith(
                        ".egg-info"
                    ):
                        stack.append(entry)
                elif entry.suffix == ".py":
                    yield entry.relative_to(project_dir).as_posix()


def _parents(name: str) -> list[str]:
    parts = name.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def _references(
    tree: ast.Module, own_names: list[str], is_package: bool
) -> tuple[set[str], set[str]]:
    """Module names and string literals a file refers to."""
    modules: set[str] = set()
    strings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.update(_parents(alias.name))
        elif isinstance(node, ast.ImportFrom):
            bases = _from_bases(node, own_names, is_package)
            for base in bases:
                modules.update(_parents(base))
                modules.update(
                    f"{base}.{alias.name}" for alias in node.names if alias.name != "*"
                )
        elif isinstance(node, ast.Call):
            func = node.func
            called = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            if called in IMPORT_CALLS and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    modules.update(_parents(first.value))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value
        ):
            strings.add(node.value)
    return modules, strings


def _from_bases(
    node: ast.ImportFrom, own_names: list[str], is_package: bool
) -> list[str]:
    if node.level == 0:
        return [node.module] if node.module else []
    bases = []
    for own in own_names:
        package = own.split(".") if is_package else own.split(".")[:-1]
        if node.level - 1 > len(package):
            continue
        anchor = package[: len(package) - (node.level - 1)]
        parts = [*anchor, *(node.module.split(".") if node.module else [])]
        if parts:
            bases.append(".".join(parts))
    return bases


def _reexports(tree: ast.Module) -> bool:
    return any(
        isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree)
    )


def _name_tests(path: str, config: _Config, existing: set[str]) -> set[str]:
    """Rule 3: `test_<mod>.py` and `test_<pkg>_<mod>.py` next to any test root."""
    found = set()
    for name in _module_names(path, config):
        parts = name.split(".")
        if not parts:
            continue
        stems = {"test_" + "_".join(parts[i:]) for i in range(len(parts))}
        for test in existing:
            if PurePosixPath(test).stem in stems:
                found.add(test)
    return found


def _place(path: str, config: _Config) -> str:
    """Classify a changed file: `quiet`, `config`, `test`, `code` or `full`."""
    name = PurePosixPath(path).name
    if any(_under(path, prefix) for prefix in config.ignore):
        return "quiet"
    if path in FULL_SUITE_FILES or name == "conftest.py":
        return "full"
    if any(_under(path, root) for root in config.test_roots):
        if _is_test_file(path, config):
            return "test"
        return "full"
    if any(path.startswith(d) for d in QUIET_DIRS):
        return "quiet"
    if path.startswith(".claude/") and not path.startswith(".claude/hooks/"):
        return (
            "config"
            if not path.endswith(PROSE_SUFFIXES) and "/" not in path[8:]
            else "quiet"
        )
    if name.endswith(PROSE_SUFFIXES) or name.split(".")[0] in PROSE_NAMES:
        return "quiet"
    if any(path.startswith(d) for d in CONFIG_DIRS) or (
        "/" not in path and name.startswith(".")
    ):
        return "config"
    if name.endswith(".py") and _module_names(path, config):
        return "code"
    return "full"


def selection(project_dir: Path, changed: Iterable[str]) -> Selection:
    """Select the test files the changed files can affect, with the reason.

    Args:
        project_dir: Repository root.
        changed: Repo-relative paths of the changed files (deleted ones included).

    Returns:
        The selection; `tests` is None when the full suite must run.
    """
    config = _config(project_dir)
    files = sorted(set(_walk(project_dir, config.module_roots)))
    tests = {f for f in files if _is_test_file(f, config)}
    total = len(tests)
    paths = sorted({_clean(c) for c in changed if _clean(c)})
    if not paths:
        return Selection((), "nothing changed", total)

    places = {path: _place(path, config) for path in paths}
    for path, place in places.items():
        if place == "full":
            return Selection(None, f"{path} changed", total)

    trees: dict[str, ast.Module] = {}
    for file in files:
        try:
            source = (project_dir / file).read_bytes()
            trees[file] = ast.parse(source, filename=file)
        except (OSError, SyntaxError, ValueError):
            return Selection(None, f"{file} cannot be parsed", total)

    for path, place in places.items():
        if place == "code" and PurePosixPath(path).name == "__init__.py":
            tree = trees.get(path)
            if tree is not None and _reexports(tree):
                return Selection(None, f"{path} re-exports names", total)

    names_of = {file: _module_names(file, config) for file in files}
    referrers: dict[str, set[str]] = {}
    strings_of: dict[str, set[str]] = {}
    for file, tree in trees.items():
        is_package = PurePosixPath(file).name == "__init__.py"
        modules, strings = _references(tree, names_of[file], is_package)
        for name in names_of[file]:  # importing a submodule runs its packages first
            modules.update(_parents(name)[:-1])
        strings_of[file] = strings
        for module in modules:
            referrers.setdefault(module, set()).add(file)
    known = {name for names in names_of.values() for name in names}
    for file, strings in strings_of.items():
        for text in strings:
            for module in _named(text, known):
                referrers.setdefault(module, set()).add(file)

    chosen: dict[str, str] = {}

    def choose(test: str, why: str) -> None:
        if test in tests and test not in chosen:
            chosen[test] = why

    for path, place in places.items():
        if place == "test":
            choose(path, "changed")
    for path, place in places.items():
        if place == "config":
            label = PurePosixPath(path).name
            for file in sorted(tests):
                if any(path in s or label in s for s in strings_of.get(file, ())):
                    choose(file, f"names {label}")
    seeds = [path for path, place in places.items() if place in ("code", "test")]
    for path in seeds:
        for test in sorted(_name_tests(path, config, tests)):
            choose(test, f"tests {PurePosixPath(path).stem}")
        path_referrers = {
            f for f, s in strings_of.items() if any(path in text for text in s)
        }
        for file in sorted(path_referrers):
            choose(file, f"names {path}")
        for file, via in _dependents(path, config, referrers, names_of):
            choose(file, f"imports {via}")

    reasons = ", ".join(f"{test} ({why})" for test, why in sorted(chosen.items()))
    if not chosen:
        return Selection((), "no test depends on the changed files", total)
    return Selection(tuple(sorted(chosen)), reasons, total)


def _named(text: str, known: set[str]) -> Iterator[str]:
    if " " in text or len(text) > 200:
        return
    candidate = text
    while candidate:
        if candidate in known:
            yield candidate
            return
        candidate = candidate.rpartition(".")[0]


def _dependents(
    path: str,
    config: _Config,
    referrers: dict[str, set[str]],
    names_of: dict[str, list[str]],
) -> Iterator[tuple[str, str]]:
    """Every file that depends on `path`, transitively, with the module it reached."""
    start = _module_names(path, config)
    queue = list(start)
    seen = set(queue)
    while queue:
        module = queue.pop(0)
        for file in sorted(referrers.get(module, ())):
            if file == path:
                continue
            yield file, start[0] if start else module
            for name in names_of.get(file, []):
                if name not in seen:
                    seen.add(name)
                    queue.append(name)


def select_tests(project_dir: Path, changed: Iterable[str]) -> list[str] | None:
    """Select the test files the changed files can affect.

    Args:
        project_dir: Repository root.
        changed: Repo-relative paths of the changed files (deleted ones included).

    Returns:
        Repo-relative test paths, sorted; an empty list when no test applies; None
        when the full suite must run.
    """
    chosen = selection(project_dir, changed)
    return None if chosen.tests is None else list(chosen.tests)


def main() -> None:
    """CLI: `[--base REF] [--root DIR]`; tests on stdout, the reason on stderr."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main", help="ref to compare with")
    parser.add_argument("--root", default=".", help="repository root")
    args = parser.parse_args()
    project_dir = Path(args.root).resolve()
    try:
        changed = changed_files(project_dir, args.base)
    except ValueError as exc:
        print(ALL)
        print(
            f"full suite: cannot list changes against {args.base!r}: {exc}",
            file=sys.stderr,
        )
        return
    chosen = selection(project_dir, changed)
    if chosen.tests is None:
        print(ALL)
    else:
        for test in chosen.tests:
            print(test)
    print(chosen.summary(), file=sys.stderr)


if __name__ == "__main__":
    main()

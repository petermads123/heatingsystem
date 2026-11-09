# Devlog

One entry per version, newest first: what changed, then the files it touched. `/finish`
writes each entry in the commit that opens the pull request.

## 1.1.3 — 2026-11-09

The concept conversation asks one question at a time, as in template 0.2.1. In `/feature`
every turn that waits for the user ends on exactly one question, never a list, and the four
repo scouts no longer write into the chat: a report that arrives while a question is open is
noted in `BRIEF.md` under `## Scout findings`, the turn ends without a message, and the
findings shape the next question once the user answers. The rounds are proposed only after
every report is in. `/fix` asks its intake and its agreement the same way.

Files:
- `.claude/skills/feature/SKILL.md`, `.claude/skills/fix/SKILL.md`
- `README.md`, `DEVLOG.md`, `pyproject.toml`

## 1.1.2 — 2026-11-09

A round runs only the tests its change can affect; the full suite runs in CI on the pull
request. Adopted from `petermads123/template` 0.2.0: the new hook `select_tests.py` reads the
round's diff against the feature branch and picks the changed test files, each changed
module's `test_<module>.py` and every test file that imports a changed module, directly or
through other modules (by `ast`, never by importing). It falls back to the full suite when
`pyproject.toml`, a `conftest.py`, a test helper, a re-exporting `__init__.py` or an
unparseable file changed, and selects nothing for prose and pipeline files. Step 7 runs
that selection plus ruff and mypy on the whole tree, records it in `log.md`, and in the fix
loop re-runs with `--ff` and the earlier failures; `final.js` runs the selection over the
whole feature against `origin/main`. `/finish` now sees the pull request's CI through to
green, fixing a red run on the feature branch, and never calls a feature done on a red or
pending CI.

Files:
- `.claude/hooks/select_tests.py`, `tests/test_select_tests.py`
- `.claude/workflows/round.js`, `final.js`; `.claude/agents/test-runner.md`, `concept-checker.md`
- `.claude/skills/finish/SKILL.md`, `.claude/settings.json`
- `CLAUDE.md`, `STRUCTURE.md`, `README.md`, `DEVLOG.md`, `pyproject.toml`

## 1.1.1 — 2026-11-08

Replaces the ten-step plan-file pipeline with the one from `petermads123/template`: features
are agreed in `/feature`, split into rounds that each run as the `round` workflow on their own
agents, and closed by `/finish`; bugs start with `/fix` and its `diagnose` workflow. State
moves from numbered plan files to `BRIEF.md`, `brief.json` and `round.json` per feature. CI
now runs the gate and `check_sync.py` on every pull request. No package code changes.

Files:
- `.claude/skills/` — `feature`, `fix`, `round`, `finish` replace the fourteen old skills
- `.claude/agents/` — the template's pipeline agents replace the five old ones
- `.claude/workflows/` — `diagnose.js`, `round.js`, `final.js`
- `.claude/hooks/` — `dev_state.py`, `check_sync.py` added, `session_brief.py`, `guard_git.py`, `lint_py.py` updated, `plan_state.py`, `stop_gate.py` removed
- `.claude/rules/python.md`, `.claude/settings.json`, `.gitignore`
- `tests/` — `test_dev_state.py`, `test_session_brief.py`, `test_check_sync.py` added, `test_plan_state.py`, `test_stop_gate.py` removed
- `.github/workflows/check.yml`
- `development/README.md` added, `development/TEMPLATE.md` and `docs/BACKLOG.md` removed
- `CLAUDE.md`, `STRUCTURE.md`, `README.md`, `DEVLOG.md`, `DEVELOPMENT.md`, `pyproject.toml`

Versions up to 1.1.0 predate this log; their history is in git and under `development/`.

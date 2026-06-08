# CLAUDE.md — heatingsystem

Guidance for Claude Code when working in this repository.

## What this project is

`heatingsystem` is a Python (≥ 3.13) package of heating-system models. The first
model is `PIController` (`heatingsystem/pi_controller/`). The package is installed
with `pip install -e .` and consumed via `import heatingsystem as hs`.

## ⚙️ Coding workflow — ALWAYS use the multi-agent dev pipeline

**For any request that writes new code or fixes/extends existing code, follow the
[`dev-pipeline`](.claude/skills/dev-pipeline/SKILL.md) skill** (also invokable
explicitly as `/dev-pipeline`). You are the **facilitator**: you spawn the subagents
and synthesize their work, because subagents cannot spawn subagents themselves.

Pipeline summary (full detail in the skill):

1. **Plan** — spawn two `planner` agents in parallel; they draft plans, then
   critique each other; you synthesize one final plan with a two-way work split.
2. **Plan approval gate** — present the plan to the **user** as a clean overview and
   wait for their comments / confirmation. **Coding does not start until the user
   approves.**
3. **Code** — you coordinate two `coder` agents **plus** one `coordinator` agent in
   parallel. The coders each own a disjoint set of files; the coordinator watches
   their progress against the goal and course-corrects drift each checkpoint.
4. **Review & Test** — spawn `code-reviewer` and `code-tester` in parallel. The
   reviewer checks correctness + every rule below; the tester writes pytest cases
   (normal + boundary + edge) and runs mypy + ruff + pytest.
5. **Acceptance verification** — spawn the `acceptance-verifier` (final gate) to
   confirm the result matches the **user's original request and the approved plan**,
   not just that the checks are green.
6. **Loop or ship** — any blocker/fail/reject loops back (≤ 3 rounds) to coding or
   planning; ship only when review, test, **and** acceptance all pass.

**Scale to the task:** trivial edits (typos, renames, one-liners) skip the
plan/code agents — just make the change (with a quick "ok?" at the approval gate) and
run the check pipeline. Reserve the full pipeline for non-trivial work. State which
path you're taking.

The agents live in [`.claude/agents/`](.claude/agents/): `planner`, `coder`,
`coordinator`, `code-reviewer`, `code-tester`, `acceptance-verifier`.

**Per-agent thinking budget:** Sonnet agents (`coder`, `coordinator`) use **maximum**
thinking (`ultrathink`); Opus agents (`planner`, `code-reviewer`, `code-tester`,
`acceptance-verifier`) use **medium** thinking (`think hard`).

## 📏 Non-negotiable code rules

Every piece of code produced in this repo MUST satisfy all of these. They are
enforced in the review step and verified in the test step.

1. **100% mypy compliant.** Full type annotations on every function, parameter, and
   return. mypy runs in strict-ish mode (see `[tool.mypy]` in `pyproject.toml`) and
   is part of the test step. Never weaken the mypy config to make code pass.
2. **ruff clean.** Honor every enabled rule group
   (`E,W,I,F,B,ANN,D,UP,TID,N,SIM,ARG,ERA,DTZ`) in `pyproject.toml`.
3. **Google-style docstrings** on every module, class, function, and method
   (`convention = "google"`, ruff `D` rules).
4. **Comments throughout** explaining non-obvious logic.
5. **`main()` + `if __name__ == "__main__": main()`** in every standalone module,
   demonstrating **at least one example of every function and class** it defines.

## ✅ Check commands (Windows, project venv)

```powershell
.venv/Scripts/python.exe -m mypy heatingsystem    # type check — must be clean
.venv/Scripts/python.exe -m ruff check .          # lint — must pass
.venv/Scripts/python.exe -m pytest                # tests — must be green
```

Tests live in `tests/` (configured in `[tool.pytest.ini_options]`). Tests are exempt
from the `D` and `ANN` ruff rules via `per-file-ignores`.

## Conventions

- Match the existing style in `heatingsystem/pi_controller/pi_controller.py`.
- Math-style names (`Kp`, `Ki`, `dt`) are allowed (`N803`/`N806` ignored).
- New models go in their own subpackage under `heatingsystem/` and are re-exported
  from `heatingsystem/__init__.py`.

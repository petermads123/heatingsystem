---
name: dev-pipeline
description: The multi-agent development workflow for this repo. Use whenever you are asked to write new code or fix/extend existing code — it orchestrates parallel planning (with a mandatory user plan-approval gate), parallel coding supervised by a coordinator, parallel review+testing, and a final acceptance check against the user's request, all through subagents, enforcing the project's mypy/ruff/docstring/comment/main() rules. Invoke explicitly via /dev-pipeline or follow it automatically for any non-trivial coding task.
---

# Dev Pipeline — multi-agent coding workflow

You (the main session) are the **facilitator**. Subagents cannot spawn their own
subagents, so *you* drive every phase by spawning agents with the `Agent` tool and
synthesizing their results. Phases that say "in parallel" mean: emit the `Agent`
calls **in a single message** so they run concurrently.

## When to run this

Trigger for any request that writes new code or fixes/adds features. **Scale the
process to the task** (per the project rule):

- **Trivial** (typo, rename, one-line fix, comment): skip planning/coding agents.
  Make the change yourself, then run the **Review & Test** phase (or at minimum the
  check pipeline: mypy + ruff + pytest).
- **Non-trivial** (new function/class/module, feature, multi-file change, anything
  with edge cases): run the **full** pipeline below.

State at the start which path you're taking and why.

## Phase 1 — Plan (parallel, cooperate + critique)

1. **Draft (parallel):** spawn **two** `planner` agents in one message — "Planner A"
   and "Planner B" — both in **DRAFT** mode on the same task. They independently
   produce high-level plans.
2. **Critique (parallel):** spawn **two** `planner` agents again in one message,
   each in **CRITIQUE** mode, each given the *other's* draft. They red-team each
   other.
3. **Synthesize:** you merge both drafts + both critiques into **one final plan**:
   goal, approach, exact interfaces (fully typed), edge cases, test strategy, and a
   **two-way work split into non-overlapping files**. Resolve every `[BLOCKER]`.

If the two plans fundamentally disagree on approach, do one more critique round, then
decide. If still blocked, surface the trade-off to the user.

## Phase 1.5 — Plan approval gate (USER CONFIRMATION — mandatory)

**Do not start coding until the user approves the plan.**

1. Present the final synthesized plan to the user as a **clean, readable overview** —
   goal, approach, the public interfaces, the edge cases to be handled, the test
   strategy, and the two-way work split. Use headings/bullets; keep it skimmable, not
   a wall of text.
2. Explicitly invite the user to **comment, request changes, or approve.**
3. If they ask for changes, fold their feedback in (loop back into Phase 1 if the
   change is design-level) and re-present the updated plan.
4. Only once the user has **confirmed** do you proceed to Phase 2. Treat their
   comments at this gate as part of the requirements for the rest of the pipeline.

(For trivial tasks that skipped Phase 1, this gate is a one-line "here's the small
change I'll make — ok?" rather than a full plan.)

## Phase 2 — Code (facilitator coordinates 2 coders + 1 coordinator in parallel)

1. Partition the work into **two disjoint chunks** (different files where possible) so
   the coders never edit the same file. Pin the shared interface contracts.
2. Spawn, **in one message**, three agents that run together: **Coder A**, **Coder B**
   (each given the final plan, its own chunk, and the contracts), and one
   **`coordinator`** (given the plan's goal + both chunk assignments).
3. Run coding in **short iterations**. At each checkpoint, feed the coders' current
   progress/diffs to the `coordinator`; relay its `[STOP]`/`[ADJUST]`/`[WATCH]` steers
   back to the relevant coder so drift is corrected early. (Isolated subagents can't
   watch each other live, so the coordinator operates per checkpoint — see its agent
   definition.)
4. **Integrate:** reconcile the coders' outputs. If they touched a shared seam, you
   make the final reconciling edit yourself. Resolve any cross-chunk mismatch.

Every coder must deliver fully-typed, ruff-clean, Google-docstringed, commented code
with a `main()` example covering every function/class (see Rules below).

## Phase 3 — Review & Test (parallel)

Spawn **`code-reviewer`** and **`code-tester`** in one message:

- `code-reviewer` checks correctness + every rule, runs mypy & ruff, returns a
  verdict + findings.
- `code-tester` writes pytest cases (normal + boundary + edge), then runs
  **mypy + ruff + pytest** and each module's `main()`, returns a verdict + real output.

## Phase 4 — Acceptance verification (final gate)

Once review and test both pass, spawn the **`acceptance-verifier`** (Opus). Give it
the **user's original request** (verbatim, plus any comments from the Phase 1.5 gate),
the **approved plan**, and the final code + reviewer/tester results. It confirms the
implementation actually delivers what the user asked for and what the plan
committed to — not merely that the checks are green. It returns `ACCEPT` or `REJECT`
with a requirement-by-requirement trace.

## Phase 5 — Loop or ship

- Any `[BLOCKER]`, `CHANGES REQUESTED`, `FAIL`, or `REJECT` → loop back:
  - rule/correctness/test failures → re-spawn the relevant `coder`(s) with the findings;
  - design-level or intent gaps → return to Phase 1 (and re-confirm at the 1.5 gate);
  - if the acceptance-verifier flags **unclear intent**, ask the user rather than guess.
- **Cap at 3 full loops.** If still failing, stop and report to the user what's
  blocking, with the failing output — don't loop forever or weaken the rules to pass.
- **Ship only when reviewer = `PASS`, tester = `PASS` (mypy, ruff, pytest all green),
  and acceptance-verifier = `ACCEPT`.** Then summarize what changed, the checks that
  passed, the test cases added, and how it maps back to the user's request.

## The non-negotiable rules (enforced in every phase)

1. **100% mypy compliant** — full type annotations; mypy runs in the test step and is
   considered in review. Never weaken `[tool.mypy]` to pass.
2. **ruff clean** — honor every rule group in `pyproject.toml`; run `ruff check .`.
3. **Google-style docstrings** on every module/class/function/method.
4. **Comments** throughout for non-obvious logic.
5. **`main()` + `if __name__ == "__main__": main()`** in each standalone module, with
   **at least one example of every function and class**.

## Check commands (Windows venv)

```
.venv/Scripts/python.exe -m mypy heatingsystem
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pytest
```

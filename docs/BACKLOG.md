# Backlog — Claude setup improvements

Findings from a review of the Claude configuration this repo adopted from
`petermads123/template_repo` (`.claude/`, the hooks, the ruleset, `pyproject.toml`),
September 2026. The configuration was ported wholesale, so every open item here applies
to this repo as much as to the template. It was re-imported in October 2026 with three
deliberate exceptions, which a future wholesale import must keep:

- `/repo-setup` is left out, because it sets up new repos only.
- `.claude/settings.json` finds the hook interpreter differently (see Portability in §4).
- `.claude/skills/implement/SKILL.md` and `.claude/skills/test/SKILL.md` give this
  package's `pi_controller.py` and `tests/test_pi_controller.py` as a second reference.
  The template points at its `hello_world` placeholder instead, which does not exist here.

This is **not** a plan file. It carries no `claude-plan` marker and lives outside
`development/`, which is the only directory `.claude/hooks/plan_state.py` scans — so nothing
here affects the pipeline's state. Items graduate out of this file by becoming a plan
folder under `development/`, at which point delete the entry here.

| Status | Meaning |
|---|---|
| **confirmed** | Reproduced. The diagnosis below is verified, not a hunch. |
| **proposed** | Judgment call, not yet discussed. |
| **rejected** | Considered and declined; the reason is recorded so it is not re-litigated. |

---

## 1. `guard_git.py` fails open on quoted shell separators — **shipped**

Fixed in the template before this repo adopted its setup: the two rounds under
`development/fix/guard-git-parsing/` in `petermads123/template_repo` hold the full
diagnosis, design and evidence, and `tests/test_guard_git.py` here is their regression
suite. Both defects are fixed, and the rounds closed eight further holes the first
diagnosis had not found. `sudo -u me git push origin main` is still allowed — a documented
limit with a test asserting it, not an oversight. Three follow-ups are deferred in that
repo's round 2 file (§8), of which **R6** is the one worth opening first: `current_branch`
returns `""` both for a detached HEAD, where allowing a commit is correct, and for "git
could not answer", which is the original fail-open shape by another road.

This entry stays as a pointer rather than being deleted outright, because the rule above
says an item's entry goes when it graduates — and a reader who remembers the defect should
find where it went rather than find nothing.

## 2. Continuous integration — **rejected**

A GitHub Actions workflow running the four checks on every pull request, plus a
`required_status_checks` rule in the branch ruleset so a red branch cannot merge.

**Declined**: not worth the per-repo overhead on small solo projects.

One consequence to accept knowingly: the CI clauses in `watch-pr` ("CI red — fix it"),
`create-pr` ("CI green") and `CLAUDE.md` ("CI green where there is CI") now describe a
thing that will never exist. They are hedged with "where the repo has any", so nothing
misfires — they are simply dead text. Worth a prose cleanup eventually; not urgent.

---

## 3. Cheap, high value — **proposed**

### 3.1 ~~`.gitignore` entries for `.claude/`~~ — **done**

Shipped with the autonomous-pipeline work: `.claude/.skip-gate` and
`.claude/settings.local.json` are ignored, so neither can be committed by accident.

### 3.2 A `PreCompact` hook

Pipeline state lives in a plan file precisely so it survives a context reset — and a
compaction is the moment that promise is tested. A ten-line hook re-injecting the active
plan's path and step would close the loop. Four of the five hook slots are in use; this is
the one that matters most. Routing: `/feature` (adds a file).

### 3.3 A statusline

`plan_state.py` already computes feature, round, step name and branch. Surfacing
`csv-export · round 2 · step 5 Test · feat/csv-export` permanently makes the single piece
of state the whole system turns on visible at all times. Routing: `/feature` (adds a file).

### 3.4 Two documentation drifts

- ~~`CLAUDE.md` claims "two active plans is a state the hooks will complain about, and
  rightly".~~ **Prose fixed** with the autonomous-pipeline work: it now says the session
  brief reports it and the hooks then guess. What remains is the check itself:
  `plan_state.active_plan` silently returns the most recently modified one and `stop_gate`
  has no check. Adding it to `gate_failures` is as mechanical as `missing_init_files`.
- `.claude/rules/python.md` spends roughly sixty lines mandating `main()` plus the
  `if __name__` guard on every module. Nothing verifies it.

The prose half of this item is **done**: the three hooks that called this a "nine-step
pipeline", `stop_gate`'s "steps 4 to 9", and `Plan.status` documented as three values when
`template` is a fourth were all corrected and merged in `e155ea4`. What remains is the two
*checks* — one active plan, and the `main()` guard rule — neither of which exists.

Routing: the prose fix alone is `/small-change`; adding either check to the stop gate is
`/feature`.

---

## 4. Larger or later — **proposed**

| Item | Why | Routing |
|---|---|---|
| ~~Tests for `plan_state.py` and `stop_gate.py`~~ — **done**, `e155ea4` | Shipped with the git-guard work rather than as a later round: 291 tests now cover all three substantial hooks, where there were none. | — |
| ~~`gh` is an undeclared hard dependency~~ — **done** | `create-pr` now says "the GitHub MCP tools where they are available, `gh pr create` where it is", which is how `template_repo`'s PR #6 and #7 were actually opened. A `.mcp.json` pinning the GitHub MCP server would make the two surfaces identical; not needed until one of them fails. | — |
| `CODEOWNERS` | `main` now runs the template's solo ruleset, which leaves `require_code_owner_review` off, so nothing needs the file yet. It would auto-route the approver named in `CLAUDE.md`, and would be needed if code-owner review were ever turned on. | `/small-change` |
| ~~Pin the toolchain~~ — **done**, `e155ea4` | It happened exactly as predicted while the git-guard work was in flight: a Ruff release added formatting of Python inside Markdown and turned the gate red on `.claude/rules/python.md`, prose nobody had edited. Now pinned to `ruff>=0.16,<0.17`, `mypy>=2.3,<3`, `pytest>=9.1,<10`, with the reason recorded in `pyproject.toml` so a future reader does not undo it. | — |
| Portability | `.vscode/settings.json` hardcodes `.venv\Scripts\python.exe` and the documented commands are PowerShell. ~~`settings.json` invokes hooks as bare `python`~~ — **done**: every hook command now runs the first of `python3` or `python` that actually starts, so a `python3`-only machine runs the hooks and the Windows Store alias stub (exit 9009, which made `guard_git` fail open) is skipped. This line deliberately differs from `template_repo`, which still has `command -v python3 \|\| command -v python`: a future wholesale `.claude/` import must keep it until the template carries the same fix. | `/feature` |
| ~~`/bugfix` skill~~ — **done** | Shipped from the template as `/fix`, which diagnoses before step 1 and runs the pipeline as a fix round. | — |
| `/release` skill | Nothing manages `version = "0.1.0"`. No tag, no changelog. | `/feature` |
| `/audit` skill | The git history contains "Fix the inconsistencies a full audit of the repo turned up" — that audit was ad hoc, and the drifts in 3.4 are the same class of thing recurring. Make it repeatable. | `/feature` |

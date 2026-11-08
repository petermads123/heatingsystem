# Working in this repo

## What this project is

`heatingsystem` is a Python (>= 3.13) package of heating-system models, installed with
`pip install -e ".[dev]"` and consumed via `import heatingsystem as hs`. The models are
`PIController` (`pi_controller/`), the actuator mapping `Modulator` (`modulator/`) and the
multi-room `SectionAllocator` (`allocator/`); new models go in their own subpackage under
`src/heatingsystem/` and are re-exported from `src/heatingsystem/__init__.py`. `test.py` at
the repo root is a matplotlib simulation of the controller, not a test.

@STRUCTURE.md

`STRUCTURE.md` above maps every module, class and function with its signature and return
type. Read it before searching the repo or opening source files: it exists so a plan can be
made without reading every file. It must describe the code on `main` exactly.

## Files that are always kept current

| File | Holds | Updated |
|---|---|---|
| `pyproject.toml` | the package, its dependencies, Ruff/mypy/pytest config, **the version** | the version by `/finish`; dependencies in the change that needs them |
| `STRUCTURE.md` | every module, public class and function, with signatures and return types | by `/finish`, from the code, once per feature |
| `README.md` | what the repo is, `**Version:**`, each package and its main entry points, and each model's usage section | by `/finish`, once per feature |
| `DEVLOG.md` | one entry per version, newest first: what changed and which files | by `/finish`, once per feature |
| `CLAUDE.md` | this routing map | when the workflow itself changes |
| `DEVELOPMENT.md` | open development-side questions and things to fix later | an entry is added when an extra is deferred, deleted by the change that resolves it |

During a feature these documents are deliberately left alone: the rounds record what they
add in their `log.md` "Changes" sections, and `/finish` writes all four documents in the
commit that opens the pull request. `python .claude/hooks/check_sync.py` checks that they
agree with each other and with the code; CI runs it on every pull request, so `main` is never
out of sync.

## Versions: `X.Y.Z`

The version lives in `pyproject.toml` and is repeated in `README.md` and the newest
`DEVLOG.md` entry.

| Bump | Moves | For | Callers on the old version |
|---|---|---|---|
| `major` | **X**.0.0 | big new features, new packages, changed endpoints: return values or types change | may break; updating needs work |
| `minor` | x.**Y**.0 | new features inside existing frameworks, new endpoints | keep working unchanged |
| `patch` | x.y.**Z** | fixes, docstrings, refactors that change no behaviour | keep working unchanged |

`major` is for a big new feature or package, **or** anything that can make an existing
caller fail — a removed or renamed endpoint, a changed return type, a new required
parameter — however small. `minor` is for new features and endpoints that break nothing;
`patch` for fixes and docstrings. A feature takes the largest bump any of its rounds needs. The bump is
agreed at `/feature` and written by `/finish`, counted from `main`'s version at that moment.

## The feature pipeline

Every change to the code goes through `/feature`. State lives in files under `development/`,
committed and pushed after every step, so any session resumes where the last one stopped;
`.claude/hooks/session_brief.py` reports a feature in flight at session start.

```
development/<branch>/            e.g. development/feat/csv-export/
  BRIEF.md                       the feature in prose: goal, criteria, rounds, decisions
  brief.json                     status and rounds, read by hooks and mods
  diagnosis/                     a fix only: log.md, state.json, test_reproduction.py
  r<n>-<slug>/                   one folder per round
    log.md                       the round's notepad: concept, plan, changes, results
    round.json                   the round's step and status, read by hooks and mods
```

`development/README.md` documents the two JSON schemas. `brief.json` is written only by the
main session and `round.json` by the round's steps (and by `/round` only to record a stop the
workflow script could not write itself).

| Stage | Skill | Runs as | Waits for |
|---|---|---|---|
| Main Conceptualize: concept, branch name, bump | `/feature` | main session, Opus medium | the user |
| A fix instead: intake, then diagnosis | `/fix` | main session, Opus medium, with the `diagnose` workflow | the user agrees cause, scope and bump |
| Narrow down, rounds, BRIEF | `/feature` | main session + 4 `repo-scout`, Sonnet medium, in the background | the user agrees the rounds |
| Each round, one at a time | `/round` | main session, Sonnet high, coordinating the `round` workflow | only questions the round escalates |
| Final check, extras, docs, version, pull request | `/finish` | main session, Sonnet high, with the `final` workflow and `docs-writer` | the user, only on a critical extra |

### A round

A round runs on its own branch, `<branch>--r<n>` (a flat name: git cannot hold both
`feat/x` and `feat/x/r1`), as the workflow `.claude/workflows/round.js`. Each step is an
agent from `.claude/agents/` on the model the step needs; each writing step commits and
pushes its output with `round.json` advanced.

| # | Step | Agent | Model, effort |
|---|---|---|---|
| 1 | Conceptualize: exact round scope, acceptance criteria | `round-conceptualizer` | Opus medium |
| 2 | Plan: modules, signatures, test intents | `round-planner` | Opus medium |
| 3 | Implement | `implementer` | Sonnet medium |
| 4 | Verify: ruff and mypy, then parallel review per unit | `verifier`, `code-reviewer` × ≤5 | Sonnet medium |
| 5 | Test design, four lenses in parallel | `test-designer` × 4 | Sonnet low |
| 6 | Write tests | `test-writer` | Sonnet medium |
| 7 | Run tests: the full gate | `test-runner` | Sonnet low |
| 8 | Concept check: three independent full checks (concept, purpose, criteria), majority passes | `concept-checker` × 3 | Sonnet low |
| 9 | Close: merge into the feature branch | `round-closer` | Sonnet medium |

A failing gate (steps 7 and 8) goes back to the `implementer` in fix mode, twice at most per
round, counted across resumes; every fix sends the round back through the test gate, and
after two the round escalates. A step that meets something genuinely unclear stops the
round with a question; the main session answers it from the BRIEF when it can, asks the user
when it cannot, and resumes the round at that step.

A skill's pinned model holds for the turn that invokes it. Stages that span turns re-invoke
their skill: `/round` when its workflow completes, `/feature` on each turn of the
conversation, `/finish` when the final workflow completes.

If a hosted session may push only its own branch, the feature and its rounds run on that one
branch: `/round` records the round's `branch` as the feature branch and the workflow skips
the merge.

### A fix

`/fix` replaces `/feature`'s concept stage with a diagnosis, then joins the same pipeline.
The `diagnose` workflow (`.claude/workflows/diagnose.js`, state in `diagnosis/state.json`):

| # | Step | Agent | Model, effort |
|---|---|---|---|
| 1 | Reproduce: the smallest failing test, red for the reported reason | `reproducer` | Sonnet medium |
| 2 | Hunt: candidate causes through three lenses (trace, history, contract) in parallel | `cause-hunter` × 3 | Sonnet medium |
| 3 | Root cause: one cause, proven by a reverted change; its class; bug or not | `root-cause-analyst` | Opus high |
| 4 | Critique: try to falsify it; one revision, then the user decides | `diagnosis-critic` | Opus high |

The user then agrees the cause, the scope (this case or the whole class) and the bump, and the
rounds run with `kind: "fix"`: the reproduction is the first acceptance criterion, it is run
red before the fix and green after, and the concept check wants both runs on record. A
diagnosis that finds no bug (the code does what it promises) parks the fix and hands over to
`/feature`.

### Opening the pull request

`/finish` opens the pull request on its own when the final check passes and no extra is
critical. It then lists the non-critical extras in chat. A critical extra holds the pull
request until the user decides whether it becomes another round.

## Routing

The user never has to type a slash command. Classify the request first and say the route in
one line:

| Situation | Route |
|---|---|
| A new feature, a behaviour change | `/feature` |
| Something that exists behaves wrongly: wrong output, a crash, a check that lets something through | `/fix` |
| A feature or fix is in flight | its stage: `/feature`, `/fix`, `/round` or `/finish`, as the session brief says |
| The workflow itself: skills, agents, hooks, CI, `CLAUDE.md` | a `chore/` branch, the change, the gate below, `check_sync.py`, a `patch` bump with a DEVLOG entry, a pull request |
| A question | an answer, not a pipeline |

An explicit slash command wins.

## Branches

Never commit to `main`; `.claude/hooks/guard_git.py` refuses it. Names are `type/kebab-case`:
`feat/`, `fix/`, `refactor/`, `docs/`, `chore/`. A round's branch is its feature's name plus
`--r<n>`. Round branches are deleted once merged into the feature branch.

## Review and merge

The approver is `petermads123`. **Claude never merges a pull request on its own judgment,
and never on an approval alone**: it reaches `main` because the approver pressed the button,
or told Claude in so many words to merge it. Even then: nothing pushed since they looked, CI
green, no conflict. After a merge, Claude deletes the feature branch once
`git merge-base --is-ancestor` confirms its head is on `main`.

## Commands

From the repo root, with `.venv` active:

```
ruff check .
ruff format --check .
mypy
pytest
python .claude/hooks/check_sync.py                     # documents agree with the code
python .claude/hooks/check_sync.py --base origin/main  # and the version is raised
python .claude/hooks/dev_state.py                      # the feature in flight
```

## Automation in place

- **At session start**, `session_brief.py` reports a feature in flight and the skill that
  resumes it. Silent otherwise.
- **Before every `Bash` or `PowerShell` call**, `guard_git.py` refuses a commit or push that
  would land on `main`. `settings.json` pre-approves the git commands the pipeline uses so a
  round never stalls on a prompt; the guard is what keeps that safe.
- **After every `Write`/`Edit` of a `.py` file**, `lint_py.py` runs `ruff format` and
  `ruff check --fix` on it.
- **On every pull request and push to `main`**, CI runs the gate and `check_sync.py`.

`.claude/rules/python.md` loads whenever a `.py` file is read or edited.

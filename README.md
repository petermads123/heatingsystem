# Heating System

This repo is for implementing various heating system models. The first is `PIController`,
a discrete-time PI controller for radiator and floor-heating actuators, driven from
Home Assistant / AppDaemon on a fixed polling interval.

## Install as a dependency

```powershell
pip install git+https://github.com/petermads123/heatingsystem.git@main
```

Or in `dependencies` in `pyproject.toml`:

```toml
"heatingsystem @ git+https://github.com/petermads123/heatingsystem.git@main"
```

## PIController usage

Install the package (see above), then:

```python
import heatingsystem as hs

# --- Radiator controller (continuous output in [0.0, 1.0]) ---
radiator = hs.PIController(
    kp=0.3,
    ki=0.015,
    mode=hs.HeatingMode.RADIATOR,  # or just mode="radiator"
    setpoint=21.0,  # target room temperature in °C
    history_length=24,  # rolling window length (default 24 steps)
)

# In Home Assistant / AppDaemon this callback fires every 5 minutes.
# Pass the current room temperature; get back a valve-open fraction [0, 1].
measured_temp = 19.5  # e.g. read from a sensor entity
output = radiator.update(measured_temp)
print(f"Radiator valve: {output:.2f}")  # e.g. 0.46

# Pass a new setpoint with the call; it replaces the stored setpoint from here on:
output = radiator.update(measured_temp, setpoint=22.0)

# Inspect the last 24 issued commands (oldest first) and the mean ON-fraction:
print(radiator.history)  # tuple of floats, length <= history_length
print(radiator.duty_cycle)  # mean of the rolling window; 0.0 if empty
print(radiator.is_history_full)  # True once history_length steps have been issued

# --- Floor-heating controller (binary output: 0.0 or 1.0) ---
# Uses the same internal PI logic but quantises to on/off each step.
# Over a full window the duty_cycle converges to within about one slot (1 / history_length)
# of the equivalent continuous output.
floor = hs.PIController(
    kp=0.3,
    ki=0.015,
    mode=hs.HeatingMode.FLOOR_HEATING,  # or mode="floor_heating"
    setpoint=21.0,
)

measured_temp = 20.0
command = floor.update(measured_temp)  # 0.0 (off) or 1.0 (on)
print(f"Floor heating: {'ON' if command else 'OFF'}")
print(f"Duty cycle over window: {floor.duty_cycle:.2f}")

# --- Reset (start the loop afresh while keeping every setting) ---
radiator.reset()  # zeroes the integral accumulator and clears the history window

# --- Fixed output override (e.g. hold the valve shut during a price spike) ---
# Any real number in [hs.OUTPUT_MIN, hs.OUTPUT_MAX] is accepted. Out of range or non-finite
# raises ValueError; a string, bool or other non-number raises TypeError.
radiator.fixed_output = 0.0  # update() now returns 0.0 regardless of measured_temp
output = radiator.update(measured_temp)
print(radiator.pi_output)  # the PI demand the loop would have issued, unheld
radiator.fixed_output = None  # release the override; update() resumes the PI result

# --- State snapshot (e.g. survive a Home Assistant restart or AppDaemon reload) ---
# to_dict() captures every setting and every piece of running state as a plain dict
# of built-in types, which json.dumps accepts directly. Store it however suits you —
# a file, a Home Assistant entity attribute — and hand it back to from_dict() on
# startup to rebuild an equivalent controller. A missing, unknown or invalid key
# raises ValueError, TypeError or OverflowError naming it, and no controller is produced.
state = radiator.to_dict()
radiator_restored = hs.PIController.from_dict(state)

# On startup: restore if a snapshot exists and is usable, otherwise start fresh.
try:
    radiator = hs.PIController.from_dict(state)
except (TypeError, ValueError, OverflowError):
    radiator = hs.PIController(kp=0.3, ki=0.015, setpoint=21.0)
```

The actuator mapping behind `update()` — the mode, the rolling history window, the duty
cycle and the `fixed_output` hold — lives in its own `hs.Modulator` class, reusable by any
future model that drives the same actuator range without reimplementing it. It can also be
driven on its own, for example by a demand that comes from a schedule or a slider rather
than a PI loop:

```python
floor_valve = hs.Modulator("floor_heating", history_length=24)

command = floor_valve.command(0.25)  # a demand level in [0, 1] -> 0.0 or 1.0 this step
print(floor_valve.duty_cycle)  # the realised ON fraction over the window so far
floor_valve.fixed_output = 0.0  # hold the floor off; command() now returns 0.0
floor_valve.fixed_output = None  # release the hold
floor_valve.reset()  # clear the window; mode, history_length and the hold stay
```

A few things to know when the snapshot meets your own configuration on the next startup:

- **Your configuration wins.** The snapshot carries the settings it was saved with. Restore
  first, then assign `kp`, `ki`, `setpoint`, `mode` and `fixed_output` from your config;
  the running state is kept and the settings are yours.
- **A shrunk window needs the history trimmed first.** `history_length` is the one setting
  that cannot be re-applied after restore, and a history longer than the window is
  refused. If you changed it from 24 to 12, set `state["history_length"] = 12` and
  `state["history"] = state["history"][-12:]` before calling `from_dict`.
- **Keep your own metadata outside the dict.** An unknown key is refused, so store
  something like `{"saved_at": ..., "controller": radiator.to_dict()}` rather than adding
  keys to the snapshot itself.
- **A stale snapshot is restored, then reset.** After a long outage the saved integral and
  window describe a room that no longer exists. `from_dict` followed by `reset()` keeps the
  settings and any hold and discards the running state.

The PI loop keeps running underneath a hold: the integral goes on accumulating the error
the room builds up, and the fixed commands are recorded in `history` like any others. So
when the hold is released the controller does not resume gently. A radiator jumps to the
demand that built up during the hold, and floor heating fires several slots in a row
because its duty-cycle window is still full of the held commands. That burst is usually
what you want after a cold spell, since the room needs the heat it went without. If you
would rather resume from a clean state, call `reset()` at release.

`test.py` at the repo root runs a closed-loop simulation of both modes and plots the
result. It needs matplotlib, which is installed with the `sim` extra:

```powershell
pip install -e ".[dev,sim]"
python test.py
```

## Layout

Everything installable lives under `src/`, so nothing else in the repo — `tests/`,
`docs/`, the simulation script — can be picked up as a package by accident, and an import
in a test resolves against the installed package rather than against whatever happens to
sit in the working directory.

The trade-off: the repo root is not on `sys.path`, so `python -m heatingsystem.<module>`
only works once you have run `pip install -e ".[dev]"`. `pytest` is unaffected —
`pythonpath` in `pyproject.toml` points it at `src`.

New models go in their own subpackage under `src/heatingsystem/` and are re-exported from
`src/heatingsystem/__init__.py`. `STRUCTURE.md` maps what lives where.

## Development

### Prerequisites

All installable from PowerShell — no website visits needed:

```powershell
winget install --id Git.Git --source winget
winget install --id Python.Python.3.13 --source winget
code --install-extension ms-python.python
code --install-extension charliermarsh.ruff
```

The Python version must satisfy `requires-python` in `pyproject.toml`. To install a
different one, replace the version in the package id, e.g. `Python.Python.3.14`.

`winget` does not update the PATH of the shell it ran in. Open a new terminal before
continuing, then check with `python --version`.

### Create the environment

From the repo root:

```powershell
python -m venv .venv
```

```powershell
.\.venv\Scripts\Activate.ps1
```

```powershell
python -m pip install --upgrade pip
```

```powershell
pip install -e ".[dev]"
```

The `[dev]` part installs Ruff, mypy and pytest. Without it you get the package only.

In VS Code the environment activates automatically in new terminals once the interpreter is
selected. If it is not picked up, use `CTRL + Shift + P` -> `Python: Select Interpreter` and
choose the one in `.venv`.

### Verify

All four must pass on a fresh clone:

```powershell
ruff check .
```

```powershell
ruff format --check .
```

```powershell
mypy
```

```powershell
pytest
```

### Editor

`.vscode/settings.json` is checked in, so format-on-save, import sorting and Ruff as the
Python formatter are already configured for this repo. Installing the Ruff extension (see
Prerequisites) is all that is required.

## Working with Claude Code

This repo ships a Claude Code configuration under `.claude/`, plus `CLAUDE.md` (a routing
map, loaded every session) and `STRUCTURE.md` (a map of what lives where, imported by
`CLAUDE.md`). It was adopted from `petermads123/template_repo`.

### The commands

These are the ones you would type. Everything else under `.claude/skills/` is a step the
pipeline opens by itself, and you rarely need to know it is there.

| Command | Use it for |
|---|---|
| `/feature <what to build>` | Anything new or changed that is not cosmetic: a module, a public function, a behaviour change. Opens the ten-step pipeline below at step 1. With no argument, reports where an in-flight feature got to. |
| `/fix <symptom>` | Something that exists behaves wrongly: wrong output, a crash, a guard that lets something through. Reproduces it, finds the root cause and sizes what else the cause breaks **before** the pipeline opens, then runs the same ten steps as a fix round. Sends you to `/feature` or `/small-change` instead if the diagnosis says it is not a bug. |
| `/small-change <what to change>` | Cosmetic edits with none of the pipeline: a local rename, a docstring reword, message text, plot styling, formatting. Refuses anything that adds or removes a file, changes a public signature, changes behaviour or needs a new test. |
| `/build` | Resume a build that halted to ask you something, once you have answered. |
| `/recommend` | Re-open the follow-up decisions at step 8, if a session ended with them undecided. |
| `/create-pr` | Open the pull request for a finished branch, if you did not do it in the session that finished it. |
| `/watch-pr` | Resume watching an open pull request in a fresh session. |

You do not have to type any of them: describe the work in prose and Claude picks the route,
saying which and why in one line. The routing rules are in `CLAUDE.md`.

### The implementation pipeline

Anything that is not cosmetic goes through ten steps. You decide three times — the concept,
the plan, and the follow-ups — and the build in between runs on its own. The state lives on
disk rather than in the conversation, and every step commits and pushes it, so a feature
survives closing the session and coming back tomorrow — one folder per branch, one numbered
file per round:

```
development/
  TEMPLATE.md
  feat/csv-export/
    01-csv-export.md          round 1, shipped
    02-streaming-writer.md    round 2, in flight
```

| Step | Command | Produces | Waits for |
|---|---|---|---|
| 1 | `/conceptualize` | The concept, agreed with you, numbered acceptance criteria, and the branch | you |
| 2 | `/plan` | Modules, full signatures, implementation guide, test intents | you |
| 3 | `/build` → `/implement` | The production code | — |
| 4 | `/build` → `/verify` | ruff, mypy, and a check that the code matches the plan | — |
| 5 | `/build` → `/test` | The edge-case suite, and fixes for what it finds | — |
| 6 | `/build` → `/concept-check` | An audit against step 1, criterion by criterion | — |
| 7 | `/build` → `/ship` | The round closed, whole tree green | — |
| 8 | `/recommend` | Ranked follow-ups, decided with you | you |
| 9 | `/create-pr` | A full re-verification of the whole branch, then a pull request to `main` | you, before it publishes |
| 10 | `/watch-pr` | An hourly check of the open PR, acting on comments, until it merges or closes | — |

**Steps 3 to 7 run without you.** Once you accept the plan, `/build` runs implement, verify,
test, concept-check and ship in order, each in its own subagent on the model that step
pins, committing and pushing after each. It halts for exactly two things: a finding that
would change the concept you agreed to, and a check that fails twice the same way after one
fix. A halt commits what exists, writes the question into the plan file, and waits; `/build`
resumes once you answer. While it runs you get a trace — a line or two per module, class,
function and test group as each step lands — so you can see what was built without reading
the diff.

Where the work genuinely diverges, more than one agent reads it: a diagnosis critic tries
to falsify the root cause before a fix is agreed, a plan critic reads the plan against the
concept before you accept it, two test designers with different briefs find the edge cases
at step 5, and three brainstormers with different lenses propose the follow-ups at step 8.
The calling step merges what they find and stays the single voice.

Step 9 requests your review on the PR it opens. **Claude never merges on its own judgment,
and never on an approval alone** — a PR reaches `main` either because you pressed the button
or because you explicitly told Claude to. An approval says the change is wanted, not that it
should ship now. Once told, the instruction still waives nothing: it must not be stale
(anything pushed since means you would be merging code you have not seen), no conflict, and
every review thread resolved. Once a pull request has merged, by either route, Claude
deletes its branch as part of closing out, after confirming the head is on `main`.

Note that **GitHub lets nobody request a review from, or approve, their own pull request**.
In a solo repo, where Claude pushes under your token, every PR is authored by you — so the
review route is unavailable, step 9 assigns you instead, and the merge signal is an explicit
"merge it" from you. `CLAUDE.md` names the approver.

Start with `/feature <what to build>` — it opens step 1. Your confirmation of the concept
opens step 2 in the same turn, and your acceptance of the plan opens the build. After the
build, step 8 ends on a question, and step 9 asks before it publishes.

A bug starts with `/fix <symptom>` instead, and gets a diagnosis before step 1: the symptom
reproduced and its output quoted, the root cause as a file and line, the commit that
introduced it, the other inputs the same cause breaks, and who depends on the current
behaviour — read a second time by a critic whose job is to find a different cause. Only
then does step 1 open, and its first question is the one every fix has: this instance, or
the whole class? The rest of the pipeline is the same, with differences you will see in
the trace: the build writes the reproduction as a test and runs it red before fixing,
and halts if it is not red; the concept check has to show more than a green suite for
"nothing else changed"; and the follow-ups get a fourth reader asking where else the same
cause lives and what should have caught it. If the diagnosis finds that the code does what
was agreed and you want something different, `/fix` says so and hands over to `/feature`.

**You do not have to type the commands.** Describe the work in prose — "I want to add a
thermostat model", "rename that variable" — and Claude classifies it against the small-or-large test
before doing anything: a local rename with no signature or behaviour change is small, and
anything that adds a file, changes a public signature, changes behaviour or needs a test is
not — and a bug is a third thing, routed to `/fix` because the first question it raises is
whether it is a bug at all. It says which way it routed and why in one line, so a wrong
call costs you a sentence to correct, and asks only when the request is genuinely
borderline. When it is close, it routes up to the pipeline, because step 1 is a
conversation you can redirect — whereas a feature handled as a small change quietly skips
the concept, the tests and the audit.

A slash command still wins if you type one, and a question stays a question: asking how
something works gets an answer, not a pipeline.

Each step also picks its own model. Concept, planning and recommendations run on Opus
because they are judgment; implementation, verification, tests, the concept check and the
pull request run on Sonnet at max effort because the thinking has already been done and
written down. Steps 3 to 7 run as subagents for exactly this reason — a skill's model
override lasts the whole turn, so five steps chained in one turn would all run on the first
one's model. `/small-change` runs on Opus too — deciding a change is small enough to skip
the pipeline is the one judgment made without the pipeline to catch it. `CLAUDE.md` has
the table.

Step 9 is not a formality. It is the only point where the branch is verified as a whole:
step 7 checked one round at one moment, so on a multi-round branch nothing has yet proved
that round 2 left round 1 working. It re-runs the full suite, every module showcase and
every round's plan against a clean tree and a current `main`, and it marks the plan `done`
in the commit that opens the pull request — so the review lives on the pull request thread
and no commit ever exists just to tidy up afterwards.

Two things are worth knowing about the shape of it. **Step 6 audits against step 1, not
step 2**: a plan can drift from its concept a little at each step while passing every check
along the way, and this is where that gets caught. And **step 8 is where new scope belongs**
— ideas that turn up during steps 1 to 7 are a distraction, but with the finished feature in
front of you they are a decision. A recommendation you accept opens a new numbered file in
the same folder and goes back through steps 1 to 7 on the same branch, so one pull request
can carry several deliberate passes over one feature. Step 6 of a later round re-checks the
earlier rounds' acceptance criteria, so a follow-up cannot quietly regress what it builds on.

### Hooks

Four run automatically:

- **Session start** — reports the active plan and its step, so a new session picks up where
  the last one stopped. Silent when nothing is in flight.
- **Before any shell command** — refuses a `git commit` or `git push` that would land on
  `main`, including inside a `&&` chain.
- **After every `.py` write** — Ruff formats and auto-fixes the file; only unfixable issues
  come back.
- **Before a turn ends** — the stop gate. Through step 7 it only reports, because the build
  runs its own checks and has to be able to halt on a red tree; from step 8, and for any
  work with no plan file, it refuses to end a turn that changed Python while ruff, mypy or
  pytest fail or `STRUCTURE.md` is out of sync. Prose-only work is not gated. Create
  `.claude/.skip-gate` to bypass it.

Three caveats worth knowing:

- **Hooks are read at session start.** Editing anything under `.claude/hooks/` or
  `.claude/settings.json` needs a Claude Code restart. Skills and rules hot-reload.
- **The first session prompts for workspace trust**, because `.claude/settings.json`
  registers hooks. Accept it or the hooks stay inactive.
- The hooks call `python` from your PATH and only use the standard library; they locate
  `ruff`, `mypy` and `pytest` inside `.venv` themselves.

### Branch protection

`.claude/skills/repo-setup/main_protect.solo.json` is the ruleset for `main` in a solo
repo: deletion and force-push blocked, a pull request required, review threads resolved,
Copilot reviewing each push. It is not applied automatically. Import it under
*Settings → Rules → Rulesets → New ruleset → Import a ruleset*, or use
`main_protect.collab.json` once other people work here too.

### Troubleshooting

`Activate.ps1 cannot be loaded because running scripts is disabled` — allow local scripts
for your user:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

`python` not found right after `winget install` — open a new terminal so PATH is reloaded.

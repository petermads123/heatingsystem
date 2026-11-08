# Heating System

This repo is for implementing various heating system models. The first is `PIController`,
a discrete-time PI controller for radiator and floor-heating actuators, driven from
Home Assistant / AppDaemon on a fixed polling interval.

**Version:** 1.1.1

## Packages

| Package | Main entry points |
|---|---|
| `heatingsystem.pi_controller` | `PIController`, `HeatingMode`, `OUTPUT_MIN`, `OUTPUT_MAX` |
| `heatingsystem.modulator` | `Modulator` — the actuator mapping `PIController` composes |
| `heatingsystem.allocator` | `SectionAllocator`, `Room` — multi-room control of on/off sections |

All are re-exported from `heatingsystem` (`import heatingsystem as hs`).

## Install as a dependency

```powershell
pip install git+https://github.com/petermads123/heatingsystem.git@main
```

Or in `dependencies` in `pyproject.toml`:

```toml
"heatingsystem @ git+https://github.com/petermads123/heatingsystem.git@main"
```

The package depends on `numpy` and `scipy` (used by `SectionAllocator`); pip installs
both with it.

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
# error, p_term and i_term describe the last update exactly as it computed it (read them
# rather than recomputing: the setpoint or gains may have changed since, and under
# anti-windup the stored integral is not the one the output used). p_term + i_term is the
# raw PI sum before clamping; all three are None before the first update and after reset().
print(radiator.error, radiator.p_term, radiator.i_term)
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

## SectionAllocator usage

`hs.SectionAllocator` controls on/off heating sections that may each heat more than one
room. It is built from a layout (changeable with `with_layout`, below): rooms with a priority (in `(0, 1]`, 1 = most important, only ratios matter) and an evenness
weight (in `[0, 1]`), and sections with their **coverage** of each room they serve: the
fraction of that room's floor heating the section provides, in `(0, 1]`. Each `update`
takes one temperature per room and returns a `0.0`/`1.0` command per section.

```python
import heatingsystem as hs

# HS2 provides 30 % of R1's floor and 30 % of R2's. Evenness 0 means that room's floor
# evenness does not matter; a weight above 0 pulls its sections' duty cycles together
# (it counts as priority x evenness, so 1 prices an uneven floor like a temperature miss).
rooms = {
    "R1": {"priority": 1.0, "evenness": 0.1},
    "R2": {"priority": 1.0, "evenness": 0.0},
}
sections = {
    "HS1": {"R1": 0.7},
    "HS2": {"R1": 0.3, "R2": 0.3},
    "HS3": {"R2": 0.7},
}
allocator = hs.SectionAllocator(rooms, sections, kp=0.3, ki=0.015, setpoint=21.0)

# Every 5 minutes: one temperature per room in, one command per section out.
commands = allocator.update({"R1": 20.9, "R2": 20.4})  # {"HS1": 1.0, "HS2": 1.0, ...}
print(allocator.duty)  # last allocated duty per section, in [0, 1]
print(allocator.rooms["R2"].demand)  # last PI demand of R2
# For a dashboard, read the terms of the last PI step rather than recomputing them:
print(
    allocator.rooms["R2"].error,
    allocator.rooms["R2"].p_term,
    allocator.rooms["R2"].i_term,
)

# A dead sensor: None means "no reading". R2's PI takes no step and the allocation
# reuses its last demand (0.0 if it never had a reading); R1 steps as usual.
commands = allocator.update({"R1": 20.9, "R2": None})

# Setpoint and gains are adjustable per room, effective at the next update.
allocator.rooms["R1"].setpoint = 19.0
allocator.rooms["R1"].kp = 0.5

# Hold a section at a fixed level (relay locked open or shut, pump lock, ...);
# the other sections compensate. None releases it.
allocator.hold("HS1", 0.0)
print(allocator.holds)  # {"HS1": 0.0, "HS2": None, "HS3": None}
allocator.hold("HS1", None)

# Snapshot and restore, as for PIController; holds are part of the snapshot.
state = allocator.to_dict()
allocator = hs.SectionAllocator.from_dict(state)
```

**No reading.** A room's temperature may be `None` ("no reading this step"; the key must still
be present). That room's PI takes no step, so its `integral` and `demand` stay as they were,
and the allocation uses its last demand, or 0.0 before its first reading (`demand` itself stays
`None` until then, and after `from_dict`). Every other room steps normally and every section
still gets a command. Deciding when a sensor has been dead too long, and closing its sections,
is up to the caller, with `hold`.

**Coverage.** `sections[s][r]` is the fraction of room `r`'s floor heating that section `s`
provides. A room's coverages must sum to at most 1 (tolerance 1e-9); a section may cover
several rooms with any sum. What a room's sections do not cover (the other half of a room
only half served, say) is an outside disturbance like the weather. Internally each room's
coverages are normalised by its total, so a demand of 1.0 means "every loop of this room
fully on", and a small section can never be believed to heat a whole room. A room served
by one section of any coverage behaves like a standalone floor-heating `PIController`.
`allocator.sections` returns the coverages as you gave them, not normalised. Snapshots
from 1.0.0 (where `sections[s][r]` was the share of the section's heat reaching the room, and
the snapshot key was `shares`) are refused, not misread.

**Holds.** `hold(section, level)` fixes a section's duty at `level` in `[0, 1]` (validated like
`PIController.fixed_output`); `hold(section, None)` releases it, and `holds` reports every
section's level or `None`. A held section's duty is its level at every `update`; its commands
are the floor-heating modulation of that level (exactly the level for 0.0 and 1.0, a 0/1
pattern averaging the level over the window otherwise) and its window records them. The free
sections are solved with the held contribution subtracted, and a group of sections that are
all held calls no solver. Every room's PI keeps running during a hold, as `PIController` does
under `fixed_output`: a room that stays cold while a section is held keeps integrating, so on
**release** the next `update` can show a demand burst. Nothing in `update` changes a hold.

**Changing the layout.** An allocator's layout (rooms, priorities, evenness weights, coverages)
is read-only, but `allocator.with_layout(rooms, sections, history_length=n)` returns a **new**
allocator with a changed layout (an argument left `None` keeps the current one; the original is
not changed). State carries over by name: a room in both layouts keeps its `setpoint`, `kp`,
`ki`, integral and last `demand`; a section in both keeps its command window (trimmed to its
newest `n` slots) and its hold; `duty` carries when the section names are unchanged and in the same order (otherwise it is `None` until the next `update`). A new room
starts with the constructor defaults, a new section with an empty window and no hold, and a
layout the constructor would refuse raises the same error. Unlike `from_dict`, a carried room's
`demand` is kept, so a room with no reading still allocates its last demand. Adding or removing a room
usually needs both `rooms` and `sections`.

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

This repo ships a Claude Code configuration under `.claude/`, plus `CLAUDE.md` (the routing
map, loaded every session) and `STRUCTURE.md` (every module and public signature, imported by
`CLAUDE.md`). The pipeline is the one from `petermads123/template`.

Ask for a feature in plain words, or `/feature <what to build>`; report a bug the same way,
or `/fix <symptom>`. Claude routes it and says which route in one line.

| Stage | Skill | What happens | Waits for you |
|---|---|---|---|
| Concept | `/feature` | The concept, the branch name and the version bump are agreed; repo scouts investigate in the background; `BRIEF.md` splits the work into rounds | the concept and the rounds |
| Diagnosis (a bug) | `/fix` | The symptom reproduced as a failing test, causes hunted from three angles, one root cause proven and criticised | the cause, the scope and the bump |
| Each round | `/round` | Conceptualize, plan, implement, verify with parallel reviewers, four test designers, write and run the tests, three concept checkers, merge into the feature branch | only a question the round cannot answer |
| Finish | `/finish` | Whole-feature check, extras, the version, `STRUCTURE.md`, `README.md` and `DEVLOG.md` written, the pull request opened | only a critical extra |

State lives under `development/<branch>/` (`BRIEF.md`, `brief.json`, one `r<n>-<slug>/`
folder per round) and is committed after every step, so a session can be closed and resumed.
`development/README.md` documents the JSON files; the older numbered `NN-*.md` plan files
there are the history of the previous pipeline.

Claude never merges on its own judgment or on an approval alone: a pull request reaches
`main` because you pressed the button or told Claude to merge it. Every version is recorded
in `DEVLOG.md`.

### Hooks and CI

- **Session start** — reports a feature in flight and the skill that resumes it.
- **Before any shell command** — refuses a `git commit` or `git push` that would land on `main`.
- **After every `.py` write** — Ruff formats and auto-fixes the file.
- **CI** (`.github/workflows/check.yml`) — on every pull request and push to `main`, runs the
  four checks above and `python .claude/hooks/check_sync.py`, which checks that the version,
  `README.md`, `DEVLOG.md` and `STRUCTURE.md` agree with each other and the code.

Hooks are read at session start, so a change under `.claude/hooks/` or to
`.claude/settings.json` needs a Claude Code restart, and the first session asks for
workspace trust. The hooks run the first of `python3` or `python` that actually starts, so
the Windows Store alias stub is skipped.

### Branch protection

`main` is protected by the `main_protect` ruleset: no deletion or force-push, and changes
arrive by pull request. To require CI as well, import
`.claude/skills/setup/main_protect.solo.json` (or `.collab.json`) from `petermads123/template`:
delete the current ruleset, then *Settings → Rules → Rulesets → New ruleset → Import a ruleset*.

### Troubleshooting

`Activate.ps1 cannot be loaded because running scripts is disabled` — allow local scripts
for your user:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

`python` not found right after `winget install` — open a new terminal so PATH is reloaded.

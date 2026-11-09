# Structure

The map of this repo: every module, and every public class and function with its signature
and return type. Read it before opening source files. `/finish` rewrites it from the code once
per feature; `check_sync.py` fails when a Python file under `src/`, `tests/` or
`.claude/hooks/` is missing from it or named here without existing. Private names (`_x`) stay
out.

The package has three subpackages; once this file grows unwieldy, per-subpackage detail can
move into `.claude/rules/structure-<subpackage>.md` with `paths: ["src/heatingsystem/<subpackage>/**"]`
so it loads only when Claude works there (see `DEVELOPMENT.md`).

## Tree

```
src/heatingsystem/      the package: heating-system models
  allocator/            subpackage: multi-room control of on/off sections (PI per room, bounded least-squares allocation)
  modulator/            subpackage: the actuator mapping (mode, window, duty cycle, hold)
  pi_controller/        subpackage: the discrete-time PI controller
tests/                  pytest suite, one test_<module>.py per module
test.py                 closed-loop simulation and plot of the controller (matplotlib)
development/            the pipeline's state: one folder per feature branch (see its README)
.claude/
  skills/               /feature, /fix, /round, /finish
  agents/               the pipeline's agents, one file each
  workflows/            diagnose.js (a fix's diagnosis), round.js (one round), final.js (the whole-feature check)
  hooks/                session brief, git guard, Ruff on edit, state reader, sync checker, test selector
  rules/python.md       coding conventions, loaded for **/*.py
.github/workflows/      CI: the gate and check_sync
.vscode/                editor config (Ruff as formatter, format on save)
pyproject.toml          package, version, Ruff/mypy/pytest config
CLAUDE.md               routing map and rules for Claude
README.md               version, packages, usage and setup guide
DEVLOG.md               one entry per version
DEVELOPMENT.md          open development-side questions and things to fix later
STRUCTURE.md            this file
```

## Package: `src/heatingsystem/`

### `src/heatingsystem/__init__.py`

Package entry point. Re-exports the public API so callers write `import heatingsystem as
hs` and use `hs.PIController` and `hs.HeatingMode` rather than reaching into modules.
Every new model subpackage is re-exported from here.

| Export | From |
|---|---|
| `HeatingMode` | `heatingsystem.modulator` |
| `Room` | `heatingsystem.allocator` |
| `SectionAllocator` | `heatingsystem.allocator` |
| `Modulator` | `heatingsystem.modulator` |
| `PIController` | `heatingsystem.pi_controller` |
| `OUTPUT_MIN` | `heatingsystem.modulator` |
| `OUTPUT_MAX` | `heatingsystem.modulator` |

Every package directory under `src/`, including every subpackage added later, needs one of
these. A directory of modules without it is not a package, so it will not install.

### `src/heatingsystem/_validation.py`

Private module: the numeric contract behind every validating setting on `Modulator`,
`PIController`, `Room` and `SectionAllocator`, stated once so it does not drift between the classes' docstrings. Not a
subpackage — a single module at the package root, imported by `modulator.py`,
`pi_controller.py` and `allocator.py` as `from heatingsystem import _validation`, so a helper name can never be
shadowed by a local or a parameter (`finite`/`level` are called as `_validation.finite`/
`_validation.level`, never imported by name).

| Signature | Description |
|---|---|
| `finite(name: str, value: object) -> float` | Validate a finite real number (`bool` excluded), representable as a `float`; `-0.0` normalised to `+0.0`. Raises `TypeError` naming `name` for a non-numeric or `bool` value, `ValueError` for a non-finite one, `OverflowError` for one too large to represent as a `float`. |
| `window_length(value: object) -> int` | Validate a rolling-window length: an `int` (`bool` excluded) of at least 1 and at most `sys.maxsize`. Raises `TypeError` naming `history_length` for `bool` or a non-`int`, `ValueError` below 1, `OverflowError` above what a `deque`'s `maxlen` can hold. |
| `level(name: str, value: object, lower: float, upper: float) -> float` | Builds on `finite`, adding an inclusive `[lower, upper]` range check — the bounds are parameters rather than imported constants, so this module has no dependency on `modulator.py`. Raises the same `TypeError`/`OverflowError` as `finite`, plus `ValueError` naming `name` for a value outside `[lower, upper]`. |
| `snapshot_mapping(data: object, keys: frozenset[str]) -> Mapping[str, object]` | Validate a snapshot as a `Mapping` with exactly `keys`; missing keys reported before unknown ones (both sorted, unknown by `repr` since a mixed-type key set may not compare with `<`). Raises `TypeError` for a non-mapping naming its type, `ValueError` naming the missing or unknown keys. Returns `data` unchanged. |
| `main() -> None` | Showcase: `finite` normalising `-0.0`, a `level` range refusal and a `snapshot_mapping` missing-key refusal. |

Runnable standalone: `python -m heatingsystem._validation`.

### `src/heatingsystem/allocator/__init__.py`

Subpackage entry point. Re-exports `SectionAllocator` and `Room` from `allocator.py`.

### `src/heatingsystem/allocator/allocator.py`

Multi-room control of on/off heating sections that may each serve several rooms. Built
from a layout, changeable through `with_layout` (rooms with a priority and an evenness weight; sections with a
*coverage* per served room: the fraction of that room's floor heating the section provides,
in `(0, 1]`, a room's coverages summing to at most 1 with the rest an outside disturbance;
each room's coverages are normalised by its controlled total, so demand 1.0 means every
loop of the room fully on). A section can be held at a fixed level (`hold`). Every `update` runs, per room, a composed radiator-mode
`PIController` (`history_length=1`) for the demand; allocates section duty cycles by
bounded weighted least squares (`scipy.optimize.lsq_linear`, `method="bvls"`) minimising exactly
`J(u) = sum_r p_r (d_r - h_r)^2 + sum_r p_r e_r spread_r` (`p` priority in `(0, 1]`, `e` evenness
in `[0, 1]`, `h_r = sum_s (normalised coverage) u_s` the heat room r receives,
`spread_r = sum((u_s - mean)**2)` over its sections, a held section entering with its level),
split by connected component of the room-section graph. A one-room, one-section component
has a closed form, `min(1, demand)`, or the held level when its section is held. Every
other component is solved by least squares over its free sections only: each held
section's column is split off at solve time and its contribution (column times level) is
subtracted from the right-hand side, demand and evenness rows alike, and a component with
no free section calls no solver. Every row weight (a priority, or priority times evenness)
is divided by the component's largest priority and floored at 1e-12 of it, as a safety net
so a very low-priority room's feasible demand is never lost to round-off, and the solver's
status is checked. Finally each duty becomes 0.0/1.0 through one floor-heating `Modulator`
per section. First module with runtime dependencies (`numpy`, `scipy`). The component
split, matrix assembly, coverage normalisation, the solve step and the layout validation
are private helpers and are omitted per this file's convention.

| Signature | Description |
|---|---|
| `Room(name: str, *, priority: float, evenness: float, kp: float, ki: float, setpoint: float)` | One room, built by `SectionAllocator` (construction is internal). Validates `name` (non-empty `str`), `priority` (finite, in `(0, 1]`), `evenness` (finite, in `[0, 1]`); a value outside raises `ValueError` naming the attribute and repeating the caller's value; composes the radiator-mode `PIController`. |
| `Room.name -> str` | Read-only. |
| `Room.priority -> float` | Read-only. |
| `Room.evenness -> float` | Read-only. |
| `Room.setpoint -> float` (settable) | Delegates to the composed `PIController`; same contract and message. |
| `Room.kp -> float` (settable) | Delegates to the composed `PIController`. |
| `Room.ki -> float` (settable) | Delegates to the composed `PIController`. |
| `Room.integral -> float` | Read-only; the PI integral. |
| `Room.demand -> float \| None` | Read-only; the clamped PI demand of the last PI step (unchanged by a `None` reading), `None` before the first real reading and after `from_dict`. |
| `Room.error -> float \| None` | Read-only; delegates to the composed `PIController`. `None` before the first real reading and after `from_dict`; unchanged by a `None` reading, restored by a failed `update`, carried by `with_layout` (`None` for a new room). |
| `Room.p_term -> float \| None` | Read-only; delegates, same cases. |
| `Room.i_term -> float \| None` | Read-only; delegates, same cases. |
| `SectionAllocator(rooms: Mapping[str, Mapping[str, float]], sections: Mapping[str, Mapping[str, float]], *, kp: float = 0.3, ki: float = 0.015, setpoint: float = 21.0, history_length: int = 24)` | The controller. `rooms` maps name to `{"priority" in (0, 1], "evenness" in [0, 1]}`; `sections` maps name to `{room: coverage}`, each coverage in `(0, 1]`. Shared `kp`/`ki`/`setpoint` are validated first and named as the argument passed; every layout error names its path (`sections['HS1']['R1']`); an uncovered room is named; then a room whose coverages sum above `1 + 1e-9` raises `ValueError` naming it and the sum (`rooms['R3'] coverages sum to 1.1, more than 1.`, first in room order); a section covering several rooms may sum to anything; nothing is built on failure. |
| `SectionAllocator.update(measured: Mapping[str, float \| None]) -> dict[str, float]` | One step: a temperature per room in (`None` = no reading: that room's PI takes no step, its integral and `demand` stay, and the allocation uses its last demand, 0.0 before its first; the key must still be present), a `0.0`/`1.0` command per section out, in section order. Validates everything before any state changes: `TypeError` for a non-mapping `measured` or a non-numeric/`bool` temperature (`None` is accepted), `ValueError` for a missing or unknown room (missing named first) or a non-finite temperature, `OverflowError` for one too large for a `float`; a bad temperature names `measured['<room>']`, a missing or unknown room is reported as the list of names (`measured is missing rooms [...]` / `measured has unknown rooms [...]`), and a non-mapping names `measured`. If the allocation fails (`ArithmeticError` for a non-finite or non-converged solver result), every room's integral, demand, `error`, `p_term` and `i_term` are restored and the error propagates; no command is issued and `duty` is unchanged. A held section's duty is its level and its command the floor-heating modulation of that level (exactly the level for 0.0 and 1.0); the free sections compensate; every room's PI keeps stepping during a hold (a burst can follow a release); `update` never writes a hold. |
| `SectionAllocator.rooms -> Mapping[str, Room]` | Read-only `MappingProxyType` of the `Room` handles. |
| `SectionAllocator.sections -> dict[str, dict[str, float]]` | Read-only; a fresh deep copy of the coverages as given (not normalised). |
| `SectionAllocator.hold(section: str, level: float \| None) -> None` | Hold a section at `level` in `[0, 1]`, or release it with `None`. A non-`str` section raises `TypeError`, an unknown name `ValueError` listing the known sections; `level` is validated like `Modulator.fixed_output` (`TypeError`/`ValueError`/`OverflowError` naming `holds['HS3']`, repeating the caller's value). Stored in the section modulator's `fixed_output`; takes effect on the next `update`, leaves `duty` and `history` untouched, and a raising call changes nothing. |
| `SectionAllocator.holds -> dict[str, float \| None]` | Read-only; a fresh dict with every section in constructor order, its held level or `None`. |
| `SectionAllocator.history_length -> int` | Read-only. |
| `SectionAllocator.duty -> dict[str, float] \| None` | Last allocated duty per section (a held section reports its level); `None` before the first `update`, after `from_dict`, and after a `with_layout` whose section names or order differ. |
| `SectionAllocator.history -> dict[str, tuple[float, ...]]` | Each section's command window, oldest first. |
| `SectionAllocator.to_dict() -> dict[str, object]` | JSON-friendly snapshot: `history_length`, `rooms` (`priority`, `evenness`, `setpoint`, `kp`, `ki`, `integral`) and `sections` (`coverage` as given, `history`, `hold`), fresh containers. |
| `SectionAllocator.from_dict(data: Mapping[str, object]) -> Self` (classmethod) | Rebuild from a snapshot: key sets checked at every level (missing before unknown), the layout through the constructor (so a layout error is the constructor's, naming the constructor's path, e.g. `sections['HS1']['R1']` for a bad coverage), settings through the setters, each section's hold validated (after the constructor, before its window) and its window restored into a fresh floor-heating `Modulator` whose `fixed_output` is the hold; setting, hold and window errors keep their class with the snapshot path prefixed (`rooms['R1'].integral`, `sections['HS1'].hold`, `sections['HS1'].history[0]`). A 1.0.0 snapshot (`shares` instead of `coverage` and `hold`) is refused for the missing keys. `duty` and every room's `demand` are `None` afterwards. |
| `SectionAllocator.with_layout(rooms: Mapping[str, Mapping[str, float]] \| None = None, sections: Mapping[str, Mapping[str, float]] \| None = None, *, history_length: int \| None = None) -> Self` | A new allocator (same type) with the given layout, an argument left `None` keeping the current one; built through the constructor, so it raises what the constructor raises (even with a path naming a defaulted argument) and the original is never changed. Carries by name: a room's `setpoint`, `kp`, `ki`, integral, last `demand` and last `error`/`p_term`/`i_term` (priority and evenness from the new `rooms`), a section's window (newest `history_length` slots) and hold; `duty` only when the section names are identical in order. A new room takes the constructor defaults (`kp` 0.3, `ki` 0.015, `setpoint` 21.0, `demand` `None`), a new section an empty window and no hold. |
| `main() -> None` | Showcase: the reference layout (HS1-HS5 over R1-R4) with worked example E1, E2 (HS3 held shut, then released), a `None` reading for R3 with its kept `demand` and last `error`/`p_term`/`i_term`, E3 (R1 normalisation) and E4 (evenness), a JSON snapshot round trip carrying a hold, a `with_layout` change (R3 priority, shorter window) and a room whose coverages sum above 1. |

Runnable standalone: `python -m heatingsystem.allocator.allocator`, once the package is
installed.

### `src/heatingsystem/modulator/__init__.py`

Subpackage entry point. Re-exports `Modulator`, `HeatingMode`, `OUTPUT_MIN` and
`OUTPUT_MAX` from `modulator.py`, so the package root can import from the subpackage
without naming the module.

### `src/heatingsystem/modulator/modulator.py`

The actuator mapping as its own reusable object: owns the heating mode, the rolling
command-history window, the duty cycle and the fixed-output hold, and maps a demand level
to a mode-specific command. Nothing here is PI-specific — `PIController` composes one
rather than implementing the mapping itself, and any future model driving the same
actuator range can reuse it the same way. `HeatingMode`, `OUTPUT_MIN` and `OUTPUT_MAX` live
here (moved from `pi_controller.py`, same objects, still re-exported from every import
path a caller used before).

| Signature | Description |
|---|---|
| `OUTPUT_MIN: float = 0.0` | Lower clamp of the actuator range. |
| `OUTPUT_MAX: float = 1.0` | Upper clamp of the actuator range. |
| `HeatingMode(StrEnum)` | `RADIATOR` (continuous output in `[0, 1]`) or `FLOOR_HEATING` (binary on/off from duty-cycle modulation over the rolling window). |
| `Modulator(mode: HeatingMode \| str = HeatingMode.RADIATOR, *, history_length: int = 24, fixed_output: float \| None = None)` | The actuator mapping. `mode` and `fixed_output` are assigned through their validating properties, so a bad value raises the same error at construction as later; `history_length` is validated by the same numeric-family contract as `PIController`'s and stored as a read-only property; the window cannot be resized afterwards. |
| `Modulator.command(level: float) -> float` | Map a demand level to this step's command: the fixed level replaces `level` when `fixed_output` is set; radiator mode passes the target through; floor-heating mode duty-cycle-modulates it, reading the window before appending. Appends the command to `history` and returns it. Raises `TypeError` naming `level` for a non-numeric or `bool` value, `ValueError` for a non-finite or out-of-range one, `OverflowError` for one too large to represent as a `float`. |
| `Modulator.reset() -> None` | Clear the history window. `mode`, `history_length` and `fixed_output` are left unchanged. |
| `Modulator.mode -> HeatingMode` (settable) | Actuator mode. Setter accepts a `HeatingMode` or its string value via a shared coercion helper; anything else raises `ValueError` naming `mode` and listing the valid values, previous mode unchanged. |
| `Modulator.history_length -> int` | Read-only. The rolling command-window length given at construction; sized once, not resizable — a restore builds a new modulator instead. |
| `Modulator.fixed_output -> float \| None` (settable) | The current output override in `[OUTPUT_MIN, OUTPUT_MAX]`, or `None` when the demand level is in control. The setter raises `TypeError` naming `fixed_output` for a non-numeric or `bool` value, `ValueError` for a non-finite or out-of-range value, `OverflowError` for one too large to represent as a `float`; the previous setting is unchanged on any failure, and a valid value is stored as `float`. |
| `Modulator.history -> tuple[float, ...]` | The last `history_length` commands, oldest first. |
| `Modulator.duty_cycle -> float` | Mean of `history`; `0.0` while it is empty. |
| `Modulator.is_history_full -> bool` | Whether `history_length` commands have been issued. |
| `main() -> None` | Showcase: a radiator pass-through, a floor-heating run to a converged duty cycle, a `fixed_output` override, and a reset. |

The mode coercion shared by the setter and the restore path, and the four-key snapshot pair
that `PIController.to_dict`/`from_dict` fold into the controller's own snapshot, are private
helpers and are omitted per this file's convention — the modulator has no public wire format
of its own, since nothing in the feature asks for one.

Runnable standalone: `python -m heatingsystem.modulator.modulator`, once the package is
installed (`pip install -e ".[dev]"`).

### `src/heatingsystem/pi_controller/__init__.py`

Subpackage entry point. Re-exports `HeatingMode`, `PIController`, `OUTPUT_MIN` and
`OUTPUT_MAX` from `pi_controller.py` — `HeatingMode`, `OUTPUT_MIN` and `OUTPUT_MAX` are
themselves imported into `pi_controller.py` from `heatingsystem.modulator.modulator`, so
this re-export and the objects it names are unchanged for any caller.

### `src/heatingsystem/pi_controller/pi_controller.py`

A discrete-time proportional-integral controller that regulates room temperature by
modulating a heating actuator. It is driven externally (Home Assistant / AppDaemon on a
fixed 5-minute poll) and holds no timing of its own. Anti-windup is conditional
integration: the integral only advances while the raw output is inside `[0, 1]`, or when
integrating would pull a saturated output back toward it. The actuator mapping — the mode,
the rolling history window, the duty cycle and the fixed-output hold — is owned by a
composed `Modulator` (`heatingsystem.modulator.modulator`); the properties below delegate
to it, and the modulator instance itself is not exposed.

| Signature | Description |
|---|---|
| `PIController(kp: float = 0.3, ki: float = 0.015, mode: HeatingMode \| str = HeatingMode.RADIATOR, setpoint: float = 21.0, *, history_length: int = 24, fixed_output: float \| None = None)` | The controller. Builds its `Modulator` first (`mode`, `history_length`), then assigns `kp`, `ki`, `setpoint` through their validating properties, then `fixed_output` last through its delegating setter — the same validation order as before the split. Every setting — `kp`, `ki`, `mode`, `setpoint`, `fixed_output`, `integral` — raises the same error at construction as later assignment would. |
| `PIController.kp -> float` (settable) | Proportional gain. Setter: a finite real number; `bool` rejected. Raises `TypeError` naming `kp` for a non-numeric or `bool` value, `ValueError` for a non-finite one, `OverflowError` for one too large to represent as a `float`. Previous value unchanged on failure. |
| `PIController.ki -> float` (settable) | Integral gain. Same setter contract as `kp`, naming `ki`. |
| `PIController.setpoint -> float` (settable) | Target temperature in °C. Same setter contract as `kp`, naming `setpoint`; also the path a per-call `setpoint` of `update` assigns through. |
| `PIController.mode -> HeatingMode` (settable) | Actuator mode. Delegates to the composed `Modulator`; same setter contract and message as before the split. |
| `PIController.fixed_output -> float \| None` | Property, settable. Delegates to the composed `Modulator`; same setter contract and messages as before the split. |
| `PIController.integral -> float` (settable) | The integral accumulator. Same numeric-family setter contract as `kp`, naming `integral`; previous value unchanged on failure. Writable so a restore can put it back exactly; `update` and `reset` write the private field directly rather than through this setter, so the control law does not depend on it never tightening. |
| `PIController.history_length -> int` | Read-only. Delegates to the composed `Modulator`. |
| `PIController.pi_output -> float \| None` | Read-only. The clamped PI result of the last `update`, in `[OUTPUT_MIN, OUTPUT_MAX]`. Reports the PI demand even while `fixed_output` holds the actuator at a different level. `None` before the first `update` and again after `reset()` or `from_dict()`. |
| `PIController.error -> float \| None` | Read-only. `setpoint - measured` of the last `update`, the setpoint in force for that step; stored as computed. `None` before the first `update` and after `reset()` or `from_dict()`. |
| `PIController.p_term -> float \| None` | Read-only. `kp * error` of the last `update`, stored as computed (`-0.0` and non-finite values included); same `None` cases. |
| `PIController.i_term -> float \| None` | Read-only. `ki *` the tentative integral (previous integral plus this error) the last `update`'s raw output used, whether or not anti-windup committed it, so `p_term + i_term` is the raw sum before clamping; same `None` cases. None of the three is in `to_dict()`. |
| `PIController.update(measured: float, setpoint: float \| None = None) -> float` | One control step: returns the actuator command for `measured` (°C). `measured` is validated first; a passed `setpoint` is then assigned through its setter, so a call that fails validation stores nothing. The new integral and the PI demand `u` are computed into locals; the demand is then handed to the composed `Modulator`'s `command`, which raises before `integral` or `pi_output` is written (`ValueError`/`TypeError`/`OverflowError` propagate unchanged and leave both untouched; a `setpoint` passed to that call has already been stored, as after any raise past validation); only then are `integral`, `pi_output`, `error`, `p_term` and `i_term` written. If the raw PI sum or the tentative integral is not finite (reachable only with extreme finite inputs, e.g. `kp * -inf`), the PI demand is `OUTPUT_MIN`, `pi_output` reads `0.0`, and the integral is held rather than advanced; while `fixed_output` is set, the command is unaffected, since the fixed level replaces the demand regardless inside the modulator. Raises `TypeError` for a non-numeric or `bool` `measured`/`setpoint`, `ValueError` for a non-finite one, `OverflowError` for one too large to represent as a `float`. |
| `PIController.reset() -> None` | Zero the integral, reset the composed `Modulator` (clearing its history window) and set `pi_output`, `error`, `p_term` and `i_term` back to `None`. Leaves `fixed_output` and every setting unchanged. |
| `PIController.to_dict() -> dict[str, object]` | A snapshot of every setting and every piece of running state — `kp`, `ki`, `setpoint`, `mode` (as its string value), `history_length`, `fixed_output`, `integral`, `history` (a list, oldest first) — as built-in types `json.dumps` accepts, merging the controller's own four keys with the modulator's private four-key snapshot into this frozen eight-key order. A fresh dict and a fresh list each call; mutating either, or updating the controller afterwards, leaves the other unchanged. `pi_output` is derived, not included. |
| `PIController.from_dict(data: Mapping[str, object]) -> Self` (classmethod) | Rebuilds a controller from a `to_dict()`-shaped mapping, each value applied through the same validating setter or check a direct assignment would use — the four modulator-owned keys are checked by building a `Modulator` first, then the four controller-owned keys. A missing or unknown key raises `ValueError` naming the keys (missing checked first); a bad value raises what the matching setter or check raises, naming the key (`history[i]` for an entry, `OverflowError` for a huge `int`); a `history` longer than `history_length` raises `ValueError`; a non-mapping `data` (e.g. the JSON text instead of the loaded object) raises `TypeError`. On any failure no controller is produced. The result equals the source in every setting and in `integral`, `history`, `duty_cycle`, `is_history_full` and `fixed_output`; `pi_output`, `error`, `p_term` and `i_term` are `None`, as on any fresh controller. |
| `PIController.history -> tuple[float, ...]` | Read-only. Delegates to the composed `Modulator`; the last `history_length` commands, oldest first. |
| `PIController.duty_cycle -> float` | Read-only. Delegates to the composed `Modulator`; mean of `history`, `0.0` while it is empty. |
| `PIController.is_history_full -> bool` | Read-only. Delegates to the composed `Modulator`; whether `history_length` commands have been issued. |
| `main() -> None` | Showcase: the enum, a radiator warm-up, a setpoint override with that step's `error`/`p_term`/`i_term`, a `fixed_output` override (now also printing `pi_output`) and release, a reset, a floor-heating run to a converged duty cycle, a state snapshot round trip through `to_dict`/`from_dict` (including a JSON round trip) with the released hold surviving, a `mode` reassignment, and the `ValueError`/`TypeError` demonstrations including an invalid snapshot. |

Runnable standalone: `python -m heatingsystem.pi_controller.pi_controller`, once the
package is installed (`pip install -e ".[dev]"`). Under a `src/` layout the repo root is
not on `sys.path`, so without the install it fails with `No module named heatingsystem` —
an un-set-up environment, not a broken module. The `RuntimeWarning` about the module already
being in `sys.modules` is the expected consequence of the subpackage re-exporting it. `pi_controller.py` names the three pass-through imports in its own `__all__` so
`ruff --fix` does not strip them as unused.

## Script: `test.py`

Closed-loop simulation of `PIController` against a first-order thermal model of a room
(with a configurable delay, `HEAT_DELAY_STEPS`, before a command's heat reaches the room,
and a thermal mass, `ROOM_CAPACITY`, that slows its response),
once per `HeatingMode`, with a fixed-output hold once the room has settled (hatched on the
plot, so the sag during it and the burst at release are visible) and a setpoint step
halfway through, drawn as two stacked matplotlib subplots and saved as `simulation.png`
(git-ignored). Not a test, despite the
name: it lives outside `tests/` on purpose, `pytest` never collects it, and matplotlib is
only installed with the `sim` extra (`pip install -e ".[dev,sim]"`).

| Signature | Description |
|---|---|
| `step_temperature(temp: float, command: float) -> float` | Advance the room model one step. |
| `run_simulation(mode: hs.HeatingMode) -> tuple[list[float], list[float], list[float]]` | Run the loop; returns temperatures, commands and setpoints per step. |
| `plot_mode(ax: Axes, title: str, temps: list[float], commands: list[float], setpoints: list[float]) -> None` | Draw one mode's run onto an axis. |
| `main() -> None` | Entry point: simulate both modes, plot, save. |

## Tests: `tests/`

### `tests/test_pi_controller.py`

Covers `PIController` and `HeatingMode`: construction defaults and explicit gains, `mode`
as enum and as string and an invalid string, the `ValueError` for a non-positive
`history_length` and for non-finite gains, setpoints and measurements; radiator output for
positive, zero, large and negative error, the `[0, 1]` clamp under random inputs, two
hand-computed steps and the per-call setpoint override; anti-windup in both saturation
directions, that the built-up integral stays bounded and recovers when the error reverses;
floor heating's binary output, all-on under full demand, all-off when too hot, the
read-before-append ordering of the first command, and the duty cycle converging to the
fractional, half and full demand; `history` empty, growing, capped, ordered, a tuple and a
snapshot; `duty_cycle` and `is_history_full` either side of the window filling; `reset`
clearing every piece of state; and that the package imports without AppDaemon.

`fixed_output`: that an unfixed controller's multi-step command sequence, integral and
history match hand computation and `fixed_output` stays `None`; radiator `update` returning
exactly the level regardless of measurement, setpoint or prior state, at construction and
via the setter, including the `0.0`/`1.0` boundaries, and that setting it alone disturbs
neither `history` nor `integral`; floor heating modulating the *fixed* level rather than the
PI demand — an exact ON-slot pattern and duty cycle for a quarter level, the constant
boundary levels, read-before-append on the first fixed command, re-reading existing history
when set mid-run, a one-slot window alternating, and a level just inside either edge still
firing or resting exactly once per window; that a fixed and an unfixed twin's `integral`
match exactly across the linear region, both saturation directions held, and a reversing
sequence, in both modes; that `update` still stores a passed setpoint and advances the
integral while fixed, and still raises on a non-finite input without appending or mutating
state; `ValueError` for every non-finite or out-of-range level — `nan`, `inf`, boundary
neighbours, and values further out — at construction and via the setter, naming the value
and leaving the previous setting (a number or `None`) unchanged; `TypeError` naming
`fixed_output` for a non-numeric level such as a string, and for `bool` specifically (its
own test, since round 1 stored `True`/`False` as `1.0`/`0.0` and round 2 forbids it); `int`
levels stored and returned as `float`; clearing the override resuming an unfixed twin's
exact command in radiator mode with both commands preserved in `history`, and floor
heating's duty cycle recovering to the PI demand over one full window after release; and
`reset` clearing the integral and history while leaving `fixed_output` set, in both modes.

Round 2's attribute surface: for `kp`, `ki` and `setpoint`, `ValueError` for `nan`/`inf`/
`-inf` naming the attribute and the value, `TypeError` for a dozen non-`numbers.Real` types
(`str`, `None`, `list`, `tuple`, `dict`, `complex`, `Decimal`, `bytes`, `bool`) naming the
attribute and the offending type, `OverflowError` for a huge `int` and for one past
`float`'s representable range, and `int`/`Fraction` accepted and read back as `float`, all
checked identically at construction and via the setter, with the previous value left alone
on every failure; the same `OverflowError` and range-vs-overflow boundary for
`fixed_output`, and that its range error repeats the caller's original value (a `Fraction`)
rather than its `float` conversion; `mode` storing the coerced `HeatingMode` (checked with
`is`, not `==`, since a string compares equal to its member) in both directions of a
mid-run switch — including floor-to-radiator, the exact case a plain string used to route
to the wrong mapping — and rejecting everything else, near misses included, with
`ValueError` naming `mode` and listing the valid values; `update` validating `measured`
before touching any state, `None` accepted as "no override" for its `setpoint` parameter
(a different question from the setter's own contract), a per-call `Fraction` measurement
and `int` setpoint accepted, and a huge `int` raising `OverflowError` naming `measured` or
`setpoint`; that a raising `update` — non-finite, non-numeric, `bool`, or overflowing,
combined with every prior good-setpoint case — leaves `setpoint`, `integral`, `history`,
`pi_output` and `fixed_output` all exactly as they were and consumes no step, in both
fixed and unfixed controllers; `pi_output` reading `None` before the first `update` and
after `reset()`, tracking the clamped PI result exactly (including both clamp bounds hit
precisely) rather than the fixed level or the binary floor-heating command, unaffected by
assigning `fixed_output` alone, and read-only; `OUTPUT_MIN`/`OUTPUT_MAX` importable from
both `heatingsystem` and `heatingsystem.pi_controller` as the identical module objects and
listed in both `__all__`; that a bad value raises the identical exception type and message
at construction and via the setter; that a successful setter changes only its own
attribute, leaving every other setting and all running state untouched; and that every
constructor case the pre-round-1 suite already covered still raises the same class, now
naming the attribute.

Round 3 decides the two arithmetic quirks round 2 left alone: a `raw` PI sum, or the
tentative integral, that is not finite (reachable only with extreme finite inputs such as
`kp * -inf`) now yields the closed command — `pi_output` `0.0` and the integral held —
rather than depending on `min`/`max`'s argument order; and every numeric setter, and
`update`'s `measured`/`setpoint`, now normalise `-0.0` to `+0.0`, so it no longer survives
sign-preserved through `fixed_output` or anywhere else.

Round 3's state-snapshot suite: `to_dict` returning exactly the eight documented keys as
`json.dumps`-friendly built-in types, `mode` as its string value, `fixed_output` `None` when
unset, and the returned dict and its `history` list holding no reference back into the
controller or vice versa; `from_dict(to_dict())` reproducing a controller's settings,
`integral`, `history`, `duty_cycle`, `is_history_full` and `fixed_output` exactly — mid-hold
and released, a full floor-heating window (proving the restored deque's `maxlen`, not just
its length), and a restore followed by `reset()` — with `pi_output` `None` until the next
`update`, and the same holding through a `json.dumps`/`json.loads` round trip; every
`from_dict` failure naming the offending key — a missing key reported before an unknown one
(missing checked first, and a wrong key case or trailing whitespace counting as missing, not
a near miss), every value error identical in type and message to what the matching
constructor keyword or setter raises (including `OverflowError` for a huge `int`), a
history entry or the history container's shape naming `history[i]`, a history longer than
`history_length` naming both counts, an `int` `fixed_output` of `0` read as a hold and not
`None`, and any non-`Mapping` `data` — including the JSON text itself — raising `TypeError`
naming it, while `MappingProxyType`, `OrderedDict` and `ChainMap` are all accepted;
`history_length` read-only after construction, `sys.maxsize` accepted and round-tripping
through JSON, one more raising `OverflowError`, and `bool` or any other non-`int` raising
`TypeError` naming it, at construction and via `from_dict` alike; the `integral` property
on the shared numeric-family contract, including `-0.0` read back positive and the previous
value kept on every rejected assignment; the `update` finite guard flipping exactly at
finiteness rather than at large-but-finite values (a huge but finite integral still takes
the anti-windup hold, not the guard), in both an overflowing raw sum and an overflowing
tentative integral, with the fixed-output command unaffected either way; and `-0.0` read
back `+0.0` from every numeric setter, from a per-call `setpoint`, from a fixed-output
command, and through a full `from_dict`/JSON round trip. Three production defects this
suite found and the fixes it drove: `from_dict` sorting an unknown-key set of mixed,
unorderable types (`sorted()` alone raised a bare `TypeError` instead of the documented
`ValueError`) now sorts by `repr`; `from_dict`'s `fixed_output` range error used to report
the value after conversion to `float` rather than the caller's own value (e.g. a `Fraction`)
as the setter itself would — `from_dict` now runs the range check on the original value; and
`update`'s output clamp now normalises `-0.0` explicitly with `+ 0.0`, rather than relying
on `max(OUTPUT_MIN, ...)`'s argument order to return a positive zero by accident.

Round 4 moves the actuator mapping into `Modulator` (see `tests/test_modulator.py` below)
and delegates to it, so the four tests that used to pin floor heating's exact firing
schedule here now assert only what `PIController` itself still promises: which slots fire
is the modulator's concern, not the controller's. `test_fixed_output_floor_quarter_level_converges_to_exact_fraction`
keeps its `set(outs) <= {0.0, 1.0}`, `duty_cycle` and `is_history_full` assertions and drops
the exact on-slot list; `test_fixed_output_floor_history_length_one_alternates` replaces its
exact output list with `outs.count(1.0) == 3` on the same `set(outs) <= {0.0, 1.0}` base;
`test_fixed_output_floor_level_just_above_min_fires_once_per_window` and
`test_fixed_output_floor_level_just_below_max_rests_once_per_window` each keep a slot-count
assertion (`outs.count(1.0) == 1` / `outs.count(0.0) == 1`) and their `duty_cycle` assertion,
dropping the assertion that pinned *which* slot. Round 3's snapshot-twin tests that happen to
pin an exact floor history are untouched, since they prove restore equality, not the
schedule.

Round 4's own additions: `to_dict` reproduces the exact round-3 literal for a held radiator
controller, key order included, both as a `dict` and through `json.dumps`
(`test_to_dict_matches_the_round_3_literal_byte_for_byte`); a round-3-shaped floor-heating
JSON literal restores through `from_dict` and re-serialises to the identical text
(`test_from_dict_round_3_floor_literal_restores_and_reserialises_to_itself`); after `reset()`,
`to_dict()` equals a freshly constructed controller with the same settings — for a controller
that ran, held and had its setpoint changed mid-run, and for one restored from a snapshot —
and differs from a fresh controller built with the wrong setpoint
(`test_reset_then_to_dict_equals_fresh_controller_with_current_settings`); a subclass whose
`integral` setter always raises still constructs, `update()`s and `reset()`s without raising,
proving `update`/`reset` write `_integral` directly rather than through the public setter —
while `from_dict` (external state) still goes through it and does raise
(`test_update_reset_and_construction_bypass_a_raising_integral_setter`); `Modulator`,
`HeatingMode`, `OUTPUT_MIN` and `OUTPUT_MAX` are the identical objects across every import
path (`heatingsystem`, `heatingsystem.modulator`, `heatingsystem.modulator.modulator`,
`heatingsystem.pi_controller`, `heatingsystem.pi_controller.pi_controller`), `Modulator` is
in `heatingsystem.__all__` and `heatingsystem.modulator.__all__` but not
`heatingsystem.pi_controller.__all__`, and `HeatingMode` coerces identically from any of them
(`test_public_names_are_identical_across_every_import_path`); and a monkeypatched
`Modulator.command` that raises leaves `integral`, `pi_output` and `history` — the whole
`to_dict()` snapshot — exactly as they were before the call
(`test_update_leaves_integral_pi_output_and_history_untouched_when_modulator_raises`; no
`setpoint` is passed in that call, since the setpoint setter runs before the modulator is
reached and would otherwise store even on a raise — see plan round 4 section 5).

Round 4 of `feat/allocator-home-assistant` (D1-D3, 35 cases): `error`, `p_term` and `i_term` of the last
`update` against hand computation: the linear region over several steps, both saturation directions with `i_term`
built from the tentative integral while the integral is held (including raw exactly on either clamp), a per-call
setpoint, a gain change between steps, terms stored not recomputed after a setting change, floor-heating mode and a
`fixed_output` hold matching an unfixed twin, `int`/`Fraction` inputs giving `float` terms, the non-finite cases
reported as computed (finite terms with an overflowing sum, `nan`/`inf` from an overflowing error) with `pi_output`
0.0, signed zero kept on `p_term`/`i_term` and never on `error`; `None` before the first `update`, after `reset()`
and after `from_dict` (direct and JSON), read-only, `to_dict()` unchanged; a raising `update` (bad `measured`, bad
per-call `setpoint`, a raising modulator, with and without a per-call setpoint) leaving the three untouched.

All tests live here and nowhere else — `testpaths = ["tests"]` in `pyproject.toml` means
`pytest` collects nothing outside this directory; a test file anywhere else never runs.

### `tests/test_allocator.py`

Covers `SectionAllocator` and `Room`, migrated in round 1 of `feat/allocator-home-assistant` to the
coverage meaning (reference layout HS1 {R1 0.7}, HS2 {R1 0.3, R2 0.3}, HS3 {R2 0.7}, HS4 {R3 1.0}; worked
examples B-G recomputed by an independent active-set solve, B and C also by hand; the layouts that need a
coefficient below 1 — the priority counterexample, the unequal-coverage and saturation exceptions — hold
the other half of the room shut; the share-sum tests became per-room sums and the dedicated-share test
became "any dedicated coverage gives the standalone command sequence"). Construction: every A1 refusal with its class and path (coverage
at, either side of and far from `(0, 1]`, per-room coverage sums either side of the 1e-9 tolerance, unknown and
uncovered rooms, priority and evenness limits, bad types, empty and malformed mappings, non-string
names), the error order, shared-gain errors identical to `PIController`'s and naming the argument,
`history_length` parity, no aliasing of the caller's mappings, and a read-only layout. `Room`
setters identical to `PIController`'s and effective on the next `update`. `update`: output shape and
binary values, every bad-measurement branch leaving the whole `to_dict()` unchanged, a `None`
temperature accepted as no reading (round 2, B1-B5: that room's `integral` and `demand` untouched while other rooms step like a twin; the allocation reuses its last demand, 0.0 and `demand` `None` before a first reading and after `from_dict`, also on the closed-form path and at the clamp; equals a standalone `PIController` fed only the real readings; every room `None`, with holds; a missing key, an unknown room, `"None"`/`"unavailable"` strings and every other refusal unchanged; a solver failure restores all rooms; each temperature read once from the mapping; a setting change deferred to the next real reading),
numeric variants, and a failing or non-converged solver restoring every room. Allocation: exact fits with
evenness 0 (reference layout, a three-room chain, a 60-room chain, the closed form, rank-deficient
and tiny-coverage matrices), the weight floor against extreme priority ratios and subnormal
priorities, the range refusals for priority in `(0, 1]` and evenness in `[0, 1]` (and in `from_dict`),
A5's absolute-mismatch monotonicity at evenness 0 with its documented exceptions, and its general
clause (the combined cost `|d - h|^2 + e * spread` never rises with priority) on the halt's
counterexample layout, a priority sweep and seeded random layouts, with the counterexample also
pinned as the reason the absolute-mismatch clause needs evenness 0, A6's spread monotonicity (including a three-section room) and the
hungry-R2 case, 1.0.0's A7 (now A3) bit-exact equivalence to a standalone floor-heating `PIController` for a dedicated coverage of any size, and 1.0.0's A10-A12 (now A4):
the cost's KKT conditions and an independent solve over random layouts, priority-scaling invariance, and
the worked examples A-G. Package exports, the declared scipy/numpy dependencies, and
`to_dict`/`from_dict`: shape and order (per section `coverage`, `history`, `hold`), identical next 50
commands (direct and through JSON, empty, partial and full windows), every malformed snapshot naming
its path (a bad `hold` naming `sections['HS1'].hold`, a missing one reported as `sections['HS1']: ` with the missing key), a 1.0.0 snapshot refused naming
its missing keys, subclass round trip; and the module showcase runs. Normalisation and holds (the E layout, hand-derived numbers): E1-E5, a room summing below 1, coverages
reported as given, `hold`/`holds` validation (section checked before level, previous hold kept on a raising call), a held
section's duty and its modulator-twin commands, the held contribution subtracted from demand and evenness rows, no solver
for an all-held component, the room PI equal to an un-held twin's, a raising `update` leaving holds, windows, rooms and
`duty` untouched, the restored allocator with holds (identical next 50 commands, int and `-0.0` holds read as holds), a
restored partial-coverage layout not renormalised, `from_dict` reporting a layout error, then a room setting, then a hold,
then a history error.

Round 3's `with_layout` suite (55 tests, C1-C6): a no-argument call giving an equal but distinct allocator
(`to_dict`, `duty`, `holds`, `history`, demands) with identical next 50 commands including `None` readings,
chained calls equal to one, a never-updated allocator, a zero demand carried as `0.0` not `None`; isolation
both ways and no aliasing of the caller's mappings; a priority/evenness/coverage change carrying every setting,
integral, demand, window, hold and `duty` and matching the edited-snapshot `from_dict` rebuild for real readings,
with the `None`-reading contrast (carried demand against the rebuild's 0.0); `duty` carried for reordered rooms,
a mapping proxy, a coverage-only change and a window-only change, and `None` for a reordered section list;
`history_length` trimming to the newest slots at every boundary, growth not full, a shrink irreversible, the
trimmed `from_dict` twin, a fractional hold continuing over a grown window and the `sys.maxsize` limits; adding
and removing rooms and sections (defaults not the original's gains, no ghost state on re-adding, a section kept
after its room is removed, exact-name matching); thirteen constructor refusals identical in class and message
with the original untouched (including `duty`, demands and next commands), empty mappings not read as "keep",
keyword-only `history_length`, a failed first `update` after a carry, a subclass keeping its type and the README
and docstrings no longer claiming a fixed layout.

Round 4's `Room` terms suite (D4, 26 cases): `error`/`p_term`/`i_term` `None` before the first update, equal to a
standalone `PIController` twin (NaN-aware, sign-of-zero-aware comparison) over 40 steps and under a hold and
saturation, following the setpoint and gains in force per step and stored not recomputed, read-only, `float`; a
`None` reading leaving them (and keeping them `None` before a first real reading); a failing solver (first update,
later update, `KeyboardInterrupt`), a later room's step raising mid-loop and a validation failure restoring them;
`with_layout` carrying them for matched rooms and `None` for new, renamed or re-added rooms, bit-for-bit for
non-finite terms, as a copy; `from_dict` (direct and JSON) leaving them `None`; the snapshot not containing them.

### `tests/test_modulator.py`

Covers `Modulator` and, jointly with it, the shared numeric-contract plumbing it and
`PIController` now both call through (D1, D5, D7). Construction defaults and validation for
`mode`, `history_length` and `fixed_output` identical in class and message to the
controller's own (checked directly against `PIController(**kwargs)` for the same keyword
arguments), including the previous-value-kept rule, `history_length`'s read-only property,
the keyword-only constructor after `mode`, and which attribute a three- or two-fault
construction names; `reset` clearing the window while keeping the mode, length and hold, and
being a no-op called twice.

`command`: radiator pass-through, floor-heating's binary output, reading the window before
appending (including an empty window and a hold read mid-run), the exact tie-at-duty case
that rests the slot; every range edge accepted exactly and one ULP outside rejected; every
non-real type, `bool`, non-finite value and huge `int` raising the right exception naming
`level`, including while a hold is set (the level is always validated first, even though the
hold then replaces it); `-0.0` returned as `+0.0` in both modes; `int` and `Fraction` demands
accepted and returned as `float`, with a rejected `Fraction`'s range error repeating the
original value; and a mid-run `mode` switch reading a fractional radiator-mode history
correctly once floor heating takes over.

The four exact firing-schedule tests moved from `tests/test_pi_controller.py` (R8, D7) live
here, driven directly through `command`: the quarter-level pattern (`[1, 6, 10, 14, 18, 22]`
of 24), proven identical whether driven as a demand or as a `fixed_output` hold; a
one-slot window alternating on every call; a level one ULP above `OUTPUT_MIN` firing exactly
once per 24-slot window; and a level one ULP below `OUTPUT_MAX` resting exactly once.

The private four-key snapshot pair (`_to_dict`/`_from_dict`, not the public wire format):
exactly the keys `mode`, `history_length`, `fixed_output`, `history` in that order, a fresh
copy that a caller mutating the returned dict cannot use to reach back into the modulator's
own history, round-tripping through `_from_dict`, refusing the controller's own eight-key
snapshot (unknown keys named, sorted), reporting missing keys before unknown ones, refusing a
non-mapping, and a subclass's `_from_dict` returning an instance of that subclass rather than
`Modulator` itself. `_load_history`: atomic on a bad entry (the window is left as it was),
the length check running before the per-entry checks, and the empty and full cases.

D5's helper consolidation: `_finite`, `_level`, `_window_length`, `_to_command` and
`_heating_mode` no longer exist on either the controller or the modulator module (`hasattr`
false throughout, including `PIController._to_command`), the four `heatingsystem._validation`
helpers are callable, and monkeypatching the modulator module's single `_heating_mode` breaks
mode coercion identically for `PIController.mode`, `Modulator.mode` and
`PIController.from_dict` — proof there is exactly one coercion function, not one per class.

### `tests/test_validation.py`

Covers `heatingsystem._validation`, the private module the numeric contract is stated in
once and that `PIController`, `Modulator` and the allocator (`Room`, `SectionAllocator`) call
through by name (D5). `finite`:
accepts a plain number and returns `float`, normalises `-0.0` to `+0.0`, accepts `int` and
`Fraction`, rejects every non-finite value and every non-`numbers.Real` type (`bool`
included) naming the attribute and the offending type, and raises `OverflowError` naming the
attribute for a huge `int`. `window_length`: accepts a positive `int` up to `sys.maxsize`
unchanged, rejects every non-`int` type (`bool` included) naming it, rejects less than 1, and
raises `OverflowError` one past `sys.maxsize`. `level`: accepts values inside the range,
rejects values outside it while repeating the caller's own value, accepts equal bounds as
exactly that one value and rejects one ULP outside them, treats inverted bounds as rejecting
every value, checks finiteness before the range, normalises `-0.0`, and repeats a rejected
`Fraction`'s own value rather than its `float` conversion. `snapshot_mapping`: returns the
mapping unchanged on success (identity, not a copy) for a plain `dict` and a
`MappingProxyType`, reports an unknown key, reports missing keys sorted, reports missing
before unknown, sorts a mixed-type unknown-key set by `repr` rather than raising a bare
`TypeError` from `sorted()`, and rejects a `set` and a list of pairs (anything that is not a
`Mapping`) naming the type.

### Pipeline tests

| File | Covers |
|---|---|
| `tests/test_dev_state.py` | parsing, malformed files, active-brief choice, git helpers; its `write_brief`/`write_round` helpers are reused by other tests |
| `tests/test_session_brief.py` | the brief text and `main`'s output |
| `tests/test_check_sync.py` | every check, including the version raise against a throwaway repo |
| `tests/test_guard_git.py` | the guard, vendored with it |
| `tests/test_select_tests.py` | every selection rule on a throwaway repo (transitive and relative imports, re-exporting `__init__`, helpers, deletions, renames, cycles, unparseable files), `changed_files` and the CLI |

## Plans: `development/`

The pipeline's state, laid out in `development/README.md`. The numbered `NN-<slug>.md` files
under `development/feat/fixed-output/`, `feat/section-allocator/` and
`feat/allocator-home-assistant/` are the record of the earlier ten-step pipeline: read-only
history, ignored by the hooks.

## Hooks: `.claude/hooks/`

Stdlib only; importable by each other and by the tests (`pythonpath` in `pyproject.toml`).

### `.claude/hooks/dev_state.py`

Reads `brief.json` and `round.json` leniently: a malformed file reads as None.

| Signature | Returns |
|---|---|
| `RoundEntry` | Frozen dataclass: `number`, `name`, `slug`, `branch`, `status`, `summary`; property `folder -> str` (`r<n>-<slug>`). |
| `Brief` | Frozen dataclass: `path`, `feature`, `title`, `branch`, `bump`, `status`, `summary`, `current_round`, `rounds: tuple[RoundEntry, ...]`, `pr`, `kind` (`feature` or `fix`); properties `folder -> Path`, `live -> bool`; `round_entry(number: int) -> RoundEntry \| None`. |
| `RoundState` | Frozen dataclass: `path`, `round`, `name`, `branch`, `status`, `step`, `steps: tuple[str, ...]`, `attempt`, `question`, `note`; property `step_name -> str`. |
| `read_brief(path: Path) -> Brief \| None` | One `brief.json`, or None if unusable. |
| `read_round(path: Path) -> RoundState \| None` | One `round.json`, or None if unusable. |
| `all_briefs(project_dir: Path) -> list[Brief]` | Every brief under `development/`, newest first. |
| `active_brief(project_dir: Path, branch: str = "") -> Brief \| None` | The live brief, preferring the checked-out feature or round branch. |
| `round_state(brief: Brief, number: int) -> RoundState \| None` | That round's `round.json`. |
| `read_diagnosis(path: Path) -> RoundState \| None` | A fix's diagnosis `state.json`, read as a `RoundState` with `round` 0 and the diagnosis steps. |
| `diagnosis_state(brief: Brief) -> RoundState \| None` | The feature's `diagnosis/state.json`, or None. |
| `git_lines(project_dir: Path, args: list[str]) -> list[str]` | Non-empty output lines of a git command, `[]` on failure. |
| `current_branch(project_dir: Path) -> str` | The checked-out branch, or `""`. |
| `main() -> None` | Prints the live feature and its current round. |

Constants: `DEV_DIR`, `DIAGNOSIS_DIR`, `KINDS`, `LIVE_STATUSES`, `FEATURE_STATUSES`,
`ROUND_STATUSES`, `STEP_STATUSES`, `ROUND_STEPS` (the nine step names), `DIAGNOSIS_STEPS` (the
four diagnosis steps).

### `.claude/hooks/session_brief.py`

`SessionStart` hook: injects the live feature's state and the skill that resumes it.

| Signature | Returns |
|---|---|
| `brief(project_dir: Path, feature: Brief) -> str` | The paragraph for the session's context. |
| `main() -> None` | Emits it as `additionalContext`, or nothing when no feature is live. |

### `.claude/hooks/check_sync.py`

Checks the documents agree with each other and with the code. Run by `/finish` and CI.

| Signature | Returns |
|---|---|
| `parse_version(text: str) -> tuple[int, int, int] \| None` | The version, or None if not exactly `X.Y.Z`. |
| `bumped(version: tuple[int, int, int], bump: str) -> tuple[int, int, int]` | The version raised by `major`/`minor`/`patch`; raises `ValueError` otherwise. |
| `project_version(pyproject_text: str) -> str` | `[project] version`, or `""`. |
| `version_problems(project_dir: Path) -> list[str]` | pyproject, README `**Version:**` and newest DEVLOG entry disagree; DEVLOG order. |
| `structure_problems(project_dir: Path) -> list[str]` | Python files missing from STRUCTURE.md, or named but absent. |
| `state_problems(project_dir: Path) -> list[str]` | Unparseable state files, unknown statuses. |
| `branch_brief(project_dir: Path, branch: str) -> Brief \| None` | The brief whose feature or round branch is `branch`. |
| `bump_problems(project_dir: Path, base: str) -> list[str]` | Version not raised above `base`'s by this branch's brief's bump (any raise without one). |
| `problems(project_dir: Path, base: str = "") -> list[str]` | All of the above. |
| `main() -> None` | CLI: `[--base REF] [--root DIR]`; prints problems, exits 1 if any. |

### `.claude/hooks/select_tests.py`

Picks the test files a change can affect, so a round runs those and CI runs the whole suite.
The changes are `git diff <base>...HEAD` plus uncommitted and untracked files. The full suite
(None) for `pyproject.toml`, a `conftest.py`, a non-test file under the test directory, a
re-exporting `__init__.py`, an unparseable `.py` file or a file it cannot place; otherwise
the changed test files, `test_<module>.py` (and `test_<pkg>_<module>.py`) of each changed
module, and every test file that depends on a changed module transitively, read with `ast`
(imports, `import_module("...")`, and strings naming a module or its path). Prose,
`development/` and `.claude/` outside `hooks/` select nothing; other configuration selects
the tests that name it. Module roots come from `pythonpath` and `testpaths`;
`[tool.select_tests] ignore` adds path prefixes that select nothing.

| Signature | Returns |
|---|---|
| `Selection` | Frozen dataclass: `tests: tuple[str, ...] \| None` (None = full suite), `reason`, `total`; `summary() -> str` (`selected 4 of 61: ...`, `full suite: ...`, `no tests apply: ...`). |
| `changed_files(project_dir: Path, base: str) -> list[str]` | Changed paths against `base...HEAD`, committed or not, sorted; renames as both paths. Raises `ValueError` when git fails. |
| `selection(project_dir: Path, changed: Iterable[str]) -> Selection` | The selection with its reason. |
| `select_tests(project_dir: Path, changed: Iterable[str]) -> list[str] \| None` | The test paths, `[]` when none applies, None for the full suite. |
| `main() -> None` | CLI: `[--base REF] [--root DIR]`; one test per line, `ALL`, or nothing on stdout, the reason on stderr; `ALL` when the changes cannot be listed. |

### `.claude/hooks/guard_git.py`

`PreToolUse` hook on `Bash` and `PowerShell`: refuses a `git commit` or `git push` that would
land on `main`, reading the command as a shell would (quoting, separators, subshells, branch
switches chained with `&&`). Vendored from `template_repo`.

| Signature | Returns |
|---|---|
| `Segment` | Frozen dataclass: `tokens`, `separator`. |
| `segments(command: str) -> list[Segment] \| None` | The command split into invocations, or None if unreadable. |
| `git_subcommand(tokens: tuple[str, ...]) -> tuple[str, tuple[str, ...]]` | The git subcommand and its arguments. |
| `push_targets_main(args: tuple[str, ...], branch: str) -> bool` | Whether a push would update `main`. |
| `switch_target(subcommand: str, args: tuple[str, ...]) -> str` | The branch a checkout/switch moves to, `""`, or `UNRESOLVED`. |
| `violation(command: str, branch: str) -> str` | The reason to refuse, or `""`. |
| `main() -> None` | Entry point: allow or refuse. |

### `.claude/hooks/lint_py.py`

`PostToolUse` hook: `ruff format` and `ruff check --fix` on an edited `.py` file.

| Signature | Returns |
|---|---|
| `find_ruff(project_dir: Path) -> Path \| None` | Ruff in the project venv. |
| `run(ruff: Path, args: list[str]) -> CompletedProcess[str]` | Ruff's result. |
| `target_file(payload: dict[str, object], project_dir: Path) -> Path \| None` | The edited `.py` file. |
| `main() -> None` | Format, fix, report. |

## Pipeline configuration: `.claude/`

| Path | Role |
|---|---|
| `skills/feature/` | `/feature`: Main Conceptualize (Opus medium), background `repo-scout`s, BRIEF, starts round 1 |
| `skills/fix/` | `/fix`: intake, runs `workflows/diagnose.js`, agrees cause, scope and bump with the user, then the rounds with `kind: "fix"` |
| `skills/round/` | `/round`: coordinates one round (Sonnet high), runs `workflows/round.js`, answers or escalates questions |
| `skills/setup/` | `/setup`: one-time setup of a repo made from the template (purpose, package rename, approver, version 0.1.0, ruleset from its `main_protect.*.json`); deletes itself when done |
| `skills/finish/` | `/finish`: runs `workflows/final.js`, extras, `docs-writer`, version, pull request, then CI through to green |
| `workflows/diagnose.js` | A fix's four diagnosis steps; args `feature_dir`, `branch`, `symptom`, `start_step`, `answers`; returns `diagnosed` (with `is_bug` and the `defect`), `needs_input` or `failed` |
| `workflows/round.js` | The nine round steps (step 7 runs the tests `select_tests.py` picks against the feature branch, `--ff` plus the earlier failures in the fix loop); args `feature_dir`, `round_dir`, `round`, `feature_branch`, `round_branch`, `start_step`, `attempt`, `kind`, `answers` (`kind` `fix` adds the fix-round rules); returns `merged`, `needs_input` or `failed` |
| `workflows/final.js` | Read-only whole-feature check: test gate first (the selection against `origin/main`, not the full suite), then three independent concept checks and two extras scouts; returns `tests`, `checks`, `extras`, `missing` |
| `agents/reproducer.md`, `cause-hunter.md`, `root-cause-analyst.md`, `diagnosis-critic.md` | The diagnosis's four steps (`/fix`) |
| `agents/repo-scout.md` | Read-only investigation for `/feature`, one angle each |
| `agents/round-conceptualizer.md`, `round-planner.md` | Round steps 1 and 2 (Opus) |
| `agents/implementer.md` | Step 3, and fix mode for failing gates |
| `agents/verifier.md`, `code-reviewer.md` | Step 4 |
| `agents/test-designer.md`, `test-writer.md`, `test-runner.md` | Steps 5 to 7 |
| `agents/concept-checker.md` | Step 8 and the final check |
| `agents/round-closer.md` | Step 9: merge into the feature branch |
| `agents/extras-scout.md`, `docs-writer.md` | `/finish` |
| `rules/python.md` | Conventions for `**/*.py` |
| `settings.json` | Hooks and pre-approved commands |

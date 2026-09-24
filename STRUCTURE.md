# Structure

Map of everything in this repo. Loaded into context at the start of every session via the
`@STRUCTURE.md` import in `CLAUDE.md`, so it is what Claude uses to find things without
searching.

## Keeping this file current

Update it **in the same change** that causes any of the following:

- a module is added, deleted, renamed or moved
- a public function or class is added or removed
- a public signature changes (parameters, defaults, return type)
- a module's purpose changes

Private helpers (names starting with `_`) are intentionally left out. They are
implementation detail, and listing them is what makes a file like this rot.

The stop gate (`.claude/hooks/stop_gate.py`) cross-checks the module paths named here
against the `.py` files on disk and blocks on a mismatch. It only sees file-level drift —
signature drift is on you, or run the `structure-auditor` subagent.

That covers `src/`, `tests/` and `.claude/hooks/`. The hooks are documented here with full
signature tables, type-checked like the package (`[tool.mypy] files` names all three) and
importable from the suite (`[tool.pytest.ini_options] pythonpath` names `src` and
`.claude/hooks`, so a test imports a hook by module name the same way a sibling hook does
at runtime), so they are held to the same standard despite not being installable.

## Growth

While the package is flat, keep everything here. Once it has subpackages, keep the tree and
one line per subpackage in this file, and move per-subpackage detail into
`.claude/rules/structure-<subpackage>.md` with `paths: ["<subpackage>/**"]` so it loads only
when Claude works in that subpackage. Split rather than delete — there is no length limit
here, but everything in this file is in context every session.

The package now has two subpackages (`modulator/`, `pi_controller/`) plus the private
`_validation.py` module at its root, but the per-subpackage split described above has
deliberately not been applied yet — doing it inside an unattended build changes what the
stop gate and the session brief see, which is not a change to make mid-round. It is
recorded as a step 8 recommendation for this feature.

## Tree

```
src/                    everything installable; nothing outside it is packaged
  heatingsystem/        the package: heating-system models
    modulator/         subpackage: the actuator mapping (mode, window, duty cycle, hold)
    pi_controller/     subpackage: the discrete-time PI controller
tests/                  pytest suite, one test_<module>.py per module
test.py                 closed-loop simulation and plot of the controller (matplotlib)
development/            one folder per branch, one file per round: the pipeline's state
docs/BACKLOG.md         known defects and proposed setup work, not yet in the pipeline
.claude/                Claude Code configuration: rules, skills, agents, hooks
.vscode/                editor config (Ruff as formatter, format on save)
pyproject.toml          packaging, Ruff, mypy and pytest configuration
README.md               install, usage and human setup guide
CLAUDE.md               routing map for Claude
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
| `Modulator` | `heatingsystem.modulator` |
| `PIController` | `heatingsystem.pi_controller` |
| `OUTPUT_MIN` | `heatingsystem.modulator` |
| `OUTPUT_MAX` | `heatingsystem.modulator` |

Every package directory under `src/`, including every subpackage added later, needs one of
these. The stop gate blocks on a directory of modules without it: it is not a package, so
it will not install.

### `src/heatingsystem/_validation.py`

Private module: the numeric contract behind every validating setting on `Modulator` and
`PIController`, stated once so it does not drift between the two classes' docstrings. Not a
subpackage — a single module at the package root, imported by both `modulator.py` and
`pi_controller.py` as `from heatingsystem import _validation`, so a helper name can never be
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
| `PIController.update(measured: float, setpoint: float \| None = None) -> float` | One control step: returns the actuator command for `measured` (°C). `measured` is validated first; a passed `setpoint` is then assigned through its setter, so a call that fails validation stores nothing. The new integral and the PI demand `u` are computed into locals; the demand is then handed to the composed `Modulator`'s `command`, which raises before `integral` or `pi_output` is written (`ValueError`/`TypeError`/`OverflowError` propagate unchanged and leave both untouched; a `setpoint` passed to that call has already been stored, as after any raise past validation); only then are `integral` and `pi_output` written. If the raw PI sum or the tentative integral is not finite (reachable only with extreme finite inputs, e.g. `kp * -inf`), the PI demand is `OUTPUT_MIN`, `pi_output` reads `0.0`, and the integral is held rather than advanced; while `fixed_output` is set, the command is unaffected, since the fixed level replaces the demand regardless inside the modulator. Raises `TypeError` for a non-numeric or `bool` `measured`/`setpoint`, `ValueError` for a non-finite one, `OverflowError` for one too large to represent as a `float`. |
| `PIController.reset() -> None` | Zero the integral, reset the composed `Modulator` (clearing its history window) and set `pi_output` back to `None`. Leaves `fixed_output` and every setting unchanged. |
| `PIController.to_dict() -> dict[str, object]` | A snapshot of every setting and every piece of running state — `kp`, `ki`, `setpoint`, `mode` (as its string value), `history_length`, `fixed_output`, `integral`, `history` (a list, oldest first) — as built-in types `json.dumps` accepts, merging the controller's own four keys with the modulator's private four-key snapshot into this frozen eight-key order. A fresh dict and a fresh list each call; mutating either, or updating the controller afterwards, leaves the other unchanged. `pi_output` is derived, not included. |
| `PIController.from_dict(data: Mapping[str, object]) -> Self` (classmethod) | Rebuilds a controller from a `to_dict()`-shaped mapping, each value applied through the same validating setter or check a direct assignment would use — the four modulator-owned keys are checked by building a `Modulator` first, then the four controller-owned keys. A missing or unknown key raises `ValueError` naming the keys (missing checked first); a bad value raises what the matching setter or check raises, naming the key (`history[i]` for an entry, `OverflowError` for a huge `int`); a `history` longer than `history_length` raises `ValueError`; a non-mapping `data` (e.g. the JSON text instead of the loaded object) raises `TypeError`. On any failure no controller is produced. The result equals the source in every setting and in `integral`, `history`, `duty_cycle`, `is_history_full` and `fixed_output`; `pi_output` is `None`, as on any fresh controller. |
| `PIController.history -> tuple[float, ...]` | Read-only. Delegates to the composed `Modulator`; the last `history_length` commands, oldest first. |
| `PIController.duty_cycle -> float` | Read-only. Delegates to the composed `Modulator`; mean of `history`, `0.0` while it is empty. |
| `PIController.is_history_full -> bool` | Read-only. Delegates to the composed `Modulator`; whether `history_length` commands have been issued. |
| `main() -> None` | Showcase: the enum, a radiator warm-up, a setpoint override, a `fixed_output` override (now also printing `pi_output`) and release, a reset, a floor-heating run to a converged duty cycle, a state snapshot round trip through `to_dict`/`from_dict` (including a JSON round trip) with the released hold surviving, a `mode` reassignment, and the `ValueError`/`TypeError` demonstrations including an invalid snapshot. |

Runnable standalone: `python -m heatingsystem.pi_controller.pi_controller`, once the
package is installed (`pip install -e ".[dev]"`). Under a `src/` layout the repo root is
not on `sys.path`, so without the install it fails with `No module named heatingsystem` —
an un-set-up environment, not a broken module. The `RuntimeWarning` about the module already
being in `sys.modules` is the expected consequence of the subpackage re-exporting it. `pi_controller.py` names the three pass-through imports in its own `__all__` so
`ruff --fix` does not strip them as unused.

## Script: `test.py`

Closed-loop simulation of `PIController` against a first-order thermal model of a room,
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

All tests live here and nowhere else — `testpaths = ["tests"]` in `pyproject.toml` means
`pytest` collects nothing outside this directory, and the stop gate blocks on a test file
found anywhere else.

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
once and that both `PIController` and `Modulator` call through by name (D5). `finite`:
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

### `tests/test_guard_git.py`

Covers `.claude/hooks/guard_git.py`, and is the regression suite for the quoting defect
its parser was rebuilt to fix. `segments` for quoting, every separator, a separator glued
to a word or to a newline, a run of newlines, a newline inside a quoted message, a `#`
inside a word, and input that cannot be lexed at all; `git_subcommand` for each spelling
of the executable and the options that hide the subcommand; `push_targets_main` for every
refspec shape that reaches `main`; `switch_target` for both subcommands, their new-branch
options, an option left without a value and a file restore; `violation` for the whole
behavioural matrix — punctuation in a commit message, a branch switch trusted across `&&`
and across an `&&` ending a line but distrusted across everything else, including a mixed
run such as `; &&`, a subshell that has closed and another repository reached with `-C`;
commands hidden behind grouping delimiters; unreadable input, matched on word boundaries
so `committee` is not a commit; and every refusal that held before the rewrite. `main` is
exercised end to end against a throwaway repository: a commit on `main` refused with exit
2 and the reason on stderr, a commit allowed after branching and off `main`, payloads that
are not a git command, an unparseable payload, and a byte-order mark.

The command-recognition cases follow: a commit or push hidden behind variable assignments,
behind each redirection form including `2>&1`, inside backticks and `$( )`, and under each
of the five wrapper programs; the executable in five spellings and cases; each push option
whose value would otherwise be read as the remote; `@` and `refs/heads/main` reduced to
the branches they name; an unresolvable switch refusing from either branch with its own
message; and the commands that must stay allowed — `echo git commit`, `grep push log.txt`,
`sudo apt install git`, `time ls`, and a commit message naming both `sudo` and `git push`.
One test asserts a documented miss rather than a fix: `sudo -u me git push` is allowed,
because only options are skipped after a wrapper and never a bare word.

### `tests/test_plan_state.py`

Covers `.claude/hooks/plan_state.py`. `parse` against a complete marker, a file with none,
a step outside 1-10, an uppercase status, a missing Branch row, a missing title, an
unreadable path, a nested `type/topic` folder read as a relative `feature`, a file outside
the plans directory falling back to its parent's name, and an unprefixed filename such as
`TEMPLATE.md` giving round 0 and an empty `feature`; `all_plans` for a missing plans
directory, recursion, files without a marker, modification-time ordering and relative
paths; `active_plan` for none, one and several active at once; `feature_rounds` for round
ordering, a nested feature folder, feature isolation and an unknown feature; `git_lines`/`current_branch` against a throwaway
repository, including a detached HEAD and a directory that is not a repository at all; and
`main` printing the plans it finds, with one active and with none. `Plan.step_name` and
`Plan.gated` are covered either side of `GATE_FROM_STEP`.

### `tests/test_stop_gate.py`

Covers `.claude/hooks/stop_gate.py`. `venv_tool` for both layouts, neither, and which wins
when both exist; `capture` for output, a non-zero exit and an expired timeout;
`changed_python_files` and `tracked_python_files` against a throwaway repository, including
a rename, a `.gitignore` and work committed on a branch; `structure_problems` in both
directions plus placeholder paths; `stray_test_files`; `missing_init_files`; and
`advisory_notes`, `notice` and `block`; `enforce` for blocking on one failure, for gathering
every check's problems into a single reason, and for returning quietly when all four pass; and
`main` for an unreadable payload, a turn already blocked once, the `.skip-gate` escape hatch,
enforcement with no plan and Python changed, and the advisory path below the gate step.

`gate_failures` is driven through a monkeypatched `venv_tool` and `capture` rather than
real executables. Running it for real would invoke Ruff, mypy and `pytest` from inside
`pytest`, and building stub executables would need a shell script on POSIX and an `.exe` on
Windows. `enforce` and `main` are driven the same way, with the four checks monkeypatched, so
neither reaches a real tool either.


## Plans: `development/`

One folder per branch, one numbered file per round inside it, created at the close of step
1 from `TEMPLATE.md` and carried through all ten steps:

```
development/
  TEMPLATE.md                     copied for each new round; never itself active
  feat/csv-export/                the folder is the branch name, so it nests one level
    01-csv-export.md              round 1
    02-streaming-writer.md        round 2, opened from round 1's recommendations
```

`development/` starts with only `TEMPLATE.md`: the hooks and their tests came from
`petermads123/template_repo`, whose own plan history stayed behind.

Each file holds the concept and acceptance criteria, the plan, the verification and test
logs, the concept-check audit, the recommendations and the pull request, plus a `Halted`
section if the build stopped to ask. Every round of a feature shares one branch and one
pull request; a later round's **Builds on** section names what the earlier rounds
delivered, and its step 6 re-checks their acceptance criteria as a regression pass.

The first line after the title is the workflow's state and is read by the hooks:

```
<!-- claude-plan step=3 status=active -->
```

`step` is 1 to 10; `status` is `active`, `done`, `parked` or `template`. Exactly one file
across the whole repo should be `active` — opening a round stands its predecessor down to
`done`, and step 9 marks the newest round `done` in the commit that opens the pull request,
so `main` never carries a live marker. Every step commits and pushes the file with what it
produced: plan files are the record of why the code looks the way it is, the state any
session resumes from, and what `/create-pr` builds the pull request body from.

## Backlog: `docs/BACKLOG.md`

Findings from reviewing this repo's own Claude configuration: confirmed defects, proposed
improvements, and decisions taken against. Deliberately **not** a plan file — it carries no
`claude-plan` marker and sits outside `development/`, the only directory
`.claude/hooks/plan_state.py` scans, so it cannot be mistaken for pipeline state.

Each entry records its routing (`/feature` or `/small-change`) so picking one up does not
mean re-deciding it. An item graduates by becoming a plan folder under `development/`, and
its entry here is deleted in the same change.

## Claude configuration: `.claude/`

| Path | Role |
|---|---|
| `settings.json` | Registers the four hooks; pre-approves ruff/mypy/pytest, `python -m`, and the git commands the pipeline uses (read-only ones plus add, commit, push, fetch, checkout, switch, merge, mv) so an unattended build never stalls on a prompt — `guard_git.py` is what keeps that safe |
| — | Every skill pins `model` and `effort` in its frontmatter; the table in `CLAUDE.md` says which and why |
| `skills/build/` | `/build` — steps 3 to 7 as one unattended block: a subagent per step on its pinned model, commit and push after each, halting rules, trace relay, resume from the marker |
| `rules/python.md` | Coding conventions, auto-loaded for `**/*.py` |
| `skills/repo-setup/` | `/repo-setup` — one-time setup of a repo made from this template; carries `main_protect.solo.json` and `main_protect.collab.json` |
| `skills/feature/` | `/feature` — starts or resumes the pipeline |
| `skills/fix/` | `/fix` — starts the pipeline from a defect: reproduces, finds the root cause, sizes the class, has the diagnosis criticised, decides whether it is a bug at all, then hands to `/conceptualize` as a fix round |
| `skills/conceptualize/` | `/conceptualize` — step 1, agree the concept |
| `skills/plan/` | `/plan` — step 2, design it |
| `skills/implement/` | `/implement` — step 3, write the code (inside `/build`) |
| `skills/verify/` | `/verify` — step 4, static verification |
| `skills/test/` | `/test` — step 5, edge-case suite |
| `skills/concept-check/` | `/concept-check` — step 6, audit against the concept |
| `skills/ship/` | `/ship` — step 7, close the round: whole-tree gates and diff review (inside `/build`) |
| `skills/recommend/` | `/recommend` — step 8, ranked follow-ups |
| `skills/create-pr/` | `/create-pr` — step 9, pull request ready for review |
| `skills/watch-pr/` | `/watch-pr` — step 10, hourly review watch until merge or close |
| `skills/small-change/` | `/small-change` — cosmetic edits, outside the pipeline |
| `agents/diagnosis-critic.md` | Subagent that tries to falsify a defect diagnosis before step 1 agrees a fix on it — re-runs the reproduction, traces the cause independently, checks the class (feeds `/fix`); may run code from a scratch directory but never writes to the tree; pinned to `opus` |
| `agents/plan-critic.md` | Read-only subagent that reads a plan against its concept and the repo before the user accepts it (feeds step 2); pinned to `opus` |
| `agents/test-designer.md` | Read-only subagent that finds edge cases; run twice at step 5 with the `input-space` and `contract` briefs |
| `agents/brainstormer.md` | Read-only subagent that proposes follow-ups through one lens — `user`, `maintainer` or `integrator`, plus `defect-class` on a fix round; three or four run in parallel at step 8 |
| `agents/structure-auditor.md` | Read-only subagent that reconciles this file (feeds steps 4 and 6) |

### `.claude/hooks/plan_state.py`

Shared by the other hooks: parses the `claude-plan` marker out of the plan files and
answers which plan is active, plus the small git helpers the hooks need. Importable by its
siblings because Python puts a script's own directory on `sys.path`. Stdlib only.

| Signature | Description |
|---|---|
| `Plan` | Frozen dataclass: `path`, `step`, `status`, `title`, `branch`, `feature` (the folder relative to `development/`, so the branch name with its `/`; empty for `TEMPLATE.md`), `round_number`, plus `step_name` and `gated` properties. |
| `parse(path: Path) -> Plan \| None` | Parse one plan file, or None if it has no valid marker. |
| `all_plans(project_dir: Path) -> list[Plan]` | Every parseable plan in every feature folder, most recently modified first. |
| `active_plan(project_dir: Path) -> Plan \| None` | The plan the pipeline is working through. |
| `feature_rounds(project_dir: Path, feature: str) -> list[Plan]` | One feature's rounds, oldest first; `feature` is the folder relative to `development/`, as on `Plan.feature`. |
| `git_lines(project_dir: Path, args: list[str]) -> list[str]` | Run git, return output lines. |
| `current_branch(project_dir: Path) -> str` | The checked-out branch, or `""`. |
| `main() -> None` | Showcase: prints the plans found, the active one and its sibling rounds. |

`GATE_FROM_STEP = 8` is the step at which the stop gate starts blocking: steps 3 to 7 are
the build, which carries its own gates and must be able to halt on a red tree.
`PLAN_DIR = development` is the directory it scans; `Plan.feature` is a folder path relative
to it, so a branch-named folder such as `feat/csv-export` comes back with its `/`.

### `.claude/hooks/session_brief.py`

`SessionStart` hook. Injects the active plan's step into a new session's context, the skill
that resumes it (`/build` for steps 3 to 7), plus what any earlier rounds of the same
feature delivered, so work resumes without the user having to re-explain it. Silent when
no plan is active — including while a pull request is open, since step 9 closes the plan.
Stdlib only.

| Signature | Description |
|---|---|
| `brief(project_dir: Path, plan: Plan) -> str` | Describe one active plan's state. |
| `main() -> None` | Entry point: emit the brief as session context. |

### `.claude/hooks/guard_git.py`

`PreToolUse` hook on `Bash`. Refuses a `git commit` or `git push` that would land on
`main`. Reads the command the way a shell does — `shlex` resolves quoting, so a `;` or `|`
inside a commit message stays part of the message — then splits it on the real separators
into one invocation per segment. The grouping delimiters `(`, `)`, `{` and `}` split too, so
a command hidden inside `(git commit -m "x")` is seen rather than left with `(` sitting
where its name should be.

Each segment is judged against the branch that will be checked out when it runs. Only `&&`
guarantees its left side succeeded, so a branch switch carries forward across a run of
separators that is `&&` and newlines, and across nothing else: `git checkout -b feat/x &&
git commit` is allowed from `main`, and so is the same pair with the `&&` ending the line,
while `;`, `|`, `||`, `&`, a bare newline or a mixed run such as `; &&` is refused. A switch
that may not have taken effect here is distrusted the same way: one made inside a subshell
that has since closed, or aimed elsewhere by a global `-C`, `--git-dir` or `--work-tree`,
leaves the branch as it was. What still cannot be read is refused when it names `commit` or
`push` — matched on word boundaries, so `committee` is not a commit — while `main` is
checked out, and allowed anywhere else.

Within a segment it finds the command name where a shell would, after the prefix of
variable assignments and redirections, so `GIT_EDITOR=true git commit` and
`>log git commit` are seen. Backticks around a substitution are stripped, the executable is
matched without regard to case, and a short list of wrapper programs — `sudo`, `env`,
`time`, `nohup`, `doas` — is stepped over. That list is deliberately incomplete: a wrapper
nobody listed is a miss, which is safe, while scanning a segment for any `git` token would
refuse `echo git commit`, which is the failure this module treats as worse. Only options are
skipped after a wrapper, never a bare word, so an option that takes a value hides what
follows it.

A push's destination is read with the same care. The arguments are walked rather than
filtered, so an option that takes a value — `-o`, `--push-option`, `--repo`,
`--receive-pack`, `--exec` — does not leave its value standing where the remote should be,
and `git push -o ci.skip origin` is seen as the bare push it is. Every ref is then reduced
to the branch it names: a leading `+` dropped, the destination half of a `src:dst` pair
taken, `refs/heads/` stripped, backticks removed and `@` read as `HEAD`. Switch targets go
through the same reduction, so `git checkout refs/heads/main` is a switch to `main`.

A branch switch whose target only the running shell can resolve — `git checkout -`,
`@{-1}` — leaves the branch *unknown* rather than unchanged, and a `commit` or `push` that
meets an unknown branch is refused with a message saying so rather than the one about
`main`. Stdlib only.

| Signature | Description |
|---|---|
| `Segment` | Frozen dataclass: `tokens` and the `separator` that preceded them — one of `SEPARATORS`, a newline, or a grouping delimiter (`""` for the first). |
| `segments(command: str) -> list[Segment] \| None` | Split a command into invocations, or None if it cannot be read. |
| `git_subcommand(tokens: tuple[str, ...]) -> tuple[str, tuple[str, ...]]` | Identify the git subcommand and its arguments. |
| `push_targets_main(args: tuple[str, ...], branch: str) -> bool` | Whether a push would update `main`. |
| `switch_target(subcommand: str, args: tuple[str, ...]) -> str` | The branch a `checkout`/`switch` moves to, `""` when it moves none, or the sentinel `UNRESOLVED` (`"?"`) for a target only the running shell can resolve — `-` and `@{-1}`. |
| `violation(command: str, branch: str) -> str` | The reason to refuse, or `""` to allow. |
| `main() -> None` | Entry point: allow or refuse the command. |

### `.claude/hooks/lint_py.py`

`PostToolUse` hook. Runs `ruff format` and `ruff check --fix` on any `.py` file Claude
writes or edits, and reports unfixable issues back via exit code 2. Stdlib only.

| Signature | Description |
|---|---|
| `find_ruff(project_dir: Path) -> Path \| None` | Locate Ruff in the project venv. |
| `run(ruff: Path, args: list[str]) -> CompletedProcess[str]` | Run Ruff, capturing output. |
| `target_file(payload: dict[str, object], project_dir: Path) -> Path \| None` | Extract the edited `.py` file from the hook payload. |
| `main() -> None` | Entry point: format, fix, report. |

### `.claude/hooks/stop_gate.py`

`Stop` hook. Reads the active plan's step to decide how strict to be: advisory through step
7, blocking from step 8 and whenever no plan is active, and only when a Python file changed
in the tree or on the branch. When it blocks it runs ruff, mypy
and pytest, cross-checks `STRUCTURE.md` against the modules on disk, reports any test file
sitting outside `tests/` where `pytest` would silently never collect it, and reports any
package directory under `src/` missing its `__init__.py`. Bypass with
`.claude/.skip-gate`. Stdlib only.

| Signature | Description |
|---|---|
| `venv_tool(project_dir: Path, name: str) -> Path \| None` | Locate a tool in the project venv. |
| `capture(cmd: list[str], cwd: Path, timeout: int) -> CompletedProcess[str]` | Run a command, capturing output. |
| `changed_python_files(project_dir: Path) -> set[str]` | Python files changed in the tree or on this branch. |
| `tracked_python_files(project_dir: Path) -> set[str]` | All non-ignored Python files. |
| `structure_problems(project_dir: Path) -> list[str]` | File-level drift between this file and disk. |
| `stray_test_files(project_dir: Path) -> list[str]` | Test files outside `tests/`, which pytest never collects. |
| `missing_init_files(project_dir: Path) -> list[str]` | Package directories under `src/` with no `__init__.py`. |
| `gate_failures(project_dir: Path) -> list[str]` | Run the verification set, collect failures. |
| `advisory_notes(project_dir: Path, plan: Plan, changed: set[str]) -> list[str]` | Non-blocking observations for the steps below the gate. |
| `notice(message: str) -> None` | Show the user a message without blocking. |
| `block(reason: str) -> None` | Emit the block decision and exit. |
| `enforce(project_dir: Path) -> None` | Run the verification set and block on failure. |
| `main() -> None` | Entry point: decide whether the turn may end. |

# State snapshot and restore

<!-- claude-plan step=2 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/fixed-output` |
| Round | `3` |
| Branch | `claude/festive-maxwell-xft6pj` |
| Started | `2026-09-23` |

The branch is the one this hosted session was told to push to; the folder keeps the
conventional name `feat/fixed-output`.

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
| 2 | Plan | `/plan` | with the user | in progress |
| 3 | Implement | `/implement` | in `/build` | pending |
| 4 | Verify | `/verify` | in `/build` | pending |
| 5 | Test | `/test` | in `/build` | pending |
| 6 | Concept check | `/concept-check` | in `/build` | pending |
| 7 | Ship | `/ship` | in `/build` | pending |
| 8 | Recommend | `/recommend` | with the user | pending |
| 9 | Pull request | `/create-pr` | with the user | pending |
| 10 | Review | `/watch-pr` | on the pull request | pending |

Statuses: `pending`, `in progress`, `done`.

## Builds on

| Round | File | What it delivered |
|---|---|---|
| 1 | `01-fixed-output.md` | `PIController.fixed_output`: a level in `[0, 1]` or `None` that replaces the PI output before the mode mapping while set; the PI loop and integral keep running underneath, the fixed command is recorded in `history`, `reset()` leaves it alone. Plus, as small changes: a hold window in the `test.py` simulation with a README paragraph on the release burst, and the constructor documented once. |
| 2 | `02-attribute-surface.md` | Every setting (`kp`, `ki`, `setpoint`, `mode`, `fixed_output`) a validating property backed by one private finite-number helper: `TypeError` for a non-number or `bool`, `ValueError` for a non-finite or out-of-range value, `OverflowError` for an int too large for a float, all naming the attribute, previous value kept on failure; the constructor assigns through the setters; `update` validates before storing; a read-only `pi_output` (the clamped PI demand of the last step, `None` before the first update and after `reset()`); `OUTPUT_MIN`/`OUTPUT_MAX` re-exported from the package root. |

This round came from recommendation **R3** of round 1, which read:

> **R3** — A serialisable state snapshot and restore covering `integral`, `history`,
> `setpoint`, `mode` and `fixed_output`, for the AppDaemon caller to persist across
> reloads. AppDaemon reloads the app on every code change and HA restart, and the
> controller lives in process memory. A reload during a price spike silently constructs a
> fresh controller with `fixed_output=None` and opens the valve at full demand while prices
> are high. This round turned a pre-existing gap (losing the integral) into a costly one
> (losing the override).

The user's decision on it: "This would also in general solve the problem of a HA or
AppDaemon restart such that the entire past periods doesn't have to be repopulated, run
this through the full pipeline. Keep in mind that this should be implemented in HA. But
still keep it general."

It also carries two of round 2's recommendations, folded in at that round's step 8:

> **R1** — Bring `history_length` under this round's contract (`TypeError` naming the
> attribute for `bool` or a non-`int`, `ValueError` below 1) and expose it as a read-only
> property. The one constructor input still outside the rule: `history_length=True`
> silently builds a one-slot window, `"24"` raises a bare comparison error. Nothing
> downstream can read the window size, and round 3's restore needs it to rebuild the deque.

> **R2** — Decide the float edge semantics once, in the helper this round built: a
> non-finite raw PI sum maps to a defined command (closed is the safe side), and `-0.0` is
> normalised to `+0.0`. `pi_output`'s promised range currently holds for a `nan` raw only
> by accident of `min`/`max` argument order, and round 4 will move that line. A `nan` can
> enter through `integral`, which round 3 starts writing from a restore.

The user's steer at round 2's gate, which bounds this round: the repo stays a simple
package that assumes valid inputs arrive; what Home Assistant delivers, and how the caller
stores the snapshot, is the HA side's problem.

What is already on the branch that this round must not break:

- Round 1's criteria A1–A6 and round 2's B1–B7, both re-checked at this round's step 6.
- The 525-test suite; none may be weakened. Existing tests that read `integral` as a plain
  attribute, and the two STRUCTURE.md quirk paragraphs about `-0.0` and a `nan` raw sum,
  will need updating if R2 lands.
- The `test.py` simulation and its hold window.
- Every setting's validating-property contract from round 2: a restore must go through the
  same setters, not around them.

---

## 1. Concept

### What this is

`PIController` can hand back its complete state as a plain dictionary of JSON-friendly
values, and can be rebuilt from such a dictionary. The snapshot holds every setting and
every piece of running state: the gains, the setpoint, the mode as its string value, the
window length, the fixed-output hold, the integral, and the command history oldest first.
The caller stores it wherever suits them, in a file or on a Home Assistant entity, and
hands it back after a restart. Restore goes through the same validating setters as every
other write, so a bad snapshot raises the same errors as a bad assignment and produces no
controller. The PI demand is not in the snapshot: it is derived, and reads `None` after a
restore until the next `update`, exactly as after `reset()`.

The snapshot includes the settings and not only the running state, so a round trip gives
back exactly the controller there was; a caller who has changed gains in code sets them
after restoring. Restore builds a new controller rather than loading into an existing one,
which keeps one path. The format is a dictionary rather than a JSON string, so the caller
chooses the serialiser. The format is strict: a missing key, an unknown key, or a history
longer than the window is an error, because a snapshot that half-applies is worse than one
that refuses. `integral` becomes a validating property like the other attributes, because
restore now writes it. There is no format version key, per the user's steer to keep the
package simple.

Two items folded in from round 2's recommendations: `history_length` gets the round 2
validation contract and a read-only getter; and the float edge cases are decided once: a
raw PI sum that is not finite, reachable only with extreme finite inputs, maps to the
closed command rather than depending on `min`/`max` argument order, and `-0.0` reads back
as `+0.0` from every numeric setter and from `update`.

### Why it is worth building

AppDaemon recreates the app on every Home Assistant restart and on every code reload, and
the controller lives in process memory. Today that means a fresh controller: the integral
starts from zero, the duty-cycle window is empty for the next two hours, and a
`fixed_output` hold set for a price spike is silently gone, so the valve opens at full
demand while prices are high. With a snapshot the caller saves the state after each update
and restores it on startup, and the restart is invisible to the room.

### Inputs and outputs

- **Snapshot, out:** a `dict` with exactly these keys: `kp`, `ki`, `setpoint` (floats),
  `mode` (the enum's string value), `history_length` (int), `fixed_output` (float or
  `None`), `integral` (float), `history` (a list of floats, oldest first). Every value is a
  built-in type `json.dumps` accepts. The dict and its list are fresh objects.
- **Restore, in:** such a dict. Each setting is applied through its validating setter;
  `integral` through its new setter; `history` is checked to be a list of finite numbers in
  the actuator range no longer than `history_length`; `history_length` through its new
  check. A missing or unknown key raises `ValueError` naming it; a bad value raises what the
  setter raises, naming the key. On any failure no controller is produced.
- **Restore, out:** a new `PIController` equal to the original in every setting and in
  `integral`, `history`, `duty_cycle`, `is_history_full` and `fixed_output`, with
  `pi_output` `None`.
- **`history_length`, in and out:** an `int` of at least 1; `bool` or a non-`int` raises
  `TypeError` naming it, below 1 `ValueError`; readable back.
- **`integral`, in and out:** a finite real number under the round 2 contract, readable
  back as `float`.

### How it connects to the rest of the repo

- Changes `src/heatingsystem/pi_controller/pi_controller.py` (`PIController`, its showcase
  `main`), `tests/test_pi_controller.py`, `STRUCTURE.md` and the README usage section (a
  save-and-restore example).
- The two package `__init__.py` files are untouched: no new public names at package
  level.
- `test.py` is untouched.
- Existing tests that write `integral` as a plain attribute keep working, since the property
  accepts every value they assign; the two STRUCTURE.md paragraphs describing the `-0.0`
  and `nan`-raw quirks as "left alone" are rewritten because this round decides them.

### Explicitly out of scope

- Any storage or I/O: files, Home Assistant entities, AppDaemon APIs.
- Periodic or automatic snapshots; the caller decides when to save.
- Schema versioning or migration of old snapshots.
- Loading a snapshot into an existing instance.
- Anything on the Home Assistant side, including converting entity states.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| C1 | The snapshot is a dictionary of built-in types that `json.dumps` accepts, holding `kp`, `ki`, `setpoint`, `mode` (string), `history_length`, `fixed_output`, `integral` and `history` (a list, oldest first); it is a copy, so changing either it or the controller afterwards leaves the other alone. |
| C2 | Restoring from a snapshot yields a controller whose settings, `integral`, `history`, `duty_cycle`, `is_history_full` and `fixed_output` equal the original's, and whose next `update` on the same measurement returns the same command the original would have; `pi_output` is `None` until then. |
| C3 | A snapshot passed through `json.dumps` and `json.loads` restores identically to C2. |
| C4 | A snapshot with a missing key, an unknown key, a value the matching setter rejects, a non-finite integral, a history entry outside the actuator range, or a history longer than `history_length` raises `TypeError` or `ValueError` naming the key, and no controller is produced. |
| C5 | `history_length` is readable back; a `bool` or non-`int` raises `TypeError` naming it and a value below 1 raises `ValueError`, at construction and in restore. |
| C6 | `integral` is a validating property: a non-number raises `TypeError`, a non-finite value `ValueError`, both naming it, previous value kept. |
| C7 | A non-finite raw PI sum yields the closed command and `pi_output` of `0.0` with the integral held; `-0.0` given to any numeric setter or to `update` reads back as `+0.0`. |

### Open questions

None. Decisions taken in step 1 without asking, because one reading was clearly right for
a simple package: the snapshot carries settings as well as state; restore builds a new
instance; the format is a strict dict with no version key; `pi_output` is not in it.

---

## 2. Plan

### Approach

**Chosen — two methods on the controller, one more private check, two guarded lines.**
`to_dict()` returns a fresh dict of the eight keys, reading each through the public
properties and copying the history into a list. `from_dict()` is a classmethod that first
checks the key set (missing or unknown keys raise `ValueError` naming them), then narrows
and validates every value with the helpers the setters already use (`_finite` for the
numbers, a new `_window_length` for `history_length`, the enum for `mode`), constructs a
controller through the normal constructor so every setter runs again, assigns `integral`
through its new property, and extends the history after checking each entry is a finite
number in the actuator range and the list is no longer than the window. `history_length`
gets the private check and a read-only property backed by an `int` field, since
`deque.maxlen` is typed optional. `integral` becomes a property on `_finite`. In `_finite`,
`return number + 0.0` normalises `-0.0`. In `update`, the clamp and the anti-windup chain
are guarded by one boolean, "the raw sum and the tentative integral are both finite":
when it is false the command is `OUTPUT_MIN` and the integral is held.

**Rejected — a typed snapshot class** (a `TypedDict` or dataclass): documents the keys for
a type checker, but `json.loads` hands back a plain dict anyway, and it adds a public name
and a STRUCTURE.md row to a package the user wants kept simple. The keys are documented in
the two methods' docstrings instead. **Rejected — a JSON string in and out**: ties the
package to one serialiser; the caller may prefer YAML or an entity attribute dict.
**Rejected — restore into an existing instance**: section 1 rules it out; one path.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/heatingsystem/pi_controller/pi_controller.py` | changed | `_window_length` helper; `history_length` and `integral` properties; `to_dict`/`from_dict`; the `-0.0` normalisation in `_finite`; the finite guard in `update`; docstrings; showcase |
| `tests/test_pi_controller.py` | changed | New section for T1–T7 |
| `STRUCTURE.md` | changed | New rows, the constructor row, the `update` row, the test-file paragraph, and the two quirk paragraphs rewritten as decided behaviour |
| `README.md` | changed | A save-and-restore example in the usage section |

No new module and no change to either `__init__.py`.

### Public API

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `PIController.to_dict(self) -> dict[str, object]` | `pi_controller.py` | The snapshot: a fresh dict with exactly the keys `kp`, `ki`, `setpoint`, `mode` (the enum's `.value`), `history_length`, `fixed_output`, `integral`, `history` (a new `list[float]`, oldest first). Every value is a built-in `json.dumps` accepts. | C1 |
| `PIController.from_dict(cls, data: Mapping[str, object]) -> Self` (classmethod) | `pi_controller.py` | Rebuild a controller from a snapshot. Raises `ValueError` naming the keys for a missing or unknown key; for each value, whatever the matching setter or check raises, naming the key; `TypeError` if `history` is not a list or tuple; `ValueError` naming `history` if it is longer than `history_length`, and naming `history[i]` for an entry that is non-finite or outside `[OUTPUT_MIN, OUTPUT_MAX]`. No controller exists on failure. `pi_output` is `None` on the result. | C2, C3, C4, C5 |
| `PIController.history_length -> int` (read-only) | `pi_controller.py` | The window length given at construction. | C5 |
| `PIController.integral -> float` / setter `(value: float) -> None` | `pi_controller.py` | The integral accumulator, now a validating property on the round 2 contract, naming `integral`. | C6 |
| `PIController(kp: float = 0.3, ki: float = 0.015, mode: HeatingMode \| str = HeatingMode.RADIATOR, setpoint: float = 21.0, *, history_length: int = 24, fixed_output: float \| None = None)` | `pi_controller.py` | Unchanged signature. `history_length` now goes through `_window_length`: `TypeError` naming it for a `bool` or non-`int`, `ValueError` below 1. | C5 |
| `PIController.update(measured: float, setpoint: float \| None = None) -> float` | `pi_controller.py` | Unchanged signature. If the raw PI sum or the tentative integral is not finite, the PI *demand* is `OUTPUT_MIN`, `pi_output` is `0.0`, and the integral is held; the guard defines the demand only, so while `fixed_output` is set the command is still the fixed level (round 1's A2). Otherwise as before. | C7 |
| `main() -> None` | `pi_controller.py` | Showcase gains a save, JSON round trip, restore and a matching next command. | — (showcase) |

`_finite` (private) also changes: `-0.0` comes back as `+0.0`. `reset()`, `fixed_output`,
`pi_output`, `history`, `duty_cycle`, `is_history_full` are unchanged.

### Implementation guide

1. In `_finite`, change the last line to `return number + 0.0` and add one sentence to its
   docstring: a negative zero is returned as positive zero.
2. Add two private module-level helpers below `_finite`. `_window_length(value: object) -> int`:
   `if isinstance(value, bool) or not isinstance(value, int): raise TypeError(f"history_length must be an int, got {value!r} ({type(value).__name__}).")`;
   `if value < 1: raise ValueError(f"history_length must be >= 1, got {value}.")` (the
   existing message); `if value > sys.maxsize: raise OverflowError(f"history_length is too large, got {value}.")`
   (a `deque` refuses a larger `maxlen` with a bare error); `return value`. And
   `_level(name: str, value: object) -> float`: `level = _finite(name, value)`; if
   `level < OUTPUT_MIN or level > OUTPUT_MAX`: `raise ValueError(f"{name} must be in [{OUTPUT_MIN}, {OUTPUT_MAX}], got {value!r}.")`;
   return `level`. Rewrite the `fixed_output` setter's non-`None` branch to
   `self._fixed_output = _level("fixed_output", value)`, keeping its existing message text
   by passing the check through (the message may drop the "or None" suffix; the existing
   round 1 tests match on the value's repr and the attribute name only). Google docstrings
   with `Raises:`. `import sys` at the top.
3. Add class-level annotations `_history_length: int` and `_integral: float` beside the
   existing ones. In the constructor replace the inline `history_length < 1` check with
   `self._history_length = _window_length(history_length)`, build the deque with
   `maxlen=self._history_length`, and replace `self.integral: float = 0.0` with
   `self.integral = 0.0` (through the setter). Keep the constructor order otherwise.
4. Add the `history_length` read-only property (returns `self._history_length`) and the
   `integral` property pair (getter returns `self._integral`; setter stores
   `_finite("integral", value)`) under *Properties*, after `setpoint`. Update
   `is_history_full` to compare against `self._history_length`.
5. In `update`, after computing `new_integral` and `raw`, add
   `finite = math.isfinite(raw) and math.isfinite(new_integral)`. Change the clamp line to
   `u = max(OUTPUT_MIN, min(OUTPUT_MAX, raw)) if finite else OUTPUT_MIN`. Wrap the
   three-branch anti-windup chain in `if finite:` (indent it; a comment says a non-finite
   sum holds the integral and closes the valve). Nothing else in `update` changes; the
   `_pi_output = u` line and the substitution stay where they are. Update the docstring.
6. Add `to_dict` under *Public interface* after `reset`: build and return the dict
   literally in key order `kp, ki, setpoint, mode, history_length, fixed_output, integral,
   history`, with `self.mode.value` and `list(self._history)`. Docstring lists the keys and
   says the result is JSON-serialisable and a copy.
7. Add `from_dict` as a `@classmethod` right after `to_dict`, with
   `from collections.abc import Mapping` and `from typing import Self`. Body, in order:
   - `if not isinstance(data, Mapping): raise TypeError(f"snapshot must be a mapping, got {type(data).__name__}.")`
     (a caller passing the JSON text instead of the loaded dict gets a named error).
   - `expected = frozenset({...eight keys...})` as a module-level constant `_SNAPSHOT_KEYS`;
     `missing = sorted(expected - data.keys())`, `unknown = sorted(data.keys() - expected)`;
     raise `ValueError(f"snapshot is missing keys {missing}.")` / `ValueError(f"snapshot has unknown keys {unknown}.")`
     (missing checked first).
   - `mode = data["mode"]`; `if not isinstance(mode, (HeatingMode, str)): raise ValueError(f"mode must be a HeatingMode or one of {[m.value for m in HeatingMode]}, got {mode!r}.")`
     (the setter's own message shape).
   - `fixed = data["fixed_output"]`; `fixed_output = None if fixed is None else _finite("fixed_output", fixed)`.
   - `history = data["history"]`; `if not isinstance(history, (list, tuple)): raise TypeError(f"history must be a list, got {history!r} ({type(history).__name__}).")`.
   - `controller = cls(kp=_finite("kp", data["kp"]), ki=_finite("ki", data["ki"]), mode=mode, setpoint=_finite("setpoint", data["setpoint"]), history_length=_window_length(data["history_length"]), fixed_output=fixed_output)`
     — the constructor's setters validate again, including the `fixed_output` range.
   - `controller.integral = _finite("integral", data["integral"])`.
   - `if len(history) > controller.history_length: raise ValueError(f"history has {len(history)} entries but history_length is {controller.history_length}.")`.
   - For `i, entry in enumerate(history)`: `levels.append(_level(f"history[{i}]", entry))`;
     only after the loop `controller._history.extend(levels)`, so a failure part-way leaves
     nothing (the controller is discarded anyway).
   - `return controller`. `pi_output` is `None` because the constructor set it so.
   Docstring: the keys, the errors, that the result's `pi_output` is `None`.
8. Class docstring: rewrite the paragraph that lists the validating properties and says
   `integral` "remains a plain public attribute": `integral` is now a validating property
   on the same contract and `history_length` is validated at construction and read-only;
   update the `Args:` entry for `history_length`; add `history_length`'s `TypeError` and
   `OverflowError` to `Raises:`; mention `to_dict`/`from_dict` in one sentence.
9. Showcase: add a new case in showcase form after the floor-heating run and before the
   `mode` reassignment, on a fresh controller so no existing demo's output changes: bind
   `hold_level = 0.2  # None, or a level in [0, 1]`, build `ctrl_saved` with
   `fixed_output=hold_level`, run two updates at a cold measurement,
   `snapshot = ctrl_saved.to_dict()`, `text = json.dumps(snapshot)`,
   `restored = PIController.from_dict(json.loads(text))`, then
   `next_saved = ctrl_saved.update(measured=cold_temp)` and
   `next_restored = restored.update(measured=cold_temp)`; print the JSON text,
   `restored.fixed_output` (the hold survived the round trip, which is what this round is
   for) and both next commands. `import json` at the top. Add one `ValueError` demo for a
   snapshot with a missing key (`bad_snapshot = {"kp": 0.3}` on its own line).
10. `STRUCTURE.md`: add rows for `to_dict`, `from_dict`, `history_length`, `integral`;
    rewrite the constructor row ("`history_length` must be at least 1, checked inline" and
    "`integral` is a plain public attribute"); rewrite the `update` row for the finite
    guard; the `main()` row; the test-file paragraph; and replace the two "considered and
    left alone" quirk paragraphs with the decided behaviour.
11. README: a short block after the fixed-output block: `state = radiator.to_dict()`,
    a comment that the caller stores it (a file, an entity attribute) and restores with
    `hs.PIController.from_dict(state)` on startup, and that `json.dumps` accepts it.
12. Run `ruff check .`, `ruff format --check .`, `mypy`,
    `python -m heatingsystem.pi_controller.pi_controller`, and `pytest`; every existing
    test is expected to pass unchanged (existing writes to `ctrl.integral` assign finite
    floats, which the setter accepts).

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | `to_dict` on a controller with non-default settings, a hold, a built-up integral and a partly filled window returns a dict with exactly the eight keys and the expected values (`mode` as its string, `history` as a list oldest first, `fixed_output` `None` when unset), `json.dumps` accepts it, and mutating the returned dict or its list, or updating the controller afterwards, leaves the other unchanged. | C1 |
| T2 | `from_dict(to_dict())` gives a controller equal in every setting and in `integral`, `history`, `duty_cycle`, `is_history_full`, `fixed_output`, with `pi_output` `None`; and for a sequence of measurements the restored controller's commands equal the original's exactly, in both modes, fixed and unfixed, with a full and a partly filled window. | C2 |
| T3 | The same as T2 after `json.dumps` and `json.loads`, including a controller whose window is empty and one whose `fixed_output` is `None`. | C3 |
| T4 | Each of: a missing key, an extra key, a `kp` of `"0.3"`, a `mode` of `"steam"`, a `fixed_output` of `1.5`, an `integral` of `nan`, a `history` that is not a list, a history entry of `1.5` and of `nan`, a history one longer than `history_length`, and a `kp` of `10**400` (which raises `OverflowError`, what the setter raises) raise an error whose message names the key (with index for entries), and the original controller is untouched; missing keys are reported before unknown ones; passing the JSON text instead of the loaded dict raises `TypeError` naming the snapshot. | C4 |
| T5 | `history_length` reads back at construction and after restore; assigning to it raises `AttributeError` and the window is unchanged; `True`, `2.0`, `"24"` and `None` raise `TypeError` naming it, `0`/`-1` `ValueError`, and `2**63` `OverflowError`, at construction and via `from_dict`. | C5 |
| T6 | `integral` reads back as `float` after assignment of an `int`; `nan`/`inf` raise `ValueError`, `"1"`/`True`/`None` `TypeError`, and `10**400` `OverflowError`, naming `integral`, previous value kept; `update` still advances it. | C6 |
| T7 | With finite inputs that make the raw sum `+inf` (`kp=1e300`, `ki=0.0`, error `1e10`) and `-inf` (mirrored), an unfixed radiator `update` returns `0.0` and appends it, `pi_output` is `0.0`, `integral` is unchanged; with `integral=1e308` (through the setter), `ki=-0.015`, `setpoint=1e308`, `measured=0.0` the tentative integral overflows to `inf` and `raw` to `-inf`: no raise, integral held, command `0.0`, history grows by one (the case that would otherwise raise mid-`update` through the new setter); with `fixed_output=0.3` and a non-finite raw sum the command is `0.3` in radiator mode and the floor modulation of `0.3` in floor mode, `pi_output` is `0.0`, integral unchanged; `-0.0` assigned to `kp`, `ki`, `setpoint`, `integral` and `fixed_output`, and passed as a per-call `setpoint`, reads back with a positive sign (`math.copysign`), and a radiator controller with `fixed_output=-0.0` returns a positively signed command; every existing twin-integral and hand-computed test still passes. | C7 |

### Risks

- **The anti-windup block changes shape.** Wrapping it in `if finite:` must not alter any
  finite-path result; round 1's exact twin-integral tests and round 2's hand computations
  are the guard. If any of them fails after the change, that is an implementation slip to
  fix once; if it fails the same way again, **halt**.
- **Existing tests that assign `integral`.** They assign finite floats and keep passing;
  if any assigns something the setter rejects, that test is documenting a state the
  concept now forbids, and the build should say so and halt rather than weaken it.
- **mypy on `Self` and `Mapping[str, object]`.** `Self` is in `typing` since 3.11; the
  classmethod returns `cls(...)`, which mypy accepts. Every value read from `data` is
  narrowed by a helper before use, so no cast or `Any` is needed; if mypy still objects to
  the `mode` branch, `isinstance` narrowing to `HeatingMode | str` is what it needs.
- **A hand-written snapshot may carry `int`s** (`"kp": 1`); `_finite` converts them, so
  the round trip is exact for every float `json` can represent (`json.dumps(1.0)` is
  `"1.0"` and loads as a float). `-0.0` survives JSON as `-0.0` and is normalised on the
  way in.
- **How C7 is read against round 1.** The finite guard defines the PI *demand*. While
  `fixed_output` is set the command is still the fixed level, as A2 requires; `pi_output`
  is `0.0` and the integral is held. Round 1's A1 ("same commands as before") holds on the
  finite path only; on the non-finite path C7 supersedes it, by the user's decision at
  this gate. Step 6 reads A1 and A2 that way.
- **How C4 is read.** "`TypeError` or `ValueError`" means "what the matching setter or
  check raises"; a huge `int` therefore raises `OverflowError` naming the key, as section
  1's Inputs paragraph says. Stated to the user at this gate.
- **Halting line.** A case where the snapshot's key set must grow to satisfy a criterion
  (for example a criterion that turns out to need `pi_output`) would amend section 1:
  **halt**.

### Critique

Findings from the `plan-critic` read, verdict *accept with changes*; all nine applied.

1. **C7 and round 1's A2 meet when the sum is non-finite while a hold is set, and the plan
   picked a side silently.** Applied: the `update` row and Risks say the guard defines the
   demand only, the fixed level still wins, and A1 holds on the finite path; T7 covers it in
   both modes; stated to the user at the gate.
2. **Two T7 clauses could not be built** (the integral term cannot be non-finite alone;
   `measured` is never stored). Applied: T7 rewritten with buildable `+inf`/`-inf` cases,
   the overflowing-integral case that would otherwise raise mid-`update`, and the `-0.0`
   cases on `setpoint` and `fixed_output`.
3. **C4 names two error classes but a huge `int` raises `OverflowError`.** Applied: Risks
   record the reading, T4 covers it; stated to the user at the gate.
4. **`history_length=2**63` and `from_dict(json_text)` were undecided.** Applied: the
   window check raises `OverflowError` above `sys.maxsize`; `from_dict` rejects a
   non-mapping with `TypeError`; T4 and T5 cover both.
5. **No test intent for the read-only getter or `integral`'s `OverflowError`.** Applied to
   T5 and T6.
6. **The class docstring's "integral remains a plain attribute" sentence was not in the
   guide.** Applied to step 8.
7. **`typing.Mapping` would fail Ruff, and the `json.loads` risk misstated ints.** Applied.
8. **The showcase would have changed a later demo's output and did not show the hold
   surviving.** Applied: a fresh controller with a hold, restored, its `fixed_output`
   printed.
9. **The history range check duplicated the `fixed_output` setter's.** Applied: one private
   `_level` helper used by both.


---

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry.

---

## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

| Check | Result |
|---|---|
| `ruff check .` | |
| `ruff format --check .` | |
| `mypy` | |
| Plan completeness | every signature in the Public API table exists as written |
| `STRUCTURE.md` | in sync |
| `python -m <package>.<module>` | |

---

## 5. Test log

> Written in step 5: the dynamic half.

| Intent | Test names | Result |
|---|---|---|

Edge cases considered and deliberately skipped, with reasons:

---

## 6. Concept check

> Written in step 6, against section 1 — not against section 2. The question is whether
> the thing built is the thing agreed, not whether it matches the plan.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | | | |

Drift found, and what was done about it:

### Earlier rounds still hold

> Later rounds only. Re-check every acceptance criterion from every earlier round in this
> folder: this round changed code they depend on, and their tests passing is necessary but
> not sufficient — a criterion can be satisfied by tests that no longer describe what the
> feature does.

| Round | # | Criterion | Still met | Evidence |
|---|---|---|---|---|

---

## 7. Ship log

| Field | Value |
|---|---|
| Commits | |
| Pushed to | |

---

## 8. Recommendations

> Written in step 8. Follow-up work this change makes possible or desirable. Not bugs —
> a bug found here goes back through `/build` before the pull request.

| # | Recommendation | Why it helps | Effort | Decision |
|---|---|---|---|---|
| R1 | | | | |

Decisions: `deferred`, `rejected`, or `next round` — a new numbered file in this folder,
taken back through steps 1 to 7 on the same branch.

---

## 9. Pull request

> Written in step 9, in the commit that opens the pull request — so the URL is not known
> yet and the pull request is found from the branch. The review itself is recorded on the
> pull request thread, not here: this file is `done` from step 9 on.

| Field | Value |
|---|---|
| URL | opened by step 9 — see the branch's pull request |
| Opened as | ready for review |

---

## Halted

> Only if the build stopped. Written by `/build`: the step, the reason verbatim from the
> step that halted, the question for the user — and, once answered, the answer and what
> changed because of it. Never deleted; it is the record of where the plan was thinner
> than the code needed.

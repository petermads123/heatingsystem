# Modulator: the actuator mapping as its own object

<!-- claude-plan step=5 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/fixed-output` |
| Round | `4` |
| Branch | `claude/festive-maxwell-xft6pj` |
| Started | `2026-09-23` |

The branch is the one this hosted session was told to push to; the folder keeps the
conventional name `feat/fixed-output`.

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
| 2 | Plan | `/plan` | with the user | done |
| 3 | Implement | `/implement` | in `/build` | done |
| 4 | Verify | `/verify` | in `/build` | done |
| 5 | Test | `/test` | in `/build` | in progress |
| 6 | Concept check | `/concept-check` | in `/build` | pending |
| 7 | Ship | `/ship` | in `/build` | pending |
| 8 | Recommend | `/recommend` | with the user | pending |
| 9 | Pull request | `/create-pr` | with the user | pending |
| 10 | Review | `/watch-pr` | on the pull request | pending |

Statuses: `pending`, `in progress`, `done`.

## Builds on

| Round | File | What it delivered |
|---|---|---|
| 1 | `01-fixed-output.md` | `PIController.fixed_output`: a level in `[0, 1]` or `None` that replaces the PI output before the mode mapping while set; the PI loop and integral keep running underneath, the fixed command is recorded in `history`, `reset()` leaves it alone. A hold window in the `test.py` simulation. |
| 2 | `02-attribute-surface.md` | Every setting (`kp`, `ki`, `setpoint`, `mode`, `fixed_output`) a validating property on one private finite-number helper (`TypeError` / `ValueError` / `OverflowError`, all naming the attribute, previous value kept); the constructor assigns through the setters; `update` validates before storing; a read-only `pi_output`; `OUTPUT_MIN`/`OUTPUT_MAX` re-exported from the package root. |
| 3 | `03-state-snapshot.md` | `to_dict()` (a fresh dict of exactly eight keys: `kp`, `ki`, `setpoint`, `mode`, `history_length`, `fixed_output`, `integral`, `history`) and the `from_dict` classmethod (strict: missing keys reported before unknown ones, every value through the same helpers, no controller on failure); a read-only `history_length` with its own check; `integral` a validating property; `-0.0` normalised to `+0.0`; a finite guard in `update` (a non-finite raw sum or tentative integral closes the demand and holds the integral, while a hold still wins the command). |

This round came from recommendation **R7** of round 1, with **R8**:

> **R7** — Split the actuator mapping (`mode`, the history window, `duty_cycle`,
> `is_history_full`, `fixed_output`, `_to_command`) into a modulator object that
> `PIController` composes and any future model can reuse. Nothing in that code is
> PI-specific, and the second model the repo plans for would otherwise copy the duty-cycle
> modulation and the override or go without them. The override made the seam visible: it
> sits exactly where "demand level" becomes "actuator command".

> **R8** — Loosen the four floor-heating tests that pin the exact firing schedule (which
> slots fire) to the count-and-bound assertions that already prove A3, or relabel them as
> modulator tests. A3 says the duty cycle converges; the schedule is the private
> `duty_cycle < level` rule. Any change to the modulator (hysteresis, minimum on-time)
> breaks tests filed under `fixed_output` that have nothing to do with the override.

The user's decision on R7: "This would make a better 'interface' right?" and "Add R7 to the
list", with R8 "sounds good as well"; both routed to this round with the note that the
modulator's own tests take the schedule.

It also carries items folded in at later gates:

> **Round 2, R8** — State the numeric contract's prose once and have the setters, `update`
> and the STRUCTURE.md rows refer to it; move the helper into a small private module round
> 4's modulator can import. The contract now lives in seven docstrings and four STRUCTURE.md
> rows; round 1's R9 fixed this drift one level up and it has regrown.

> **Round 3, R1** — Constraints for round 4, from all three lenses: the eight-key flat
> snapshot is a frozen wire format, pinned by a test that a literal round-3-shaped dict
> still restores; the history gets one private loader that `to_dict`/`from_dict` use
> instead of reaching into the deque, with the entry rule written there (a level in the
> actuator range, not a command the current mode would emit, so a restored floor history
> may hold fractional entries just as a mid-run mode switch already produces); `mode`
> coercion becomes one helper shared by the setter and `from_dict`; `_level` moves with the
> other helpers; `reset()` is pinned by rule (after `reset()`, `to_dict()` equals a fresh
> controller's with the same settings) rather than by attribute list, since round 4 splits
> it across two objects.

> **Round 3, R3** — Have `update()` and `reset()` write the private integral field directly
> and keep the `integral` setter for external writes. The control law now depends on the
> public validation contract never tightening: the finite guard exists partly so the setter
> cannot raise mid-step.

The user's standing steer, which bounds this round: the repo stays a simple package that
assumes valid inputs arrive; what Home Assistant delivers is Home Assistant's problem.

What is already on the branch that this round must not break:

- Rounds 1 to 3's twenty criteria (A1–A6, B1–B7, C1–C7), all re-checked at this round's
  step 6.
- The 597-test suite. R8 permits reshaping the four schedule-pinning tests into modulator
  tests; nothing else may be weakened.
- The snapshot wire format: the eight keys, flat, and every value's meaning. A dict saved
  before this round must restore after it.
- The public surface of `PIController`: every property, `update`, `reset`, `to_dict`,
  `from_dict`, `history`, `duty_cycle`, `is_history_full`, `pi_output`, `history_length`,
  and the package-root exports, all keep their signatures and behaviour for a caller who
  never touches the modulator directly.
- The `test.py` simulation.

---

## 1. Concept

### What this is

The actuator side of `PIController` becomes its own object, a **modulator**: it takes a
demand level in the actuator range and turns it into a command for its mode, owning the
mode, the window length, the command history, the duty cycle, the window-full flag, the
fixed-output hold, and a reset that clears the window. `PIController` composes one and
keeps every existing property by delegating to it, so a caller who never touches the
modulator sees no change at all. The modulator is a public class importable from the
package root, because reuse by a future model is the point of the split.

The numeric-contract helpers move into one private module that both objects use, with the
contract's prose stated once and every setter referring to it; the mode coercion becomes
one shared helper; the history gets one loader that `to_dict`/`from_dict` use instead of
reaching into the deque. `update` and `reset` write the integral field directly while the
public `integral` setter still validates external writes. The four floor-heating tests
that pin the exact firing schedule move to the modulator's own test file, where the
schedule is the thing under test; the controller's test file keeps the count-and-bound
assertions that prove convergence.

Decisions taken in step 1: the modulator is not reachable from a controller instance and
cannot be injected into the constructor, since both would widen the public surface and
neither is needed for the split. The snapshot format is untouched: the same eight flat
keys, produced by the controller flattening its own state and the modulator's, and pinned
by a test with a literal round-3-shaped dict. `pi_output` stays on the controller, since
it is the PI demand. Where the new class lives, and whether STRUCTURE.md's per-subpackage
split rule then applies, is step 2's call.

The user was told, and accepted, that this round delivers no new behaviour: it is a
structural refactor for a second model that does not exist yet, kept strictly to the
split.

### Why it is worth building

Nothing in the mode mapping, the duty-cycle window or the fixed-output hold is specific to
a PI controller. The second model the repo plans for would otherwise copy them or go
without a price-spike override. The override made the seam visible: it sits exactly where
"demand level" becomes "actuator command". The carried-in constraints are the debt three
rounds of lenses found: the snapshot's two lines that reach into the deque, the mode
message written twice, the numeric contract's prose in seven places, `reset()` enumerated
by hand, and the control law depending on the public `integral` setter never tightening.

### Inputs and outputs

- **Modulator, in:** a mode (enum or its string), a window length, an optional fixed
  level, all under the existing validation contract; then per step a demand level in
  `[OUTPUT_MIN, OUTPUT_MAX]`.
- **Modulator, out:** a command (the level itself for a radiator, `0.0`/`1.0` for floor
  heating), appended to its history; `history`, `duty_cycle`, `is_history_full`,
  `history_length`, `mode`, `fixed_output` readable as today; a reset clearing the window.
- **`PIController`:** every constructor argument, property, method and package export
  keeps its signature, type and behaviour. `to_dict` and `from_dict` keep the eight-key
  flat format.

### How it connects to the rest of the repo

- Changes `src/heatingsystem/pi_controller/pi_controller.py` (the controller delegates),
  adds the modulator's module and the private helpers module (locations chosen in step 2),
  re-exports the modulator from `src/heatingsystem/__init__.py`, adds the modulator's test
  file under `tests/`, moves four tests out of `tests/test_pi_controller.py`, and updates
  `STRUCTURE.md` (following its Growth rule if a subpackage is added) and the README (one
  sentence naming the modulator as reusable).
- `test.py` is untouched.
- A future model composes the modulator the way `PIController` does.

### Explicitly out of scope

- Any new modulation behaviour: hysteresis, minimum on-time, pulse spreading.
- A second model.
- Any change to the snapshot format, to `PIController`'s signatures, or to the PI
  arithmetic and anti-windup.
- Exposing the modulator instance from a controller, or injecting one into it.
- Schema versioning.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| D1 | A modulator class, importable from the package root, maps a demand level to a command with the same results as today: radiator pass-through, floor-heating duty-cycle modulation reading the window before appending, and a fixed level replacing the demand; it owns `mode`, `history_length`, `fixed_output`, `history`, `duty_cycle`, `is_history_full` and a reset, with the same validation contract on its settings. |
| D2 | `PIController`'s public surface keeps every signature and behaviour: the whole pre-round test suite passes unchanged, except the four schedule tests that move. |
| D3 | `to_dict` produces exactly what round 3 produced for the same state, and a literal snapshot written before this round restores identically. |
| D4 | After `reset()`, `to_dict()` equals that of a fresh controller built with the same settings, for a controller that has run and held and for a restored one. |
| D5 | The numeric-contract helpers, the mode coercion and the history loader each exist once, in one private module or one place, used by both the controller and the modulator; the contract's prose is stated once. |
| D6 | `update()` and `reset()` write the integral field directly; the `integral` setter still validates external writes; every twin-integral and hand-computed test passes exactly. |
| D7 | The modulator's test file owns the exact firing-schedule tests; the controller's test file keeps count-and-bound assertions for floor heating; no other existing test is weakened or deleted. |

### Open questions

None.

---

## 2. Plan

### Approach

**Chosen — a `Modulator` class in its own subpackage, a private validation module at the
package root, and a controller that delegates.** `HeatingMode`, `OUTPUT_MIN` and
`OUTPUT_MAX` are actuator concepts, so they move to the modulator's module and the
controller imports them from there; both `__init__.py` re-exports keep working and the
round 2 identity test still holds because the objects are the same. The modulator owns the
window, the mode, the hold and the mapping, and exposes `command(level)`, which maps the
demand (or the fixed level) to a command, appends it, and returns it. `PIController` keeps
every existing property by delegating to its modulator; `update` becomes validate,
compute, record `pi_output`, anti-windup writing `_integral` directly, then
`return self._modulator.command(u)`. The modulator has a *private* four-key `_to_dict` /
`_from_dict` pair with a private history loader, called by the controller within the
package; nothing in section 1 asks for a second public snapshot format, so none is added.
The controller's `to_dict` merges its four keys with the modulator's into the frozen
eight-key order, and its `from_dict` checks the eight keys, builds the modulator from the
four it owns, builds the controller, and installs the modulator. The three numeric helpers move unchanged into `src/heatingsystem/_validation.py`
with plain names, imported as a module (`_validation.finite(...)`) so no parameter or
local can shadow them, and the contract's prose lives in that module's docstring, which
every setter's `Raises:` refers to. The snapshot's mapping check and missing-before-unknown
key check also become one helper there, used by both `from_dict`s. The mode coercion becomes a private function in the
modulator's module, used by its setter and its `from_dict`; the controller never coerces a
mode itself.

**Rejected — the modulator inside the `pi_controller` subpackage**: a second model
importing from `heatingsystem.pi_controller.modulator` says the opposite of what the split
means. **Rejected — the helpers inside the modulator's module**: the controller would then
import validation from the actuator, an odd dependency; a private module at the root is
where shared plumbing belongs. **Rejected — the controller reaching into the modulator's
deque for the snapshot**: that is the debt round 3's lenses named. **Deferred, not
rejected — splitting STRUCTURE.md per subpackage** as its Growth section prescribes: it
changes what the stop gate and the session brief see, which is not a thing to do inside an
unattended build; it is a step 8 recommendation.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/heatingsystem/_validation.py` | new | Private module: the numeric contract stated once, the three helpers `finite`, `window_length`, `level` (bodies moved unchanged from the controller), and `snapshot_mapping` (the mapping check and missing-before-unknown key check, moved from the controller). A `main()` showcase per the Python rules. |
| `src/heatingsystem/modulator/__init__.py` | new | Subpackage entry point: re-exports `Modulator`, `HeatingMode`, `OUTPUT_MIN`, `OUTPUT_MAX`. |
| `src/heatingsystem/modulator/modulator.py` | new | `OUTPUT_MIN`, `OUTPUT_MAX`, `HeatingMode` (moved), the private mode coercion, and the `Modulator` class. A `main()` showcase. |
| `src/heatingsystem/pi_controller/pi_controller.py` | changed | Imports the constants, the enum and the helpers; the controller composes a `Modulator`, delegates six properties, writes `_integral` directly in `update`/`reset`, merges snapshots; `_to_command`, `_finite`, `_window_length`, `_level` and the inline mode coercion removed. |
| `src/heatingsystem/pi_controller/__init__.py` | changed | Same four re-exports, now resolved through the controller module's imports (no textual change may be needed; verify). |
| `src/heatingsystem/__init__.py` | changed | Adds `Modulator` to the imports and `__all__`. |
| `tests/test_modulator.py` | new | The modulator's own suite, including the four exact-schedule tests. |
| `tests/test_pi_controller.py` | changed | The four schedule tests keep only their count-and-bound assertions; new tests for the frozen snapshot, reset-by-rule and the direct integral write. |
| `STRUCTURE.md` | changed | Tree, the new modules' sections, the controller's rows, both `__init__` tables, the test-file paragraphs. |
| `README.md` | changed | One sentence: the modulator is importable and reusable by another model. |

### Public API

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `OUTPUT_MIN: float = 0.0`, `OUTPUT_MAX: float = 1.0`, `HeatingMode(StrEnum)` | `modulator.py` | Moved verbatim from the controller module; the same objects re-exported from `heatingsystem`, `heatingsystem.modulator` and `heatingsystem.pi_controller`. | D1, D2 |
| `Modulator(mode: HeatingMode \| str = HeatingMode.RADIATOR, *, history_length: int = 24, fixed_output: float \| None = None)` | `modulator.py` | The actuator mapping. Every argument goes through its setter or check, with the controller's existing messages. | D1 |
| `Modulator.command(self, level: float) -> float` | `modulator.py` | Map a demand level to this step's command (the fixed level replaces `level` when set; radiator pass-through; floor-heating duty-cycle modulation reading the window before appending), append it to the history, return it. `level` is validated with `level("level", value)` from the helpers. | D1 |
| `Modulator.reset(self) -> None` | `modulator.py` | Clear the history; settings and the hold unchanged. | D1, D4 |
| `Modulator.mode -> HeatingMode` / setter `(value: HeatingMode \| str) -> None` | `modulator.py` | As the controller's today, same message. | D1 |
| `Modulator.history_length -> int` (read-only) | `modulator.py` | The window length. | D1 |
| `Modulator.fixed_output -> float \| None` / setter `(value: float \| None) -> None` | `modulator.py` | As the controller's today, same messages. | D1 |
| `Modulator.history -> tuple[float, ...]`, `Modulator.duty_cycle -> float`, `Modulator.is_history_full -> bool` | `modulator.py` | As the controller's today. | D1 |
| `main() -> None` in `modulator.py` | `modulator.py` | Showcase: a radiator pass-through, a floor-heating run to a converged duty cycle, a hold, a reset. | — (showcase) |
| `PIController(kp: float = 0.3, ki: float = 0.015, mode: HeatingMode \| str = HeatingMode.RADIATOR, setpoint: float = 21.0, *, history_length: int = 24, fixed_output: float \| None = None)` | `pi_controller.py` | Unchanged. Builds `Modulator(mode, history_length=history_length)` first, then assigns `kp`, `ki`, `setpoint` through their setters, initialises `_integral` and `_pi_output` directly, and assigns `fixed_output` last through its setter, so today's validation order (mode, history_length, kp, ki, setpoint, fixed_output) is kept. | D2 |
| `PIController.mode`, `.fixed_output` (settable), `.history_length`, `.history`, `.duty_cycle`, `.is_history_full` | `pi_controller.py` | Unchanged signatures; each delegates to the modulator. | D2 |
| `PIController.update(measured: float, setpoint: float \| None = None) -> float` | `pi_controller.py` | Unchanged signature and results. The new integral (or the held one) and `u` are computed into locals, `command = self._modulator.command(u)` is called, and only then are `self._integral` and `self._pi_output` assigned, so a raise from the modulator leaves the controller's state untouched (B4 by construction). | D2, D6 |
| `PIController.reset() -> None` | `pi_controller.py` | Unchanged signature; writes `self._integral = 0.0`, `self._pi_output = None`, calls `self._modulator.reset()`. | D4, D6 |
| `PIController.to_dict(self) -> dict[str, object]` / `PIController.from_dict(cls, data: Mapping[str, object]) -> Self` | `pi_controller.py` | Unchanged signatures and format: the eight keys in the round 3 order `kp, ki, setpoint, mode, history_length, fixed_output, integral, history`. | D3 |
| `PIController.kp`, `.ki`, `.setpoint`, `.integral` (settable), `.pi_output` | `pi_controller.py` | Unchanged; the setters now call the helpers from `_validation`. | D2, D6 |
| `main() -> None` in `_validation.py` | `_validation.py` | Showcase: one accepted value and one refusal per helper. | — (showcase) |
| `Modulator` re-exported from `heatingsystem` | `__init__.py` | Importable from the package root. | D1 |

No signature of `PIController` changes, and no public name is removed from any module a
caller imports today.

### Implementation guide

1. Create `src/heatingsystem/_validation.py`. Module docstring states the numeric contract
   once (real number, `bool` excluded; finite; representable as a float; `TypeError` /
   `ValueError` / `OverflowError` naming the attribute; a negative zero returned positive;
   the window length an `int` of at least 1 and at most `sys.maxsize`; a level also inside
   `[OUTPUT_MIN, OUTPUT_MAX]`). Move `_finite`, `_window_length`, `_level` in as `finite`,
   `window_length`, `level`, bodies and messages unchanged; each docstring's `Raises:`
   stays (it is the one place). `level` needs the range bounds: take them as parameters
   `level(name: str, value: object, lower: float, upper: float) -> float` so this module
   does not import from the modulator (no cycle); the callers pass `OUTPUT_MIN`,
   `OUTPUT_MAX`; its local variable is named `number`, not `level`. Add
   `snapshot_mapping(data: object, keys: frozenset[str]) -> Mapping[str, object]`: raises
   `TypeError(f"snapshot must be a mapping, got {type(data).__name__}.")` for a
   non-mapping, then `ValueError(f"snapshot is missing keys {missing}.")` (sorted) before
   `ValueError(f"snapshot has unknown keys {unknown}.")` (sorted with `key=repr`), and
   returns `data`; both `from_dict`s call it. Add `main()` in showcase form.
   `import math, numbers, sys` and `from collections.abc import Mapping`.
2. Create `src/heatingsystem/modulator/__init__.py` re-exporting `Modulator`,
   `HeatingMode`, `OUTPUT_MIN`, `OUTPUT_MAX` with `__all__`.
3. Create `src/heatingsystem/modulator/modulator.py`: move `OUTPUT_MIN`, `OUTPUT_MAX`,
   `HeatingMode` verbatim (docstrings included). Add a private `_heating_mode(value: object) -> HeatingMode`
   with the controller's current setter body and message. Define `Modulator` with
   class-level annotations `_mode: HeatingMode`, `_history_length: int`,
   `_fixed_output: float | None`, `_history: deque[float]`; the constructor assigns
   `self.mode = mode`, `self._history_length = window_length(history_length)`, the deque,
   `self._fixed_output = None`, then `self.fixed_output = fixed_output`. Import the
   helpers as a module: `from heatingsystem import _validation` and call
   `_validation.finite(...)`, `_validation.window_length(...)`, `_validation.level(...)`,
   so the `command(self, level: float)` parameter cannot shadow anything. Properties as
   listed, bodies moved from the controller. `command(level)`:
   `demand = _validation.level("level", level, OUTPUT_MIN, OUTPUT_MAX)`,
   `target = demand if self._fixed_output is None else self._fixed_output`, then the
   current `_to_command` body on `target`, append, return. `reset()` clears the deque. A
   private `_load_history(self, entries: object) -> None` holding today's shape check,
   length check and per-entry `_validation.level(f"history[{i}]", ...)` loop with the same
   messages, extending only after the loop; its docstring states the entry rule from round
   3's R1: an entry is a level in the actuator range, not a command the current mode would
   emit, so a restored floor history may hold fractional entries just as a mid-run mode
   switch produces. Private `_to_dict(self) -> dict[str, object]` (keys `mode`,
   `history_length`, `fixed_output`, `history` in that order, a fresh list) and
   `_from_dict(cls, data: Mapping[str, object]) -> Self` (classmethod: `_validation.snapshot_mapping(data, _KEYS)`,
   then `cls(mode=..., history_length=..., fixed_output=...)` with `fixed_output` narrowed
   as today (`None` or `_validation.level("fixed_output", ...)`), then `_load_history`).
   `main()` in showcase form.
4. Rewrite `src/heatingsystem/pi_controller/pi_controller.py`: imports become
   `from heatingsystem import _validation` and
   `from heatingsystem.modulator.modulator import OUTPUT_MAX, OUTPUT_MIN, HeatingMode, Modulator`;
   delete `_finite`, `_window_length`, `_level`, the enum, the constants, `_to_command`
   and the inline mode coercion. Class-level annotations become `_kp`, `_ki`,
   `_setpoint`, `_integral`, `_pi_output`, `_modulator: Modulator`. Constructor, in
   today's validation order: `self._modulator = Modulator(mode, history_length=history_length)`,
   then `self.kp = kp`, `self.ki = ki`, `self.setpoint = setpoint`, `self._integral = 0.0`,
   `self._pi_output = None`, and last `self.fixed_output = fixed_output` (delegating
   setter). Delegating properties: `mode` (getter and setter), `fixed_output` (both),
   `history_length`, `history`, `duty_cycle`, `is_history_full`. `update`: rename the
   local boolean `finite` to `is_finite`; validation and arithmetic unchanged up to the
   clamp; compute the committed integral into a local (`next_integral = new_integral` in
   the three anti-windup branches, else `self._integral`), then
   `command = self._modulator.command(u)`, then `self._integral = next_integral`,
   `self._pi_output = u`, `return command`. `reset` as in the table. `to_dict`: an
   explicit literal in the eight-key order, taking the four modulator values from
   `self._modulator._to_dict()`. `from_dict`: `data = _validation.snapshot_mapping(data, _SNAPSHOT_KEYS)`,
   then `modulator = Modulator._from_dict({k: data[k] for k in ("mode", "history_length", "fixed_output", "history")})`,
   then `controller = cls(kp=_validation.finite("kp", data["kp"]), ki=..., setpoint=..., mode=modulator.mode, history_length=modulator.history_length, fixed_output=modulator.fixed_output)`,
   `controller.integral = _validation.finite("integral", data["integral"])`,
   `controller._modulator = modulator`, return. Docstrings: every setter's `Raises:`
   becomes one line referring to the contract in `heatingsystem._validation`; the class
   docstring names the modulator in one sentence.
5. `src/heatingsystem/__init__.py`: add `Modulator` (import from `heatingsystem.modulator`),
   `__all__` in alphabetical order.
6. `tests/test_modulator.py`: the four schedule tests rewritten against
   `Modulator.command` with their exact-slot assertions, plus the modulator's own
   construction, validation, mapping, hold, reset and private-snapshot tests (step 5
   designs them). In `tests/test_pi_controller.py` the four originals change exactly like
   this and nothing more: `test_fixed_output_floor_quarter_level_converges_to_exact_fraction`
   drops the `on_steps == [...]` assertion and keeps the rest;
   `test_fixed_output_floor_history_length_one_alternates` replaces its list equality with
   `set(outs) <= {0.0, 1.0}` and `outs.count(1.0) == 3` (a one-slot window alternates at
   any level, so no convergence assertion applies);
   `test_fixed_output_floor_level_just_above_min_fires_once_per_window` drops
   `outs[0] == 1.0` and keeps the count and `duty_cycle` assertions;
   `test_fixed_output_floor_level_just_below_max_rests_once_per_window` drops
   `outs[1] == 0.0` and keeps the count and `duty_cycle` assertions. Round 3's
   snapshot-twin tests that pin an exact floor history stay unchanged: they pin restore
   equality, not the schedule. Nothing else changes except additions.
7. `STRUCTURE.md`: tree gains `modulator/` and `_validation.py`; new sections headed by
   the literal repo-relative paths `src/heatingsystem/_validation.py`,
   `src/heatingsystem/modulator/__init__.py`, `src/heatingsystem/modulator/modulator.py`
   and `tests/test_modulator.py` (the stop gate matches those strings, so tree entries
   alone do not satisfy it; the private module gets a table because steps 4 and 6 audit
   against it); the controller section loses the constants and enum rows and gains the
   delegation note; both `__init__` tables updated; the test paragraphs. Note in the
   Growth paragraph that the per-subpackage split is pending.
8. README: one sentence after the usage block.
9. Run `ruff check .`, `ruff format --check .`, `mypy`, `pytest`,
   `python -m heatingsystem.pi_controller.pi_controller`, `python -m heatingsystem.modulator.modulator`,
   `python -m heatingsystem._validation`, and `MPLBACKEND=Agg python test.py`.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | `Modulator` alone: construction defaults and validation errors identical in class and message to the controller's for `mode`, `history_length`, `fixed_output`; `command` returns the level in radiator mode and only `0.0`/`1.0` in floor mode, reads the window before appending, converges to a fractional level, and returns the fixed level (or its floor modulation) when a hold is set; `command` rejects a non-finite or out-of-range level (`ValueError`), a `bool` or `str` (`TypeError`) and a huge `int` (`OverflowError`), each naming `level`; `reset` clears the history and keeps settings and hold; the four exact-schedule tests moved from the controller file. | D1, D7 |
| T2 | The modulator's private snapshot pair has exactly its four keys in order and is a copy; it round-trips, refuses a non-mapping, reports missing before unknown, and raises the controller's exact history messages (tested through the controller's public `to_dict`/`from_dict` where possible, and directly where a modulator-only case needs it). | D3 |
| T3 | `hs.Modulator`, `hs.modulator.Modulator` and `hs.modulator.modulator.Modulator` are the same class; `hs.HeatingMode`, `hs.OUTPUT_MIN`, `hs.OUTPUT_MAX` are the same objects from `heatingsystem`, `heatingsystem.modulator` and `heatingsystem.pi_controller`; every name is in the relevant `__all__`. | D1, D2 |
| T4 | The frozen format, in both directions, against literals worked out from the round 3 code and written into this plan: a radiator controller with defaults and `fixed_output=0.2` after two `update(20.5)` calls has `to_dict() == {"kp": 0.3, "ki": 0.015, "setpoint": 21.0, "mode": "radiator", "history_length": 24, "fixed_output": 0.2, "integral": 1.0, "history": [0.2, 0.2]}` with `list(d)` in that key order; and a floor-heating literal with a fractional history entry (`{"kp": 0.3, "ki": 0.015, "setpoint": 21.0, "mode": "floor_heating", "history_length": 4, "fixed_output": null, "integral": 0.5, "history": [0.5, 1.0]}` as JSON) restores and re-serialises to itself. | D3 |
| T5 | After `reset()`, `to_dict()` equals `PIController(kp, ki, mode, setpoint, history_length=..., fixed_output=...).to_dict()` for a controller that has run and held, and for a restored controller. | D4 |
| T6 | The controller and modulator modules no longer define `_finite`, `_level`, `_window_length` or `_to_command` (`hasattr` is false), the helpers exist in `heatingsystem._validation`, `_heating_mode` exists once in the modulator module, and no message changed: the controller's `from_dict` error messages for every key equal the setter's, as round 3's test already asserts. | D5 |
| T7 | The `integral` setter still rejects bad values naming `integral`; `update` and `reset` still produce exactly the same integral trajectory as before (the round 1 twin tests and round 2 hand computations, unchanged); and a subclass whose `integral` setter always raises still runs `update` and `reset` without raising, proving the direct write. | D6 |
| T8 | Every pre-round test passes unchanged except the four named in the guide, whose remaining assertions still pass. | D2, D7 |

### Risks

- **Import cycles.** `_validation` imports nothing from the package; `modulator` imports
  `_validation`; `pi_controller` imports both. Keep that order. If a cycle appears, the
  fix is to move whatever crossed it, not to import lazily. Not a halt.
- **Constants identity.** The round 2 test asserts the objects are identical across the
  three import paths; re-exporting the same objects satisfies it. If it fails, something
  redefined a constant: fix. Not a halt.
- **A moved test that cannot be split.** If one of the four schedule tests has no
  assertion left once the exact-slot one moves, keep the convergence assertion from the
  neighbouring test in it rather than deleting it; nothing is deleted. Not a halt.
- **The showcase for `_validation`.** The Python rules require a `main()` on every module;
  a private module is not exempt. Showcase form, three cases.
- **STRUCTURE.md and the stop gate.** Every new `.py` file must be named in STRUCTURE.md or
  the gate blocks at step 8; the private module too.
- **Order of reported faults in `from_dict`.** With several bad values in one snapshot,
  the plan checks the four modulator keys before `kp`, `ki`, `setpoint` and `integral`,
  where round 3 checked mode, fixed_output, history shape, then the numbers. Which fault
  is reported first is documented nowhere and deliberately untested; the change is
  accepted. The constructor's order is kept exactly.
- **Halting line.** Any change to a `PIController` signature, to the eight-key format, or
  to the PI arithmetic would amend section 1: **halt**.

### Critique

Findings from the `plan-critic` read, verdict *accept with changes*; all ten applied.

1. **Moving the helpers in with plain names would crash `update` (a local named `finite`
   shadows the helper) and `command` (its parameter shadows `level`).** Applied: both
   modules import `_validation` as a module; the local is renamed `is_finite`; the helper's
   own local is `number`.
2. **The constructor row said `integral` goes through its setter, which T7's subclass
   could not survive.** Applied: the row and the guide initialise `_integral` directly.
3. **T4 was circular: the literal would have been computed with the new code.** Applied:
   the two literals are worked out from the round 3 code and written into T4, tested in
   both directions.
4. **The constructor's validation order changed.** Applied: the modulator is built without
   the hold and `fixed_output` is assigned last, keeping today's order; the `from_dict`
   order change is recorded in Risks as accepted.
5. **One schedule test had nothing left once its exact assertion moved, and D7 could be
   read against round 3's exact-history tests.** Applied: guide step 6 states each of the
   four edits exactly and says the round 3 tests stay.
6. **A public four-key snapshot on the modulator is a second format nobody asked for.**
   Applied on the side of simplicity: the pair is private, called within the package;
   stated to the user at the gate, who can make it public later.
7. **The mapping and key checks would have been copied.** Applied: `snapshot_mapping` in
   the private module, used by both.
8. **`update` wrote state before calling the validating `command`.** Applied: locals first,
   `command`, then the writes.
9. **T6 asserted something false and T1 missed two error classes.** Applied.
10. **The showcase row's name, the stop gate's literal-path matching, and the entry rule
    in `_load_history`'s docstring.** Applied.


---

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry.

Built as planned, with one addition mypy required: `_heating_mode`'s call to `HeatingMode(value)`
takes `value: object` per the guide's signature, but `HeatingMode.__call__`'s stub expects a
narrower type, so the call carries `# type: ignore[arg-type]` with a reason (the same shape
the conventions already use elsewhere in the package) — `HeatingMode()` itself validates any
object at runtime and raises `ValueError` for anything invalid, so no behaviour changed.

`tests/` was not touched in this step, per the pipeline's separation of production code
(step 3) from the test suite (step 5): the four schedule tests still live in
`tests/test_pi_controller.py` unchanged, and `tests/test_modulator.py` does not exist yet.
`STRUCTURE.md`'s Tests section is correspondingly left as it was; its test-file paragraphs
and the new `tests/test_modulator.py` section are step 5's to write, in the same commit
that creates the file.

All 597 pre-existing tests pass unchanged against the delegating controller, confirming the
split changed no behaviour: `ruff check .`, `ruff format --check .` and `mypy` are clean;
`python -m heatingsystem.pi_controller.pi_controller`, `python -m
heatingsystem.modulator.modulator` and `python -m heatingsystem._validation` all run
standalone; `MPLBACKEND=Agg python test.py` still produces `simulation.png`.

---

## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `44 files already formatted` |
| `mypy` | `Success: no issues found in 16 source files` |
| `pytest` | `597 passed in 6.38s` — the whole suite, unchanged by this step |
| Plan completeness | every signature in the Public API table exists as written (table below) |
| `STRUCTURE.md` | in sync — see the structure audit below |
| `python -m <package>.<module>` | all three ran clean (output below) |

### Plan completeness — Public API table, row by row

Read against `src/heatingsystem/modulator/modulator.py`, `src/heatingsystem/_validation.py`
and `src/heatingsystem/pi_controller/pi_controller.py`, character by character.

| Row | Result |
|---|---|
| `OUTPUT_MIN`, `OUTPUT_MAX`, `HeatingMode(StrEnum)` in `modulator.py` | Match — moved verbatim, docstrings included; the same objects re-exported from `heatingsystem`, `heatingsystem.modulator` and `heatingsystem.pi_controller` (confirmed with `is`). |
| `Modulator(mode: HeatingMode \| str = HeatingMode.RADIATOR, *, history_length: int = 24, fixed_output: float \| None = None)` | Match. |
| `Modulator.command(self, level: float) -> float` | Match — reads the window before appending, applies the hold, validates via `_validation.level("level", level, OUTPUT_MIN, OUTPUT_MAX)`. |
| `Modulator.reset(self) -> None` | Match — clears the deque only. |
| `Modulator.mode` getter/setter | Match — setter delegates to the shared `_heating_mode` coercion. |
| `Modulator.history_length -> int` (read-only) | Match — no setter defined. |
| `Modulator.fixed_output` getter/setter | Match. |
| `Modulator.history`, `.duty_cycle`, `.is_history_full` | Match, bodies moved unchanged. |
| `main() -> None` in `modulator.py` | Match — radiator pass-through, floor-heating convergence, a `fixed_output` hold, a reset; showcase form (inputs/call/output, named variables, fixed-set comment on `mode`). |
| `PIController(kp, ki, mode, setpoint, *, history_length, fixed_output)` | Match — unchanged signature; builds the `Modulator` first, then `kp`/`ki`/`setpoint`, then `_integral`/`_pi_output` direct, `fixed_output` last — today's validation order preserved. |
| `PIController.mode`, `.fixed_output`, `.history_length`, `.history`, `.duty_cycle`, `.is_history_full` | Match — each delegates to `self._modulator`. |
| `PIController.update(measured, setpoint=None) -> float` | Match — unchanged signature; locals computed first (`next_integral`, `u`), then `command = self._modulator.command(u)`, then the two writes, so a raise from the modulator leaves `integral`/`pi_output` untouched (B4 by construction). |
| `PIController.reset() -> None` | Match — unchanged signature and effect. Statement order in the body is `_integral = 0.0`, `_modulator.reset()`, `_pi_output = None`, a harmless reordering against the plan's prose order (`_integral`, `_pi_output`, `_modulator.reset()`); the three writes are independent of each other so behaviour is identical. Noted, not a deviation worth a plan edit. |
| `PIController.to_dict()` / `from_dict()` | Match — eight keys in the frozen order `kp, ki, setpoint, mode, history_length, fixed_output, integral, history`; `to_dict` merges the controller's four with `self._modulator._to_dict()`; `from_dict` builds the modulator from its four keys first, then the controller. |
| `PIController.kp`, `.ki`, `.setpoint`, `.integral`, `.pi_output` | Match — setters call `_validation.finite(...)`. |
| `main() -> None` in `_validation.py` | Match, present; showcase form. Its Purpose column says "one accepted value and one refusal per helper", but the Risks section explicitly settles this at three cases (`finite` accepted, `level` refused, `snapshot_mapping` refused) rather than one pair per each of the four helpers — `window_length` is not separately showcased. Read as the Risks section governing over the Purpose column's looser phrasing; `.claude/rules/python.md` asks for "two or three cases" per module, not exhaustive per-function coverage, and three cases is what is built. Not a mismatch; flagged here as a pre-existing wording looseness in the plan itself, nothing to fix in code. |
| `Modulator` re-exported from `heatingsystem` | Match — `heatingsystem/__init__.py` imports it and lists it in `__all__`, alphabetical. |

No **Missing**, no **Deviation** needing a plan edit, no **Unplanned** public surface: every private helper
(`_heating_mode`, `_to_command`, `_load_history`, `_to_dict`, `_from_dict`) is `_`-prefixed and correctly left
out of `STRUCTURE.md`. The `# type: ignore[arg-type]` on `_heating_mode`'s `HeatingMode(value)` call and on
`Modulator._from_dict`'s `mode=data["mode"]` both carry the narrowing code plus a same-line reason, as
`.claude/rules/python.md` requires; judged compliant, nothing to fix.

### Structure audit (by hand — subagents cannot spawn subagents here)

- Tree: `modulator/` and `pi_controller/` both listed under `src/heatingsystem/`, matching disk.
- New sections present, each headed by its literal repo-relative path so the stop gate's substring match
  finds it: `src/heatingsystem/_validation.py`, `src/heatingsystem/modulator/__init__.py`,
  `src/heatingsystem/modulator/modulator.py` — all three exist verbatim as section headers.
- The controller's section is rewritten: constants/enum rows removed, delegation noted in the prose and in
  each delegating property's row, `update`/`reset`/`to_dict`/`from_dict` rows describe the modulator split.
- All three `__init__` export tables present and correct: package root (`HeatingMode`, `Modulator`,
  `PIController`, `OUTPUT_MIN`, `OUTPUT_MAX`), `modulator/__init__.py` and `pi_controller/__init__.py`
  prose both match their actual re-exports.
- No private name (`_heating_mode`, `_to_command`, `_load_history`, `_to_dict`, `_from_dict`, `_finite`,
  `_window_length`, `_level` — the last three no longer exist anywhere) appears in any signature table.
- `tests/test_modulator.py` is correctly **absent** from both disk and `STRUCTURE.md`'s Tests section —
  the plan's own step 3 implementation notes defer it to step 5, and `structure_problems()` confirms no
  drift (see below).
- Direct stop-gate cross-check: `structure_problems(Path("."))`, `missing_init_files(Path("."))` and
  `stray_test_files(Path("."))` each returned `[]`.
- README: one sentence added after the usage block naming `hs.Modulator` as reusable by a future model.

### Module showcases

```
$ python -m heatingsystem.pi_controller.pi_controller
<frozen runpy>:128: RuntimeWarning: 'heatingsystem.pi_controller.pi_controller' found in
sys.modules after import of package 'heatingsystem.pi_controller', but prior to execution
of 'heatingsystem.pi_controller.pi_controller'; this may result in unpredictable behaviour
=== HeatingMode enum ===
  HeatingMode.RADIATOR = 'radiator'
  HeatingMode.FLOOR_HEATING = 'floor_heating'
=== RADIATOR mode (5-step warm-up) ===
  ... (unchanged from round 3) ...
=== State snapshot and restore ===
  snapshot JSON          : {"kp": 0.3, "ki": 0.015, "setpoint": 21.0, "mode": "radiator",
  "history_length": 24, "fixed_output": 0.2, "integral": 6.0, "history": [0.2, 0.2]}
  restored fixed_output  : 0.2
  next command (saved)   : 0.2000
  next command (restored): 0.2000
  Bad snapshot            -> ValueError: snapshot is missing keys [...]
All demonstrations completed successfully.
Exit code: 0 (RuntimeWarning expected per .claude/rules/python.md)

$ python -m heatingsystem.modulator.modulator
<frozen runpy>:128: RuntimeWarning: ... (same expected warning, re-exported submodule)
=== HeatingMode enum ===
  HeatingMode.RADIATOR = 'radiator'
  HeatingMode.FLOOR_HEATING = 'floor_heating'
=== RADIATOR mode: pass-through ===
  command(0.42) = 0.42  history=(0.42,)
=== FLOOR_HEATING mode: duty-cycle convergence ===
  demand=0.25  duty_cycle=0.250  history=(0.0, 0.0, 1.0, 0.0)
=== fixed_output override ===
  fixed_output=1.0  command(0.25) = 1.0
  released and reset: fixed_output=None  history=()
Exit code: 0

$ python -m heatingsystem._validation
<frozen runpy>:128: RuntimeWarning: ... (same expected warning)
finite('kp', -0.0) = 0.0
level('fixed_output', 1.5, 0.0, 1.0) -> ValueError: fixed_output must be in [0.0, 1.0], got 1.5.
snapshot_mapping({'kp': 0.3}, frozenset({'ki', 'kp'})) -> ValueError: snapshot is missing keys ['ki'].
Exit code: 0
```

Also run, though outside this step's required checklist, as a belt-and-suspenders check that the untouched
`test.py` still works against the delegating controller: `MPLBACKEND=Agg python test.py` printed
`Saved figure to simulation.png` and the file was written, exit code 0.

### Nothing fixed here

No mismatch, no ruff/mypy/pytest failure, no showcase defect. Nothing needed a code change at this step;
the log above records what was checked and confirms each check's result.

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

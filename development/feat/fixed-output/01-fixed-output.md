# Fixed output override

<!-- claude-plan step=7 status=active -->


| Field | Value |
|---|---|
| Feature | `feat/fixed-output` |
| Round | `1` |
| Branch | `claude/festive-maxwell-xft6pj` |
| Started | `2026-09-22` |

The branch is the one this hosted session was told to push to; the folder keeps the
conventional name `feat/fixed-output`.

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
| 2 | Plan | `/plan` | with the user | done |
| 3 | Implement | `/implement` | in `/build` | done |
| 4 | Verify | `/verify` | in `/build` | done |
| 5 | Test | `/test` | in `/build` | done |
| 6 | Concept check | `/concept-check` | in `/build` | done |
| 7 | Ship | `/ship` | in `/build` | in progress |
| 8 | Recommend | `/recommend` | with the user | pending |
| 9 | Pull request | `/create-pr` | with the user | pending |
| 10 | Review | `/watch-pr` | on the pull request | pending |

Statuses: `pending`, `in progress`, `done`.

## Builds on

Nothing — this is the first round.

---

## 1. Concept

### What this is

`PIController` gains an optional **fixed output level**: a float in the actuator range
`[0, 1]`, or none. While it is set, every `update` call issues that level instead of the PI
result. Radiators receive the level directly; floor heating modulates it through the
existing duty-cycle mechanism, so its long-run on-fraction converges to the level. The
fixed command is appended to the history like any other command, so the history and duty
cycle always describe what the actuator was actually told to do.

The level can be given at construction or set and cleared afterwards, and read back.
Clearing it hands control back to the PI loop on the next call. It is persistent
configuration on the controller, like the setpoint and gains — set once when a price spike
starts, cleared when it ends — not something passed on every call.

While fixed, the PI calculation still runs in full: inputs are validated, a passed setpoint
is stored, and the integral advances (with the existing anti-windup, keyed on the raw PI
output) exactly as it would unfixed. Only the command that leaves the controller differs.
The user chose this over freezing the integral: when the override lifts, the controller
already reflects the error that built up during it.

### Why it is worth building

Short periods of extreme heating prices, where the owner wants the valve held at a chosen
position — typically low or closed — rather than whatever the controller would demand. The
controller is driven from Home Assistant / AppDaemon, so the automation that knows the
price is the one that sets and clears the override.

### Inputs and outputs

- **In:** a float in `[0.0, 1.0]` to fix the output, or none to release it. Accepted at
  construction and as a settable attribute afterwards. A non-finite value or one outside
  the range raises `ValueError` and leaves the previous setting untouched.
- **Out:** `update` keeps its signature and return type. Radiator mode returns the fixed
  level itself; floor-heating mode returns `0.0` or `1.0` from duty-cycle modulation at
  that level. The current override is readable back from the controller.

### How it connects to the rest of the repo

- Changes `src/heatingsystem/pi_controller/pi_controller.py` (`PIController`, its
  showcase `main`) and `tests/test_pi_controller.py`, plus `STRUCTURE.md` and three or
  four lines in `README.md`'s usage section, which documents every other public member.
- Does not touch `src/heatingsystem/__init__.py` or the subpackage `__init__.py`: no new
  public names at package level.
- Does not touch `test.py` (the simulation) — it may be extended in a later round to show
  an override period, but that is not part of this one.
- Shares no data with anything else; the override lives on the controller instance.

### Explicitly out of scope

- Any timing, scheduling or automatic release of the override. The caller decides when it
  starts and ends.
- Price awareness of any kind.
- Any change to how the integral or anti-windup work.
- A separate "hold at the last computed value" mode; the level is always given explicitly.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | A controller with no fixed output produces, for any sequence of updates, the same commands, integral and history as before this change. |
| A2 | With a fixed level set, at construction or afterwards, `update` in radiator mode returns exactly that level whatever the measurement and setpoint, appends it to the history, and the level is readable back. |
| A3 | With a fixed level strictly between 0 and 1 in floor-heating mode, `update` returns only `0.0` or `1.0`, and the duty cycle over a full window converges to the fixed level. |
| A4 | While fixed, each `update` still validates its inputs, stores a passed setpoint, and advances the integral exactly as an unfixed controller fed the same measurements would. |
| A5 | Setting a level that is non-finite or outside `[0, 1]` raises `ValueError`, at construction and afterwards, and the previous setting is unchanged. |
| A6 | Clearing the override makes the next `update` return the PI result again, and `reset()` does not clear the override. |

### Open questions

None. Two forks were put to the user in step 1 and decided: the integral keeps advancing
while fixed (not frozen, not zeroed on release), and in floor-heating mode the fixed level
is a demand that is duty-cycle modulated (not a literal command).

---

## 2. Plan

### Approach

**Chosen — a validating property plus a constructor keyword.** `PIController` gets a
`fixed_output` property whose setter accepts a float in `[OUTPUT_MIN, OUTPUT_MAX]` or
`None`, validates it, and stores it privately; the constructor takes the same value as a
keyword-only argument and assigns through the setter so validation lives in one place. In
`update`, the PI calculation, clamp and anti-windup run untouched; the only change is that
the level handed to `_to_command` is the fixed output when one is set, and the PI output
`u` otherwise. Because `_to_command` already does radiator pass-through and floor-heating
duty-cycle modulation, both modes get the agreed semantics without new mapping code, and
the existing append-after-mapping order records the fixed command in the history.

**Rejected — a pair of methods** (`fix_output(level)` / `release_output()`) reads well at
the call site but adds two public names where one suffices, and still needs a property to
read the level back; three names for one piece of state. **Rejected — a plain public
attribute** like `setpoint`: it cannot raise on an out-of-range assignment, so A5 would
only hold at construction, and an automation that writes `1.5` would silently command an
impossible position. The property costs one more method than the attribute and buys A5 on
both paths.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/heatingsystem/pi_controller/pi_controller.py` | changed | `fixed_output` property and setter, the constructor keyword, the substitution in `update`, docstrings, and a showcase case in `main()` |
| `tests/test_pi_controller.py` | changed | Tests for T1–T6 below, in a new section of the existing file |
| `STRUCTURE.md` | changed | The `PIController` constructor row and a new `fixed_output` row; the test-file description |
| `README.md` | changed | Three or four lines in the usage section showing the override being set and cleared |

No new module: the override is one piece of state on the controller and belongs beside
`setpoint` and `integral`.

### Public API

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `PIController(kp: float = 0.3, ki: float = 0.015, mode: HeatingMode \| str = HeatingMode.RADIATOR, setpoint: float = 21.0, *, history_length: int = 24, fixed_output: float \| None = None)` | `pi_controller.py` | Existing constructor with one new keyword-only argument, defaulting to `None` (no override). Assigns through the `fixed_output` setter, so a bad value raises `ValueError` here too. | A1, A2, A5 |
| `PIController.fixed_output -> float \| None` | `pi_controller.py` | Property: the current override, or `None` when the PI loop is in control. | A2, A6 |
| `PIController.fixed_output` setter: `(value: float \| None) -> None` | `pi_controller.py` | Set or clear the override. Raises `ValueError` if `value` is not `None` and is non-finite or outside `[OUTPUT_MIN, OUTPUT_MAX]`; the stored value is untouched on failure. A number in range is stored as `float(value)`; a non-numeric value raises `TypeError`, unguarded. | A2, A5, A6 |
| `PIController.update(measured: float, setpoint: float \| None = None) -> float` | `pi_controller.py` | Unchanged signature. While `fixed_output` is set, the level passed to the mode mapping is the fixed output rather than the PI result; everything before that point runs as before. | A2, A3, A4, A6 |
| `PIController.reset() -> None` | `pi_controller.py` | Unchanged signature and behaviour: clears integral and history, leaves `fixed_output` alone (documented). | A6 |
| `main() -> None` | `pi_controller.py` | Showcase gains one case: a radiator held at a fixed level for a few steps, released, and the history printed. | — (showcase, required by the Python rules) |

The existing `HeatingMode`, `history`, `duty_cycle`, `is_history_full`, `OUTPUT_MIN` and
`OUTPUT_MAX` are unchanged and not restated.

### Implementation guide

1. In `PIController.__init__`, add `fixed_output: float | None = None` after
   `history_length` in the keyword-only group. Extend the `Args:` and `Raises:` sections of
   both the class docstring and the constructor docstring.
2. Add the private attribute `self._fixed_output: float | None = None` next to `integral`
   and `_history`, then assign `self.fixed_output = fixed_output` as the last statement of
   the constructor so the setter validates it.
3. Add the `fixed_output` property under the *Properties* section, after
   `is_history_full`, with a getter that returns `self._fixed_output` and a setter that:
   - accepts `None` and stores it;
   - otherwise raises `ValueError(f"fixed_output must be a finite number in "
     f"[{OUTPUT_MIN}, {OUTPUT_MAX}] or None, got {value!r}.")` when `value` is not finite
     or is outside the closed range, checking `math.isfinite` first so `nan` does not slip
     through a comparison;
   - stores `float(value)` only after both checks pass, so an integer `0` or `1` from an
     automation is stored and later returned by `update` as a float, keeping `update`'s
     `-> float` and the history's element type honest.
   A non-numeric value raises `TypeError` from `math.isfinite`, deliberately unguarded and
   consistent with how `kp`, `ki` and `setpoint` are validated today; it is not converted
   to `ValueError`. Google docstrings on both, with `Raises:` on the setter.
4. In `update`, after the anti-windup block and before the `_to_command` call, replace
   `command = self._to_command(u)` with a two-line substitution: `level = u if
   self._fixed_output is None else self._fixed_output`, then `command =
   self._to_command(level)`. Do not touch the PI computation, clamp, anti-windup or the
   history append. Update the `update` docstring: one paragraph saying that while
   `fixed_output` is set the returned command is derived from it instead of the PI result,
   the integral still advances, and the command is still recorded. Rename `_to_command`'s
   parameter from `u` to `level` and reword its docstring, and `HeatingMode`'s, to say
   "the demand level (the PI output, or the fixed output when one is set)"; add one line
   to the module docstring noting the override. `_to_command` is private, so no
   `STRUCTURE.md` row changes for it.
5. In `reset`'s docstring, add `fixed_output` to the list of things left unchanged.
6. In `main()`, after the setpoint-override case and before the `reset()` demonstration,
   add a case in the showcase form: bind `fixed_level = 0.2`, assign it to
   `ctrl_rad.fixed_output`, run three `update` calls at a cold measurement printing the
   command and integral each step, print `ctrl_rad.fixed_output`, set it back to `None`,
   run one more `update` and print that the PI result is back. Add one `ValueError`
   demonstration to the existing block in the showcase form: bind `bad_level = 1.5` on its
   own line, then `PIController(fixed_output=bad_level)` inside the `try`, leaving the
   surrounding demos as they are.
7. Update `STRUCTURE.md`: the constructor row's signature and description, a new
   `PIController.fixed_output` row, the `reset` row, the `update` row's description, the
   `main()` row, and the `tests/test_pi_controller.py` paragraph.
8. Add the README usage lines: set `radiator.fixed_output = 0.0` with a comment about a
   price spike, call `update`, set it back to `None`.
9. Run `ruff check .`, `ruff format --check .`, `mypy` and
   `python -m heatingsystem.pi_controller.pi_controller`; fix anything they report.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | A default-constructed controller reads back `fixed_output is None`, and an unfixed controller's full command sequence, integral and history over a multi-step run match hand-computed values in both modes, extending the existing single-step hand computations. Step 7's diff review confirms no existing test in `tests/test_pi_controller.py` was modified or weakened. | A1 |
| T2 | With a level set at construction, and separately when set after some unfixed steps, radiator `update` returns exactly that level for a cold, a hot and an at-setpoint measurement and with a per-call setpoint, each command is appended to the history, and `fixed_output` reads back the level. Boundaries `0.0` and `1.0` are accepted and returned. | A2 |
| T3 | In floor-heating mode with a level strictly inside `(0, 1)`, every command is `0.0` or `1.0`, and after `history_length` steps the duty cycle equals the level to within one slot (`1 / history_length`); a level of `0.0` gives all-off and `1.0` all-on over a full window; the first command still reads the history before appending. | A3 |
| T4 | A fixed controller and an unfixed twin fed the same measurements have equal `integral` after every step, in both saturation directions and in the linear region; a setpoint passed to `update` while fixed is stored; a non-finite measurement or setpoint still raises while fixed and appends nothing. | A4 |
| T5 | `nan`, `inf`, `-inf`, a value just below `0.0` and just above `1.0` each raise `ValueError` naming the value, both as the constructor keyword and via the setter; after a failed set the previous level (a number, and separately `None`) is unchanged. Integer levels `0` and `1` are accepted, read back as `float`, and `update` returns a `float` while fixed; a `str` level raises `TypeError`, not `ValueError`. | A5 |
| T6 | In radiator mode, after setting then clearing the override, the next `update` returns exactly the unfixed twin's command (integrals equal by A4, `_to_command` passes through) and the history holds both the fixed and the resumed commands. In floor-heating mode the released command is not compared to a twin, because the fixed commands sit in the duty-cycle window by design; the assertion is instead that over a subsequent full window the duty cycle returns to the PI demand. `reset()` zeroes the integral and clears the history but leaves `fixed_output` set. | A6 |

### Risks

- **Duty-cycle convergence tolerance (T3).** Over a window of `n` slots the realised duty
  cycle is quantised to `k / n`, so a level like `0.3` on the default 24-slot window lands
  at `7/24` or `8/24`. The test must assert within `1 / history_length`, or pick a level
  that is an exact multiple such as `0.25` on 24 slots. Not a halt: a precision choice.
- **Integral equality (T4).** Because the anti-windup keys on the raw PI output and not on
  the issued command, the fixed twin's integral must match the unfixed twin's exactly, not
  approximately, for identical float inputs. If the build finds they differ, that means
  the substitution landed before the anti-windup block, which is an implementation bug to
  fix, not a concept question. If after the fix they still differ, **halt**: A4 would be
  unachievable as written.
- **Showcase form.** `main()` already has inline literals in places from before the
  conventions were written; the new case must follow the showcase form (named inputs,
  call, output) without rewriting the old cases, which are out of scope.
- **`bool` sneaking in.** `True` passes `math.isfinite` and the range check and would be
  stored as `float(True) == 1.0`. Not guarded: it is consistent with how `kp` and
  `setpoint` are validated today, and mypy rejects it at the call site.

### Critique

Findings from the `plan-critic` read, verdict *accept with changes*; all six applied.

1. **T6 compared a released floor-heating controller to an unfixed twin, which is
   guaranteed to differ** because the fixed commands sit in the duty-cycle window by
   design. Applied: T6 now compares twins in radiator mode only and asserts duty-cycle
   recovery over a full window in floor mode.
2. **The setter's numeric contract was open in both directions**: an `int` level would be
   stored and returned untouched, breaking `update -> float`; a `str` would raise
   `TypeError` and step 5 would have to guess. Applied: the setter stores `float(value)`,
   `TypeError` is documented as deliberate and unguarded, and T5 covers both.
3. **T1 was a tautology** (default `None` versus explicit `None`). Applied: T1 now asserts
   multi-step hand-computed sequences in both modes and leaves the diff check to step 7.
4. **The plan touched `README.md` but section 1's scope list did not name it.** Applied to
   section 1, as a scope clarification rather than a new behaviour: the usage section
   documents every other public member.
5. **Three docstrings would contradict the code** (`_to_command`, `HeatingMode`, the
   module docstring all say "PI output"). Applied to guide step 4: rename the parameter to
   `level` and reword.
6. **Guide step 6 put the new `ValueError` demo in a block that uses inline literals, which
   the Risks section forbids.** Applied: the new demo binds `bad_level` first and leaves the
   old demos alone.


---

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry.

Built as planned. Every step of the implementation guide was followed in order: the
`fixed_output: float | None = None` keyword-only constructor argument, the private
`_fixed_output` attribute assigned through the setter, the `fixed_output` property under
*Properties* after `is_history_full`, the `level = u if self._fixed_output is None else
self._fixed_output` substitution in `update` (inserted after the anti-windup block and
before `_to_command`, leaving the PI computation, clamp, anti-windup and history append
untouched), `_to_command`'s parameter renamed from `u` to `level` with its docstring and
`HeatingMode`'s reworded, the `reset` docstring note, the two showcase additions in `main()`
(the fixed-output demo case and the `bad_level` `ValueError` demo), `STRUCTURE.md`, and the
README usage lines.

One presentational addition beyond the guide: `main()`'s own docstring (not itself part of
the Public API table) was updated to mention the new `fixed_output` demonstration, for
consistency with the rest of its description — not a deviation from any planned signature
or behaviour.

`ruff format` reflowed one README line to fit its width; the comment was shortened rather
than left to wrap.

---

## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `38 files already formatted` |
| `mypy` | `Success: no issues found in 13 source files` |
| `pytest` (existing suite, confirms nothing broke) | `349 passed in 4.89s` |
| Plan completeness | every signature in the Public API table exists as written — see table below |
| `STRUCTURE.md` | in sync — audited manually (no `Agent`/subagent-spawn tool was available in this run to invoke `structure-auditor`; compared every changed entry against the code by hand instead) |
| `python -m heatingsystem.pi_controller.pi_controller` | ran clean, exit 0; expected `RuntimeWarning` about re-import from `sys.modules`; output includes the new `-- fixed_output override --` block (three fixed steps at `command=0.2000`, `fixed_output : 0.2`, then release showing `fixed_output : None  (released -> PI result: 1.0000)`) and the new `bad_level = 1.5` `ValueError` demo (`fixed_output=1.5 -> ValueError: fixed_output must be a finite number in [0.0, 1.0] or None, got 1.5.`) |

### Plan completeness — Public API table (section 2) vs. code

| Signature | Result |
|---|---|
| `PIController(kp: float = 0.3, ki: float = 0.015, mode: HeatingMode \| str = HeatingMode.RADIATOR, setpoint: float = 21.0, *, history_length: int = 24, fixed_output: float \| None = None)` | Match — `pi_controller.py:94-103`, assigned through the setter at line 157 as planned |
| `PIController.fixed_output -> float \| None` (getter) | Match — `pi_controller.py:297-305` |
| `PIController.fixed_output` setter `(value: float \| None) -> None` | Match — `pi_controller.py:307-330`; `ValueError` message, range check and `math.isfinite` order, and `float(value)` storage all as planned |
| `PIController.update(measured: float, setpoint: float \| None = None) -> float` | Match — signature unchanged (`pi_controller.py:163`); the `level = u if self._fixed_output is None else self._fixed_output` substitution sits after the anti-windup block and before `_to_command`, exactly as guided |
| `PIController.reset() -> None` | Match — signature and behaviour unchanged (`pi_controller.py:248-255`); docstring notes `fixed_output` is left alone |
| `main() -> None` | Match — showcase gained the fixed-output case and the `bad_level` `ValueError` demo, both in the required named-variable / call / output form |

No **Missing**, no **Deviation**, no **Unplanned** entries — every row of the plan's Public API table exists in the code exactly as written, and no additional public surface was added beyond it.

### STRUCTURE.md audit

Compared by hand against `src/heatingsystem/pi_controller/pi_controller.py`, `src/heatingsystem/pi_controller/__init__.py` and `src/heatingsystem/__init__.py`:

- The `PIController` constructor row, the new `PIController.fixed_output` row, the `update` row's description and the `main()` row all match the code's current signatures and behaviour character for character.
- `reset`'s row already says "Leaves `fixed_output` unchanged."
- No private name (`_fixed_output`, `_to_command`) appears in `STRUCTURE.md`.
- `src/heatingsystem/pi_controller/__init__.py` and `src/heatingsystem/__init__.py` are unchanged by this round (no new package-level export, per section 1's scope) and their `STRUCTURE.md` entries still match.
- The `tests/test_pi_controller.py` paragraph does not yet mention `fixed_output` coverage — correct at this step, since step 5 (Test) has not run yet; nothing to update here until then.
- No stale entries (files that no longer exist) and no module on disk missing from the file.

Conclusion: in sync. No edits required.

---

## 5. Test log

> Written in step 5: the dynamic half.

Two `test-designer` briefs (`input-space`, `contract`) were run in parallel by the
orchestrator (this environment cannot spawn subagents from inside a subagent) and their
output merged here: 15 cases each, deduplicated by what they proved, the sharper of any
pair kept, every contradiction kept. 24 new test functions were written (115 cases
collected for this file, up from the pre-round count once parametrization is expanded),
all in `tests/test_pi_controller.py` under six new `# ---` sections, one per test intent.
No existing test was modified or weakened.

| Intent | Test names | Result |
|---|---|---|
| T1 (A1) | `test_fixed_output_defaults_to_none_and_unfixed_sequence_unchanged` | pass |
| T2 (A2) | `test_fixed_output_radiator_returns_level_regardless_of_error`, `test_fixed_output_accepts_boundaries_at_construction_and_setter`, `test_fixed_output_set_mid_run_leaves_state_untouched_then_pins_every_command` | pass |
| T3 (A3) | `test_fixed_output_floor_quarter_level_converges_to_exact_fraction`, `test_fixed_output_floor_boundary_levels_are_constant`, `test_fixed_output_floor_first_command_reads_history_before_append`, `test_fixed_output_floor_set_mid_run_reads_existing_history_first`, `test_fixed_output_floor_history_length_one_alternates`, `test_fixed_output_floor_level_just_above_min_fires_once_per_window`, `test_fixed_output_floor_level_just_below_max_rests_once_per_window` | pass |
| T4 (A4) | `test_fixed_output_integral_matches_unfixed_twin_when_error_reverses`, `test_fixed_output_integral_matches_unfixed_twin_across_regimes`, `test_fixed_output_integral_matches_unfixed_twin_when_error_sequence_reverses`, `test_fixed_output_update_stores_setpoint_and_advances_integral`, `test_fixed_output_update_still_validates_and_appends_nothing` | pass |
| T5 (A5) | `test_fixed_output_rejects_non_finite_and_out_of_range`, `test_fixed_output_failed_set_leaves_previous_value`, `test_fixed_output_string_raises_type_error_not_value_error`, `test_fixed_output_int_and_bool_stored_and_returned_as_float` | pass |
| T6 (A6) | `test_fixed_output_release_returns_twin_command_and_keeps_both_in_history`, `test_fixed_output_floor_release_duty_cycle_recovers_over_one_window`, `test_fixed_output_reset_leaves_override_set` (both modes, parametrized), `test_fixed_output_set_and_cleared_while_saturated` | pass |

All 405 tests pass (`349` pre-round + `56` new cases from the 24 functions above); no
production bug was found — every hand-computed and twin-comparison expectation was
independently verified against the shipped code before being written into an assertion,
and matched on the first run. `ruff check .`, `ruff format --check .` and `mypy` are green
without changes beyond the two docstring fixes below.

Trivial docstring wording fix (outside the Public API table, as invited by the brief):
`HeatingMode`'s `RADIATOR` attribute doc and `duty_cycle`'s docstring both still said
"the PI output" after the plan renamed the concept to "demand level" everywhere else;
reworded both to "the demand level" / "target demand level" in
`src/heatingsystem/pi_controller/pi_controller.py`.

Edge cases considered and deliberately skipped, with reasons:

- **Negative zero, signed on the way back out** (input-space #14). Skipped: this is float
  sign-propagation through `float(value)` and radiator pass-through, not a behaviour any
  acceptance criterion decides or a caller could reasonably depend on either way; pinning
  it would test CPython's float semantics, not this feature.
- **A second "still validates, appends nothing" case covering the same combinations**
  (input-space #11). Skipped as a duplicate of
  `test_fixed_output_update_still_validates_and_appends_nothing`, which already covers a
  bad `measured` alone and a bad `setpoint` alongside a good `measured`.
- **A single test asserting boundary levels pass through in both modes at once**
  (input-space #7). Skipped as a duplicate: the radiator half is
  `test_fixed_output_accepts_boundaries_at_construction_and_setter`, the floor half is
  `test_fixed_output_floor_boundary_levels_are_constant`.
- **Constructor validation order — an invalid `mode` raising before an invalid
  `fixed_output` is reached.** Skipped; both briefs flagged it themselves as low value, and
  it tests an ordering the concept never assigned meaning to.
- **The pre-existing `update` validation order — a `setpoint` is stored before `measured`
  is validated.** Not this round's to change, per the brief. Considered rather than tested
  as a defect: `test_fixed_output_update_still_validates_and_appends_nothing`'s cases are
  deliberately chosen (bad `measured` alone, or bad `setpoint` with a *good* `measured`) so
  no assertion depends on that order either way.
- **Idempotency of `update`.** Skipped; `update` is stateful by design (advances the
  integral, appends to history), so "same input twice" is not expected to give the same
  output and asserting so would misdescribe the module.
- **`duty_cycle` on an empty window / division by zero.** Already guarded and already
  covered by the pre-existing suite (`test_construction_defaults` and others); not new
  surface from this round.
- **`Decimal` / `Fraction` values for `fixed_output`.** Skipped; `math.isfinite` accepts
  them but nothing in this codebase ever passes one, and the setter's contract is stated in
  terms of `float | None`.

Other observations from the briefs, resolved:

- **A5's "previous setting unchanged" is vacuous at construction** (no object exists to
  compare against a "previous" value). `test_fixed_output_rejects_non_finite_and_out_of_range`
  asserts the raise at both construction and via the setter;
  `test_fixed_output_failed_set_leaves_previous_value` asserts the *unchanged* half only via
  the setter, against both a numeric and a `None` previous value, matching the brief's own
  resolution of this.
- **The setter's `Raises:` section documents only `ValueError`, not the deliberate,
  unguarded `TypeError` for a non-numeric value.** Left as-is: `kp`, `ki` and `setpoint`
  follow the same convention already (a `TypeError` from `math.isfinite` on a non-numeric
  value is possible but not documented), so this is consistent with the module's existing
  style rather than a gap this round introduced.
- **A correction to the plan's own Risks section.** "Integral equality (T4)" worried that a
  substitution landing *before* the anti-windup block would silently change the integral.
  On inspection the anti-windup branches test `raw` and `error`, never the demand `level`
  handed to `_to_command` — so a wrongly-placed substitution could not, in fact, produce a
  different committed integral by that specific mechanism.
  `test_fixed_output_integral_matches_unfixed_twin_across_regimes` and its sibling tests
  still catch a substituted `raw`, a skipped PI block, or a frozen integral; they just do
  not distinguish those from a merely mislocated (but otherwise correct) substitution. Not
  a halt — the implementation matches the plan exactly (verified in section 4), and the
  twin-integral tests pass.
- **Floor heating's first command after release is decided against a window still full of
  the fixed commands, not against a clean PI-only history** (contract brief's observation,
  case 7). `test_fixed_output_floor_release_duty_cycle_recovers_over_one_window` pins this:
  the first released command is `1.0` precisely because the window is still all zeros from
  the fixed period. This follows from the concept (history records what the actuator was
  actually told to do) rather than contradicting it; flagged here for step 6's attention
  when it reads A3 and A6 together.

---

## 6. Concept check

> Written in step 6, against section 1 — not against section 2. The question is whether
> the thing built is the thing agreed, not whether it matches the plan.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | Yes | `test_fixed_output_defaults_to_none_and_unfixed_sequence_unchanged` (T1) hand-computes 4 unfixed steps and matches exactly. The full pre-existing 349-case suite is unmodified (`git diff 7ba0fba..28a66aa -- tests/test_pi_controller.py` adds only new sections) and still passes: `pytest tests/` → `405 passed`, no failures, no weakened assertions. `pi_controller.py:200-231` (error, PI computation, clamp, anti-windup) is byte-identical to before this round; only lines 233-240 insert the `level = u if self._fixed_output is None else self._fixed_output` substitution, which is a no-op when `_fixed_output is None`. |
| A2 | Yes | `test_fixed_output_radiator_returns_level_regardless_of_error` (T2) parametrizes measured ∈ {-50, 50, 21} and per-call setpoint overrides, asserting `update` returns exactly `0.2` every time, appended to history. `test_fixed_output_accepts_boundaries_at_construction_and_setter` covers both construction (`fixed_output=level`) and post-hoc setting for `0.0` and `1.0`. `fixed_output` reads back the level via the getter (`pi_controller.py:297-305`), exercised in every T2 test. |
| A3 | Yes | `test_fixed_output_floor_quarter_level_converges_to_exact_fraction`: level `0.25`, floor-heating, 24-step window — every output ∈ {0.0, 1.0} (`set(outs) <= {0.0, 1.0}`), `duty_cycle == pytest.approx(0.25)` exactly at the window boundary, and after 500 steps stays within `1/24` of 0.25. `test_fixed_output_floor_boundary_levels_are_constant` and the `history_length=1` alternation test corroborate the binary-output guarantee at other levels. |
| A4 | Yes | Three twin-comparison tests (`..._when_error_reverses`, `..._across_regimes` parametrized over both modes and 3 measurement regimes, `..._when_error_sequence_reverses`) assert `fixed.integral == free.integral` (exact equality, not `approx`) after every step, including saturation in both directions. `test_fixed_output_update_stores_setpoint_and_advances_integral` confirms a passed setpoint is stored (`ctrl.setpoint == 21.5`) and the integral advances while fixed. `test_fixed_output_update_still_validates_and_appends_nothing` confirms `ValueError` on non-finite `measured`/`setpoint` while fixed, with nothing appended and the integral untouched — validation and the anti-windup-keyed-on-raw-`u` mechanism (`pi_controller.py:207-231`, unchanged) both still run underneath the override, exactly as A4 requires. |
| A5 | Yes | `test_fixed_output_rejects_non_finite_and_out_of_range` parametrizes `nan`, `inf`, `-inf`, and just-outside-boundary floats, at both construction and via the setter, matching the value in the raised message. `test_fixed_output_failed_set_leaves_previous_value` asserts the prior setting (a number, then `None` on a fresh controller) survives a failed `ValueError` set and a failed `TypeError` set. Construction-time "previous unchanged" is inherently vacuous (no object exists yet to compare against) — correctly noted as such in section 5 rather than asserted as something it cannot be. |
| A6 | Yes | `test_fixed_output_release_returns_twin_command_and_keeps_both_in_history`: after clearing, the next `update` equals an unfixed twin's command exactly (radiator mode, where `_to_command` passes the level through so this is the direct test of "PI result again"), and history holds both the fixed and resumed commands. `test_fixed_output_floor_release_duty_cycle_recovers_over_one_window` shows the floor-mode duty cycle recovering to the PI demand over one window after release. `test_fixed_output_reset_leaves_override_set` (both modes) asserts `reset()` zeroes integral and history but `fixed_output` stays at its set level. |

Out-of-scope items checked against the diff (`git diff 7ba0fba..28a66aa`) and the code — none were built:

- **Timing, scheduling or automatic release** — not present; `fixed_output` only ever changes when the caller assigns it (`pi_controller.py:307-330`), nothing clears it based on elapsed time or step count.
- **Price awareness of any kind** — no reference to price, cost or any external signal anywhere in the diff.
- **Any change to how the integral or anti-windup work** — `pi_controller.py:200-231` is unchanged text; confirmed by the A4 twin-integral tests passing with exact equality.
- **A "hold at the last computed value" mode** — no such mode exists; `fixed_output` is always an explicit float the caller supplies (constructor keyword or setter), never derived from a prior `update` call.

Surface check: no new public name was added at package level (`src/heatingsystem/__init__.py` and `src/heatingsystem/pi_controller/__init__.py` are untouched by this round's diff — confirmed via `git diff 7ba0fba..28a66aa --stat`), matching section 1's "does not touch" note. `test.py` is likewise untouched, matching section 1. The showcase (`python -m heatingsystem.pi_controller.pi_controller`) runs clean (exit 0, only the expected `sys.modules` `RuntimeWarning`) and includes a worked `fixed_output` example in the required named-variable / call / output showcase form: `fixed_level = 0.2` bound, `ctrl_rad.fixed_output = fixed_level`, three `update` calls each printing `command=0.2000` alongside the still-advancing `integral`, then release showing `fixed_output : None  (released -> PI result: 1.0000)` — a reader who only read the concept would recognise this as the agreed feature. `ruff check .`, `ruff format --check .` and `mypy` are all green; `STRUCTURE.md` was audited by hand against the code (no `Agent`/subagent-spawn tool is available inside this subagent either) — the `PIController` constructor row, the `fixed_output` row, `update`'s description, `reset`'s note and `main()`'s row all match the shipped signatures and behaviour character for character; no private name (`_fixed_output`) appears in it.

The two observations flagged for this step's attention, judged rather than obeyed:

- **Floor heating's first command after release is decided against a window still holding the fixed commands.** This is not drift: section 1 states explicitly that "the fixed command is appended to the history like any other command, so the history and duty cycle always describe what the actuator was actually told to do." A window that is still full of `0.0`s from a fixed period causing the first released command to fire high is the direct, intended consequence of that sentence, not a contradiction of A3 or A6. `test_fixed_output_floor_release_duty_cycle_recovers_over_one_window` pins exactly this and is correct evidence for, not against, A6 ("clearing hands control back to the PI loop on the next call" — it does; the *duty-cycle* value that call is judged against is, by design, history, not a fresh PI-only baseline).
- **`update` stores a passed `setpoint` before validating `measured`.** Confirmed pre-existing: `pi_controller.py:189-197` is unchanged by this round's diff (`git diff 7ba0fba..28a66aa -- src/heatingsystem/pi_controller/pi_controller.py` touches only lines 19-22, 75-86, 94-157, 233-330 and the `_to_command`/`HeatingMode` docstrings — never the setpoint/measured validation order). Section 1 and its acceptance criteria never mention validation *order*, only that inputs are validated and a passed setpoint is stored (A4) — both of which hold regardless of order. Correctly out of this round's remit; not a finding against any A1–A6 criterion.

Drift found, and what was done about it: none. Every acceptance criterion is met with direct, checkable evidence; nothing from the out-of-scope list was built; no public surface exists beyond what section 1 and the plan's Public API table describe. Work proceeds to step 7.

### Earlier rounds still hold

> Later rounds only. Re-check every acceptance criterion from every earlier round in this
> folder: this round changed code they depend on, and their tests passing is necessary but
> not sufficient — a criterion can be satisfied by tests that no longer describe what the
> feature does.

Not applicable — this is round 1 of `feat/fixed-output`, the first round on this branch; there are no earlier rounds to re-check.

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

# Attribute surface: validated settings, PI demand, range constants

<!-- claude-plan step=6 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/fixed-output` |
| Round | `2` |
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
| 6 | Concept check | `/concept-check` | in `/build` | in progress |
| 7 | Ship | `/ship` | in `/build` | pending |
| 8 | Recommend | `/recommend` | with the user | pending |
| 9 | Pull request | `/create-pr` | with the user | pending |
| 10 | Review | `/watch-pr` | on the pull request | pending |

Statuses: `pending`, `in progress`, `done`.

## Builds on

| Round | File | What it delivered |
|---|---|---|
| 1 | `01-fixed-output.md` | `PIController.fixed_output`: a validating property and constructor keyword holding the actuator at a level in `[0, 1]` while set; the level replaces the PI output before the mode mapping, the PI loop and integral keep running underneath, the fixed command is recorded in `history`, and `reset()` leaves it alone. Plus, as small changes at its step 8: a hold window in the `test.py` simulation with a README paragraph on the release burst, and the constructor documented once on the class docstring. |

This round came from three recommendations of round 1, grouped at the user's request
because they define one thing, the controller's attribute surface:

> **R1** — Expose the PI demand the controller computes and discards while fixed, as a
> read-only property (e.g. `pi_output`, the clamped `u` of the last `update`). While held,
> the only things that leave the controller are the fixed command and the raw integral. A
> Home Assistant dashboard showing "demand vs actual", or an automation releasing the hold
> early because demand is near zero anyway, must recompute the PI result by hand today.

> **R4** — One numeric-input contract for the whole controller: validating properties for
> `setpoint`, `kp`, `ki` and `mode` like `fixed_output` has; `update` validating both
> inputs before storing either; `bool` rejected and the non-numeric `TypeError` naming the
> attribute; one shared finite-check helper. `ctrl.setpoint = nan` is accepted today and
> silently pins the valve at full; `ctrl.mode = "radiator"` (a str) silently routes to
> floor modulation; `update(nan, setpoint=22.0)` raises and still stores 22.0;
> `fixed_output = True` stores 1.0, the opposite of "hold".

> **R6** — Re-export `OUTPUT_MIN` and `OUTPUT_MAX` from the package root. `fixed_output` is
> the first caller-supplied input bounded by the actuator range, and the error message
> names the constants, but an AppDaemon app that clamps an `input_number` or configures a
> slider's range must reach into the module the README says not to reach into.

What is already on the branch that this round must not break:

- Round 1's six acceptance criteria (A1–A6 in `01-fixed-output.md`), re-checked at this
  round's step 6: unfixed behaviour byte-for-byte, the exact-level radiator pass-through,
  floor-heating modulation of the fixed level, the integral advancing as unfixed, the
  `ValueError` contract of the `fixed_output` setter with the previous value untouched,
  and release plus `reset()` semantics.
- The 405-test suite, of which 56 cases cover `fixed_output`; none may be weakened.
- The `test.py` simulation and its hold window, driven by module constants.
- The `TypeError` for a non-numeric `fixed_output` is currently documented as deliberately
  unguarded; R4 changes that contract (the error must name the attribute) and round 1's
  test pinning the bare `TypeError` is expected to be updated, not deleted.

---

## 1. Concept

### What this is

Every setting on `PIController` gets the contract `fixed_output` received in round 1.
`kp`, `ki`, `setpoint` and `mode` become validating properties, and the constructor
assigns through them so one validation path serves construction and later assignment
alike. A wrong value raises `ValueError`, a wrong type raises `TypeError`, both naming the
attribute and the offending value, and a failed assignment leaves the previous value
untouched. `bool` counts as a wrong type for every numeric input, including `measured` and
the per-call `setpoint` of `update`. `update` validates its measurement before it stores
anything, so a call that raises changes no state at all.

A new read-only property reports the PI demand of the last step: the clamped result in
the actuator range that the mode mapping would have received. While the output is held by
`fixed_output`, it still reports what the loop wanted, so a caller can see demand and
actual side by side. It reads `None` before the first `update` and again after `reset()`,
because the loop has computed nothing at those points.

`OUTPUT_MIN` and `OUTPUT_MAX` are importable from the package root, next to
`PIController` and `HeatingMode`.

### Why it is worth building

The controller runs unattended under an automation. Today `ctrl.setpoint = nan` is
accepted and silently pins the valve at full, `ctrl.mode = "radiator"` as a plain string
silently routes to floor-heating modulation, `update(nan, setpoint=22.0)` raises and still
stores the setpoint, and `fixed_output = True` stores `1.0`, the opposite of "hold". None
of these surface anywhere; they are found by a cold or overheated room. Round 1 introduced
the validating-property style for one attribute; this round makes it the rule so the next
attribute has a convention to follow.

The PI demand exists at every step and is thrown away. A Home Assistant dashboard showing
"demand vs actual", or an automation that releases a hold early because the loop wants
nothing anyway, has to recompute it by hand. The range constants are named in the
`fixed_output` error message and bound the first caller-supplied input that has a range,
but the README tells callers not to reach into the module that defines them.

### Inputs and outputs

- **Settings, in:** `kp`, `ki`, `setpoint` take a real number (not `bool`) that is
  finite; `fixed_output` as in round 1, now also rejecting `bool`; `mode` takes a
  `HeatingMode` or its string value and stores the enum. The same values are accepted at
  construction. A non-numeric or `bool` value raises `TypeError`; a numeric value outside
  the rule raises `ValueError`; an unknown mode raises `ValueError`. Every message names
  the attribute and repeats the value.
- **`update`, in:** `measured` and the optional `setpoint`, under the same numeric rule.
  `measured` is validated first; the setpoint is then assigned through its setter. A
  failed call stores nothing.
- **Out:** the PI demand property, a float in `[OUTPUT_MIN, OUTPUT_MAX]` or `None`.
  `update`'s return value and `history` are unchanged.
- **Package root:** `OUTPUT_MIN` and `OUTPUT_MAX`, equal to the module's constants.

### How it connects to the rest of the repo

- Changes `src/heatingsystem/pi_controller/pi_controller.py` (`PIController`, its
  showcase `main`), `src/heatingsystem/__init__.py` and
  `src/heatingsystem/pi_controller/__init__.py` (the two re-exports),
  `tests/test_pi_controller.py`, `STRUCTURE.md`, and the README usage section (the new
  property and the constants).
- Round 1's test that pins a bare `TypeError` for a string `fixed_output` is updated to
  expect the attribute-naming message; its `bool` case flips from "stored as float" to
  "rejected". Nothing else in the existing suite may change.
- `test.py` is untouched; a later round may plot the PI demand under the hold band.

### Explicitly out of scope

- Any change to the PI arithmetic, anti-windup, clamp or mode mapping.
- Persisting or restoring state (round 3) and moving anything out of the controller
  (round 4).
- Making `history_length` settable (it would resize the window) or `integral` a
  validated property (round 3 decides how state is written).
- New validation on `history_length` beyond the existing "at least 1".

### Acceptance criteria

| # | The finished feature... |
|---|---|
| B1 | Assigning a non-finite value to `kp`, `ki` or `setpoint`, or a non-finite or out-of-range value to `fixed_output`, raises `ValueError` naming the attribute and the value, and the previous value is unchanged. |
| B2 | Assigning a non-numeric value, `bool` included, to `kp`, `ki`, `setpoint` or `fixed_output`, or passing one as `measured` or `setpoint` to `update`, raises `TypeError` naming the attribute, and the previous value is unchanged. |
| B3 | Assigning `mode` accepts a `HeatingMode` or its string value and stores the enum; anything else raises `ValueError` naming the attribute, and the previous mode is unchanged. |
| B4 | An `update` call that raises, for any reason, leaves `setpoint`, `integral`, `history` and the PI demand exactly as they were. |
| B5 | After each successful `update`, the PI demand property equals that step's clamped PI result; while the output is fixed it still reports the PI result rather than the fixed level; it is `None` before the first update and after `reset()`. |
| B6 | `OUTPUT_MIN` and `OUTPUT_MAX` are importable from the package root and equal the module's values. |
| B7 | Every input that raised at construction before this round raises the same error class there now, with a message that also names the attribute, and round 1's six criteria still hold. |

### Open questions

None. Decisions taken in step 1 without asking, because one reading was clearly right:
the PI demand is the clamped value, not the raw sum; it is `None` before the first update
and after `reset()`; `mode`'s setter coerces exactly as the constructor does;
`history_length` and `integral` stay as they are.

---

## 2. Plan

### Approach

**Chosen — one private check, five properties, one new read-only.** A private helper
takes an attribute name and a value and returns it as a `float`, raising `TypeError` when
the value is a `bool` or not an `int`/`float` and `ValueError` when it is not finite, with
both messages naming the attribute and repeating the value. `kp`, `ki` and `setpoint`
become properties whose setters call it; `fixed_output`'s setter calls it too before its
range check, which replaces the bare `TypeError` from `math.isfinite`; `mode`'s setter
coerces through `HeatingMode(value)` and raises `ValueError` naming `mode` for anything
the enum rejects. The constructor assigns every setting through its property, so it loses
its own checks and the two paths cannot disagree. `update` runs `measured` through the
helper first, then assigns a passed setpoint through the setter, so nothing is stored
until both are known good. A private field holds the clamped PI result of the last step and
a read-only `pi_output` property exposes it; `reset()` sets it back to `None`. The two
constants are added to both `__init__.py` files and their `__all__`.

**Rejected — a descriptor class** (`_FiniteFloat` assigned as a class attribute for each
setting): removes four near-identical property pairs, but the reader then has to know the
descriptor protocol to see where validation happens, and `fixed_output` (optional, ranged)
and `mode` (an enum) would each need their own descriptor anyway, so it saves less than it
looks. **Rejected — inline checks in each setter**: the current style, six copies of the
finite check; it is exactly what R4 asked to remove.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/heatingsystem/pi_controller/pi_controller.py` | changed | Private finite-number helper; `kp`, `ki`, `setpoint`, `mode` as properties; `fixed_output` setter rejecting non-numbers with the attribute name; `pi_output`; constructor and `update` assigning through the setters; `reset` clearing `pi_output`; docstrings; showcase |
| `src/heatingsystem/pi_controller/__init__.py` | changed | Re-export `OUTPUT_MIN` and `OUTPUT_MAX` |
| `src/heatingsystem/__init__.py` | changed | Re-export `OUTPUT_MIN` and `OUTPUT_MAX` |
| `tests/test_pi_controller.py` | changed | New section for B1–B7; two round 1 tests updated as section 1 says |
| `STRUCTURE.md` | changed | Constructor row, new property rows, `update`/`reset`/`main()` rows, both `__init__` export tables, the test-file paragraph |
| `README.md` | changed | Two lines showing `pi_output` beside the fixed output, and `hs.OUTPUT_MAX` in the range comment |

### Public API

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `PIController(kp: float = 0.3, ki: float = 0.015, mode: HeatingMode \| str = HeatingMode.RADIATOR, setpoint: float = 21.0, *, history_length: int = 24, fixed_output: float \| None = None)` | `pi_controller.py` | Unchanged signature. Assigns `mode`, `kp`, `ki`, `setpoint` and `fixed_output` through their setters, in that order after the `history_length` check, so the constructor raises exactly what the setters raise. | B7 |
| `PIController.kp -> float` / setter `(value: float) -> None` | `pi_controller.py` | Proportional gain. Setter: finite real number, `bool` rejected. | B1, B2 |
| `PIController.ki -> float` / setter `(value: float) -> None` | `pi_controller.py` | Integral gain. Same contract. | B1, B2 |
| `PIController.setpoint -> float` / setter `(value: float) -> None` | `pi_controller.py` | Target temperature. Same contract; also the path a per-call setpoint in `update` takes. | B1, B2, B4 |
| `PIController.mode -> HeatingMode` / setter `(value: HeatingMode \| str) -> None` | `pi_controller.py` | Actuator mode. Setter coerces with `HeatingMode(value)`; raises `ValueError` naming `mode` and listing the valid values for anything else. | B3 |
| `PIController.fixed_output -> float \| None` / setter `(value: float \| None) -> None` | `pi_controller.py` | As round 1, plus: a non-numeric or `bool` value raises `TypeError` naming `fixed_output`. | B1, B2 |
| `PIController.pi_output -> float \| None` | `pi_controller.py` | Read-only. The clamped PI result of the last `update`, in `[OUTPUT_MIN, OUTPUT_MAX]`; `None` before the first update and after `reset()`. Reports the PI result even while `fixed_output` is set. | B5 |
| `PIController.update(measured: float, setpoint: float \| None = None) -> float` | `pi_controller.py` | Unchanged signature. Validates `measured` through the helper, then assigns `setpoint` through its setter, then computes; records `pi_output` after the clamp. A raise leaves every attribute as it was. | B2, B4, B5 |
| `PIController.reset() -> None` | `pi_controller.py` | Unchanged signature. Now also sets `pi_output` to `None`; still leaves `fixed_output` and the settings alone. | B5 |
| `OUTPUT_MIN`, `OUTPUT_MAX` re-exported from `heatingsystem` and `heatingsystem.pi_controller` | both `__init__.py` | The same objects as the module constants, listed in `__all__`. | B6 |
| `main() -> None` | `pi_controller.py` | Showcase gains: `pi_output` printed beside the command during the fixed steps, one `mode` reassignment, and one `TypeError` demo (`setpoint = "22"`). | — (showcase) |

`integral` stays a plain public attribute and `history_length` stays constructor-only, per
section 1. `HeatingMode`, `history`, `duty_cycle`, `is_history_full` are unchanged.

### Implementation guide

1. Add a module-level private helper `_finite(name: str, value: object) -> float` above
   `HeatingMode`: if `isinstance(value, bool)` or not `isinstance(value, numbers.Real)`,
   raise `TypeError(f"{name} must be a real number, got {value!r} ({type(value).__name__}).")`;
   then `try: number = float(value) except OverflowError as exc: raise OverflowError(f"{name} is too large to represent as a float.") from exc`
   (an `int` such as `10**400` overflows `float()` and `math.isfinite` alike, and its
   `repr` is itself refused past 4300 digits, so the message names the attribute and not
   the value); then `if not math.isfinite(number): raise ValueError(f"{name} must be a finite number, got {value!r}.")`;
   return `number`. `numbers.Real` is section 1's "real number": `int`, `float`, `Fraction`
   and numpy scalars pass, `Decimal` does not. Google docstring with `Raises:` listing all
   three. `import numbers` at the top.
2. Replace the `kp`, `ki`, `setpoint` plain attributes with private fields `_kp`, `_ki`,
   `_setpoint` and property pairs under the *Properties* section (getter returns the field;
   setter stores `_finite("<name>", value)`). Keep the getters' docstrings one line each and
   put the contract in the setter's `Raises:`.
3. Replace `mode` with `_mode` and a property pair: getter returns the enum; setter does
   `try: self._mode = HeatingMode(value) except ValueError as exc: raise ValueError(f"mode must be a HeatingMode or one of {[m.value for m in HeatingMode]}, got {value!r}.") from exc`.
   The existing test matches on the word `mode`, which this keeps.
4. In the `fixed_output` setter, after the `None` branch, replace the `math.isfinite`
   check with `level = _finite("fixed_output", value)` and range-check `level`, keeping the
   existing `ValueError` message for the range case but formatting it with `{value!r}` (the
   value the caller passed) and storing `level`. Update its docstring: `TypeError` is now
   raised and named, `bool` rejected, `OverflowError` named.
5. Add `self._pi_output: float | None = None` to the constructor's state block and a
   read-only `pi_output` property after `is_history_full`, docstring saying what it is and
   when it is `None`.
6. Rewrite the constructor body in today's order: `self.mode = mode` first, then
   `if history_length < 1: raise ValueError(...)` as now, then `self.kp = kp`,
   `self.ki = ki`, `self.setpoint = setpoint`, the state block (`integral`, `_history`,
   `_pi_output`, `_fixed_output = None`), then `self.fixed_output = fixed_output`. Delete
   the old inline checks. Declare the backing fields as class-level annotations without
   values (`_kp: float`, `_ki: float`, `_setpoint: float`, `_mode: HeatingMode`), which
   the pinned mypy accepts for assignment through a property setter; the `mode` setter's
   wider parameter type (`HeatingMode | str`) against its getter (`HeatingMode`) is
   likewise accepted by mypy 2.3+.
7. In `update`: replace the two validation blocks with `measured = _finite("measured", measured)`
   first, then `if setpoint is not None: self.setpoint = setpoint`. After the clamp line
   (`u = ...`), add `self._pi_output = u`. `update`'s `Raises:` gains `TypeError` for a
   non-numeric or `bool` `measured`/`setpoint` and `OverflowError` for a huge `int`.
   Nothing else in `update` changes; the substitution and anti-windup block stay
   byte-identical.
8. In `reset`, add `self._pi_output = None` and mention it in the docstring.
9. Update the class docstring: `Raises:` gains the `TypeError` line, the `Args:` say
   `bool` is rejected; note that every setting is a validating property.
10. Both `__init__.py`: import and list `OUTPUT_MIN`, `OUTPUT_MAX` in `__all__`.
11. Showcase: in the fixed-output block print `pi_output` on each fixed step's line;
    after the floor-heating run, add a case in showcase form that reassigns `mode` on the
    radiator controller and prints one command; in the `ValueError` demo block add a
    `TypeError` demo in showcase form (`bad_setpoint = "22"` on its own line, then the
    assignment inside `try`, printing the message). That assignment is a deliberate type
    error under mypy, so it carries exactly
    `# type: ignore[assignment]  # deliberate misuse for the demo`, which the Python rules
    permit; `setattr` with a literal name is not an alternative (ruff B010).
12. `STRUCTURE.md`: rewrite the constructor row ("gains and setpoint must be finite" and
    "Public attributes: `kp`, `ki`, `mode`, `setpoint`, `integral`" become "every setting
    is a validating property, `integral` a plain attribute"); rewrite the `fixed_output`
    row (it says only `ValueError`); add the four property rows and `pi_output`; update
    `update`, `reset`, `main()` rows; add the constants to both `__init__` export tables;
    in the test-file paragraph rewrite "`int` and `bool` levels stored and returned as
    `float`" and "`TypeError` — deliberately unguarded" (both stop being true) and extend
    it with this round's coverage.
13. README: in the fixed-output block add `print(radiator.pi_output)` with a comment that
    it is the PI demand the loop would have issued; change the range comment on
    `fixed_output` to name `hs.OUTPUT_MIN` and `hs.OUTPUT_MAX`.
14. Run `ruff check .`, `ruff format --check .`, `mypy`,
    `python -m heatingsystem.pi_controller.pi_controller`. The existing suite will fail on
    exactly two parameter cases of one round 1 test,
    `test_fixed_output_int_and_bool_stored_and_returned_as_float[True-1.0]` and
    `[False-0.0]`, until step 5 changes it as below. That is expected and is not a gate
    failure for step 4, which runs the static checks only.

    Step 5 changes round 1's tests exactly like this and nothing more: rename that test to
    `test_fixed_output_int_stored_and_returned_as_float`, keeping the `(1, 1.0)` and
    `(0, 0.0)` cases; move `True` and `False` into a new `TypeError` test under T2 that
    matches `fixed_output` in the message, at construction and via the setter, with the
    previous value kept; add `match="fixed_output"` to
    `test_fixed_output_string_raises_type_error_not_value_error` and to the bare
    `TypeError` assertion in `test_fixed_output_failed_set_leaves_previous_value`. No
    other existing test changes.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | For each of `kp`, `ki`, `setpoint`: `nan`, `inf`, `-inf` raise `ValueError` whose message contains the attribute name and `repr(value)`, via the setter and as the constructor keyword, and the setter leaves the previous value. For `fixed_output`: the round 1 range cases still raise with the previous value kept. | B1 |
| T2 | For each of `kp`, `ki`, `setpoint`, `fixed_output`: a `str`, `None` (except `fixed_output`, where it clears), a `list`, `True` and `False` raise `TypeError` whose message names the attribute, via the setter and at construction, leaving the previous value. `update(True)`, `update("20")`, `update(20.0, setpoint=False)` and `update(20.0, setpoint="22")` raise `TypeError` naming `measured` or `setpoint`. An `int` and a `Fraction` are accepted and read back as `float`. A huge `int` (`10**400`) raises `OverflowError` naming the attribute, via the setter, at construction and through `update`, leaving the previous value. | B2, B7 |
| T3 | `mode` accepts each enum member and each string value and reads back the enum; after switching a radiator controller to floor heating, the next command is binary; an unknown string, an `int` and `None` raise `ValueError` naming `mode` and listing the valid values, leaving the previous mode. | B3 |
| T4 | After each of `update(nan, setpoint=22.0)`, `update("x", setpoint=22.0)`, `update(20.0, setpoint=nan)`, `update(True)`, `update(20.0, setpoint="22")`, `update(20.0, setpoint=True)` and `update(10**400)` on a controller with prior state (a few good steps, then separately with `fixed_output` set), `setpoint`, `integral`, `history` and `pi_output` all equal their values before the failed call. | B2, B4 |
| T5 | `pi_output` is `None` on a fresh controller; after one hand-computed step in the linear region it equals the clamped PI result; in saturation it is `1.0` or `0.0`; while `fixed_output` is set it equals an unfixed twin's radiator command for the same measurements, not the fixed level, and in floor mode it is the fractional demand rather than the binary command; it is `None` after `reset()`; assigning to it raises `AttributeError`. | B5 |
| T6 | `hs.OUTPUT_MIN is pi_controller.OUTPUT_MIN` and likewise for `OUTPUT_MAX`, both from the package root and from the subpackage, and both names are in each `__all__`. | B6 |
| T7 | Every constructor case the existing suite already covers (invalid mode string, `history_length` 0 and negative, `nan`/`inf` gains and setpoint) still raises `ValueError`, and its message now contains the attribute name; the whole pre-existing suite passes with only the two round 1 tests named in section 1 changed. | B7 |

### Risks

- **The two round 1 tests that must change.** `test_fixed_output_int_and_bool_stored_and_returned_as_float`
  asserts `True` is stored as `1.0`, which B2 forbids; the string test asserts a bare
  `TypeError`, which still passes but should match the new message. Step 5 updates them and
  records why in the test log. Deleting either, or any other existing test, is not
  permitted. Not a halt.
- **mypy and the private fields.** Assigning through a property setter inside `__init__`
  before the backing field exists is fine at runtime, but mypy wants the field's type
  declared; class-level annotations do that without giving the fields values. If mypy
  still objects, declare and assign the fields directly in `__init__` and then call the
  setters; do not add `type: ignore` for the fields (the one in the showcase demo, guide
  step 11, is the only one this round adds). Not a halt.
- **`int` remains accepted.** `numbers.Real` with the `bool` check first keeps `0` and
  `1` working for `fixed_output` as round 1 promised, and numpy scalars and `Fraction`
  keep working as they do today. `Decimal` is not a `numbers.Real`, so `Decimal("nan")`
  moves from `ValueError` to `TypeError`; B7 is read as covering `int`, `float`, `str` and
  `None` inputs, and this one case is stated to the user at the plan gate rather than
  guarded. Not a halt.
- **Order of constructor errors.** With several bad arguments the first raised is the first
  assigned. No test may depend on that order; one bad argument per case.
- **Anything that changes the PI arithmetic** is out of scope; if step 3 finds it cannot
  place `self._pi_output = u` without touching the anti-windup block, **halt**.

### Critique

Findings from the `plan-critic` read, verdict *accept with changes*; all seven applied.

1. **A huge `int` raised a bare `OverflowError` without the attribute name, breaking B7,
   and step 5 would have had to pick a class.** Applied: the helper re-raises
   `OverflowError` naming the attribute; T2 and T4 cover it.
2. **`isinstance(value, (int, float))` was narrower than section 1's "real number" and
   would have turned numpy and `Fraction` inputs into `TypeError`.** Applied:
   `numbers.Real`; the risk text corrected. `Decimal("nan")` is the one input whose class
   changes, stated at the gate.
3. **The showcase `TypeError` demo needs a `type: ignore`, which the Risks wording seemed
   to ban.** Applied: the exact comment prescribed in step 11, the ban scoped to the
   fields, class-level annotations settled as the field declaration.
4. **The round 1 test changes were unspecified and the failure prediction wrong.** Applied:
   step 14 names the two parameter cases that fail and spells out the rename, the moved
   cases and the two `match=` additions.
5. **Docstrings and `STRUCTURE.md` rows that become false were not in the guide.**
   Applied to steps 7 and 12.
6. **B2's "previous value unchanged" for `update` inputs had no test.** Applied: T4 now
   covers the `TypeError` and overflow calls with the same state check.
7. **The constructor order was reversed and the range message would report the converted
   value.** Applied: `mode` first as today; `{value!r}` in the message.


---

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry.

Built as planned. Every guide step was followed in order: the module-level `_finite(name,
value)` helper above `HeatingMode` (raising `TypeError` for a `bool` or non-`numbers.Real`
value, `ValueError` for non-finite, `OverflowError` — re-raised naming the attribute — for
a value too large to represent as a `float`); `kp`, `ki`, `setpoint` and `mode` replaced by
`_kp`/`_ki`/`_setpoint`/`_mode` and validating property pairs under *Properties*; the
`fixed_output` setter now calling `_finite("fixed_output", value)` before its range check
and storing the returned `level`, with the range message still built from `{value!r}` (the
caller's original value); the read-only `pi_output` property added after
`is_history_full`, backed by `self._pi_output: float | None = None`; the constructor
rewritten in the specified order (`mode`, the `history_length` check, `kp`/`ki`/`setpoint`,
the state block, `fixed_output` last) with the four class-level annotations (`_kp: float`,
`_ki: float`, `_setpoint: float`, `_mode: HeatingMode`) declared above `__init__`; `update`
validating `measured` through the helper first, then assigning a passed `setpoint` through
its setter, with `self._pi_output = u` inserted immediately after the clamp line and the
anti-windup block left byte-identical; `reset` clearing `_pi_output` to `None`; the class
docstring's `Args`/`Raises` rewritten to state the validating-property contract and the new
`TypeError`; both `__init__.py` files re-exporting `OUTPUT_MIN` and `OUTPUT_MAX` (ruff
format wrapped the now four-name import); the three showcase additions in `main()` (a
`pi_output` reading on each fixed step's line, a `mode` reassignment case in showcase form
after the floor-heating run, and a `bad_setpoint = "22"` `TypeError` demo carrying exactly
`# type: ignore[assignment]  # deliberate misuse for the demo`); `STRUCTURE.md`'s
constructor row, the four new property rows, `pi_output`, `update`, `reset` and `main()`
rows, and both `__init__` export tables; and the README `pi_output` line and range comment.
`ruff check .`, `ruff format .`, `mypy` and
`python -m heatingsystem.pi_controller.pi_controller` all ran clean; `pytest` shows exactly
the two predicted failures
(`test_fixed_output_int_and_bool_stored_and_returned_as_float[True-1.0]` and `[False-0.0]`,
403 passed / 2 failed), left for step 5 as guide step 14 specifies.

Three points of judgment, none touching an acceptance criterion:

- **Property order.** The plan names an order for the *constructor's* assignments but not
  for the property definitions themselves. `mode`, `kp`, `ki`, `setpoint` were placed at
  the top of the *Properties* section (mirroring the constructor's assignment order),
  ahead of the pre-existing `history`/`duty_cycle`/`is_history_full`; `pi_output` sits
  between `is_history_full` and `fixed_output` exactly as guide step 5 says.
- **README "range comment on fixed_output."** No comment naming a range already sat next
  to `fixed_output` to rewrite in place — the nearby range wording (`continuous output in
  [0.0, 1.0]`) describes the radiator constructor a dozen lines above, not the override.
  Read the instruction as introducing that comment at the override: a new line,
  `# Any level in [hs.OUTPUT_MIN, hs.OUTPUT_MAX] is accepted; anything else raises
  ValueError.`, directly above `radiator.fixed_output = 0.0`. `ruff format` then wrapped
  the `pi_output` print's trailing comment onto its own line for width; the comment was
  shortened to keep it a single readable line instead, matching the file's existing
  one-line-per-statement style.
- **`STRUCTURE.md`'s test-file paragraph.** Guide step 12 asks to rewrite the two claims
  that "stop being true" (`bool` no longer stored as `float`; the `TypeError` no longer
  deliberately unguarded) and "extend it with this round's coverage." The two stale claims
  are fixed now, since they describe the shipped code's actual contract. The extension is
  left as a forward pointer to step 5: round 1's step 4 established (and this plan's own
  section 1 confirms) that the paragraph's content for a round's tests is written once
  those tests exist, and none of T1–T7's tests exist yet at this step.

One presentational addition beyond the guide, for the same reason as round 1's: `main()`'s
own docstring was reworded to mention the `pi_output` reading, the `mode` reassignment and
the `TypeError` demonstration, and the `=== ValueError demonstrations ===` banner was
renamed `=== ValueError and TypeError demonstrations ===`, since it is no longer accurate
otherwise. Neither changes a planned signature or behaviour.

---

## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` (exit 0), first run, nothing to fix |
| `ruff format --check .` | `39 files already formatted` (exit 0), first run |
| `mypy` | `Success: no issues found in 13 source files` (exit 0), first run |
| Plan completeness | every signature in the Public API table exists as written — see table below |
| `STRUCTURE.md` | in sync, checked by hand row by row (see below); no edit needed |
| `python -m heatingsystem.pi_controller.pi_controller` | ran clean, exit 0; output below |

`pytest` (not this step's gate, run to confirm the plan's prediction): `403 passed, 2
failed` — exactly `test_fixed_output_int_and_bool_stored_and_returned_as_float[True-1.0]`
and `[False-0.0]`, both on the new `TypeError` raised for a `bool` `fixed_output`, as guide
step 14 predicted. Left untouched for step 5; `tests/` was not edited this step.

### Plan completeness — Public API table (section 2) vs. code

| Signature | Result |
|---|---|
| `PIController(kp, ki, mode, setpoint, *, history_length, fixed_output)` | Present, verbatim — `pi_controller.py:150-159` |
| `PIController.kp` / setter | Present, verbatim — `pi_controller.py:319-339` |
| `PIController.ki` / setter | Present, verbatim — `pi_controller.py:341-361` |
| `PIController.setpoint` / setter | Present, verbatim — `pi_controller.py:363-383` |
| `PIController.mode` / setter | Present, verbatim — `pi_controller.py:295-317` |
| `PIController.fixed_output` / setter | Present, verbatim — `pi_controller.py:436-476` |
| `PIController.pi_output -> float \| None` (read-only) | Present, verbatim, no setter defined — `pi_controller.py:421-434` |
| `PIController.update(measured, setpoint=None) -> float` | Present, verbatim — `pi_controller.py:194-224`; validates `measured` first, assigns `setpoint` through its setter, records `pi_output` after the clamp |
| `PIController.reset() -> None` | Present, verbatim — `pi_controller.py:280-289`; clears `_pi_output` |
| `OUTPUT_MIN`, `OUTPUT_MAX` re-exported | Present in both `__init__.py` files and both `__all__` lists |
| `main() -> None` | Present; `pi_output` printed on each fixed step, a `mode` reassignment case, a `TypeError` demo with the exact prescribed `type: ignore` comment |

No missing, no unplanned, no undocumented deviation. Nothing to fix here — step 3's
implementation notes (section 3) already record the three points of judgment, none of
which touch a signature.

### STRUCTURE.md audit (by hand — subagents cannot spawn subagents in this environment)

Checked every row named in the task by hand against the code and the file on disk:

- **`src/heatingsystem/__init__.py` export table**: lists `HeatingMode`, `PIController`,
  `OUTPUT_MIN`, `OUTPUT_MAX`, all from `heatingsystem.pi_controller` — matches
  `src/heatingsystem/__init__.py`'s actual import and `__all__` exactly.
- **`src/heatingsystem/pi_controller/__init__.py` prose**: says it re-exports
  `HeatingMode`, `PIController`, `OUTPUT_MIN` and `OUTPUT_MAX` from `pi_controller.py` —
  matches the file exactly.
- **`PIController` controller-section rows**: `OUTPUT_MIN`/`OUTPUT_MAX` constants, the
  `HeatingMode` row, the constructor row (validating-property language, `history_length`
  checked inline, `integral` plain), `kp`/`ki`/`setpoint`/`mode`/`fixed_output` property
  rows, the new `pi_output` row, `update`, `reset`, `history`, `duty_cycle`,
  `is_history_full`, and `main()` — every one read against the corresponding code above and
  matches the current signatures, defaults, return types and raised-exception contracts.
  No stale claim found (the constructor row no longer lists `kp`/`ki`/`mode`/`setpoint` as
  plain "Public attributes", which round 1's wording did).
- **Test-file paragraph** (`tests/test_pi_controller.py` section): the `fixed_output`
  paragraph's two round-1 claims were already rewritten to match this round's code — the
  `TypeError` is described as naming `fixed_output` (no longer "deliberately unguarded"),
  and only `int` levels are described as stored/returned as `float` (`bool` dropped, since
  B2 now rejects it). Checked against the actual test source
  (`test_fixed_output_string_raises_type_error_not_value_error`,
  `test_fixed_output_int_and_bool_stored_and_returned_as_float`,
  `test_fixed_output_failed_set_leaves_previous_value`): both claims hold for the tests as
  they exist right now, before step 5 touches them. The forward-pointer sentence added
  after it ("This round's attribute surface ... gets its own coverage ... at step 5") is
  accurate — none of T1–T7's tests exist yet.

No edit to `STRUCTURE.md` was needed; step 3 already left it in sync.

### `python -m heatingsystem.pi_controller.pi_controller` — full output

```
<frozen runpy>:128: RuntimeWarning: 'heatingsystem.pi_controller.pi_controller' found in sys.modules after import of package 'heatingsystem.pi_controller', but prior to execution of 'heatingsystem.pi_controller.pi_controller'; this may result in unpredictable behaviour
=== HeatingMode enum ===
  HeatingMode.RADIATOR = 'radiator'
  HeatingMode.FLOOR_HEATING = 'floor_heating'

=== RADIATOR mode (5-step warm-up) ===
  Initial integral : 0.0
  Initial duty_cycle: 0.0
  is_history_full  : False
  step 1: measured=18.0 C  command=0.9450  integral=3.0000
  step 2: measured=18.5 C  command=0.8325  integral=5.5000
  step 3: measured=19.2 C  command=0.6495  integral=7.3000
  step 4: measured=20.0 C  command=0.4245  integral=8.3000
  step 5: measured=20.6 C  command=0.2505  integral=8.7000
  step 6: measured=21.3 C  command=0.0360  integral=8.4000
  history         : (0.945, 0.8325, 0.6495000000000002, 0.4245, 0.25049999999999956, 0.03599999999999977)
  duty_cycle      : 0.5230
  is_history_full : True

  -- setpoint raised to 22 deg C mid-run --
  command after setpoint change: 0.3465  (setpoint now 22.0)

  -- fixed_output override --
  fixed step 1: command=0.2000  integral=9.1000  pi_output=1.0000
  fixed step 2: command=0.2000  integral=9.1000  pi_output=1.0000
  fixed step 3: command=0.2000  integral=9.1000  pi_output=1.0000
  fixed_output    : 0.2
  fixed_output    : None  (released -> PI result: 1.0000)

  After reset(): integral=0.0, history=()

=== FLOOR_HEATING mode (24-sample window) ===
  mode coerced to : <HeatingMode.FLOOR_HEATING: 'floor_heating'>
  10 steps at 19.0 deg C (demand > 0 -> ON/OFF switching):
    step  1: duty_cycle=1.000  command=1
    step  2: duty_cycle=0.500  command=0
    step  3: duty_cycle=0.667  command=1
    step  4: duty_cycle=0.750  command=1
    step  5: duty_cycle=0.600  command=0
    step  6: duty_cycle=0.667  command=1
    step  7: duty_cycle=0.714  command=1
    step  8: duty_cycle=0.750  command=1
    step  9: duty_cycle=0.778  command=1
    step 10: duty_cycle=0.800  command=1
  is_history_full : False
  Running 14 more steps to fill window...
  is_history_full : True
  duty_cycle      : 0.917

  -- mode reassigned on the radiator controller --
  command after mode change: 1  (mode now <HeatingMode.FLOOR_HEATING: 'floor_heating'>)

=== ValueError and TypeError demonstrations ===
  Bad mode       -> ValueError: mode must be a HeatingMode or one of ['radiator', 'floor_heating'], got 'steam'.
  Infinite kp    -> ValueError: kp must be a finite number, got inf.
  history_length=0 -> ValueError: history_length must be >= 1, got 0.
  fixed_output=1.5 -> ValueError: fixed_output must be a finite number in [0.0, 1.0] or None, got 1.5.
  NaN measured   -> ValueError: measured must be a finite number, got nan.
  NaN setpoint   -> ValueError: setpoint must be a finite number, got nan.
  Bad setpoint   -> TypeError: setpoint must be a real number, got '22' (str).

All demonstrations completed successfully.
```

The `RuntimeWarning` is the expected consequence of the subpackage re-exporting the module,
per `.claude/rules/python.md`. The showcase form was checked against the rules: every
argument in the new additions (`new_mode`, `bad_setpoint`, `fixed_level`, `fixed_steps`,
`cold_temp`) is a named variable, the calls are on their own lines, results are named and
printed, `new_mode` carries the same-line comment listing `HeatingMode`'s members, and the
`bad_setpoint` case carries exactly the `type: ignore[assignment]  # deliberate misuse for
the demo` comment the plan's guide step 11 prescribes.

Nothing needed fixing — no ruff, format, mypy or showcase failure occurred on the first
run, so the halting rule for a gate failing twice never came into play.

---

## 5. Test log

> Written in step 5: the dynamic half.

Two `test-designer` subagents ran in parallel (`input-space`, `contract`), each against the
shipped code and this plan; their merged output is
`/tmp/claude-0/-home-user-heatingsystem/87d9b7a4-0c90-5a51-97fb-1b439f365d19/scratchpad/test-designer-briefs-round2.md`.
Every hand-computed number either designer proposed was re-verified against the venv
Python before it went into an assertion (`kp=0.3, ki=0.015, setpoint=21.0, measured=20.5`:
steps `0.1575, 0.165, 0.1725, 0.18`, matching the brief; every overflow, `TypeError` and
`ValueError` message, the mode-switch and `pi_output` sequences, and the
`test_constructor_and_setter_raise_identical_error_for_same_value` cases were all run
directly rather than assumed).

Round 1's two prescribed changes (guide step 14): the `bool` cases of
`test_fixed_output_int_and_bool_stored_and_returned_as_float` moved into a new
`test_fixed_output_bool_raises_type_error_naming_attribute_and_keeps_previous`, the
survivor renamed `test_fixed_output_int_stored_and_returned_as_float`; `match="fixed_output"`
added to `test_fixed_output_string_raises_type_error_not_value_error` and to the bare
`TypeError` assertion in `test_fixed_output_failed_set_leaves_previous_value`. Per the
contract designer's T7 suggestion (permitted by this round's brief: "nothing else in the
existing suite may change beyond adding `match=` to existing assertions where a designer
suggests it"), `match=<attribute>` was also added to eight pre-existing template
assertions that already raised the right exception but didn't check the message:
`test_history_length_zero_raises`, `test_history_length_negative_raises`,
`test_non_finite_kp_raises`, `test_non_finite_ki_raises`, `test_non_finite_setpoint_raises`,
`test_update_non_finite_measured_raises`, `test_update_inf_measured_raises`,
`test_update_setpoint_override_non_finite_raises`. No other existing test's body changed;
`test_invalid_mode_string_raises` already carried `match="mode"` and was left untouched.

| Intent | Test names | Result |
|---|---|---|
| T1 (B1) | `test_numeric_setters_reject_non_finite_naming_attribute_and_keep_previous` (`kp`/`ki`/`setpoint` × `nan`/`inf`/`-inf`), `test_fixed_output_range_error_repeats_the_passed_value_not_the_float` | pass |
| T2 (B2, B7) | `test_numeric_setters_reject_non_real_types_naming_attribute_and_type`, `test_fixed_output_rejects_non_real_types_naming_attribute`, `test_fixed_output_bool_raises_type_error_naming_attribute_and_keeps_previous`, `test_update_measured_rejects_non_numeric_and_bool_naming_measured`, `test_update_setpoint_kwarg_rejects_non_numeric_and_bool_naming_setpoint`, `test_update_setpoint_none_is_not_an_override_not_a_type_error`, `test_numeric_setters_accept_int_and_fraction_read_back_as_float`, `test_update_accepts_fraction_measured_and_int_setpoint`, `test_numeric_setters_overflow_raises_naming_attribute_and_keeps_previous`, `test_update_overflow_int_raises_naming_measured_or_setpoint`, `test_fixed_output_overflow_int_raises_overflow_not_range_value_error`, `test_numeric_setters_overflow_boundary_either_side` | pass |
| T3 (B3) | `test_mode_string_is_stored_as_enum_member_not_str`, `test_mode_switch_radiator_to_floor_next_command_is_binary`, `test_mode_switch_floor_to_radiator_next_command_is_continuous`, `test_mode_rejects_everything_else_with_value_error_listing_valid_values` | pass |
| T4 (B2, B4) | `test_update_failed_call_leaves_every_piece_of_state_untouched` (8 malformed inputs × fixed/unfixed), `test_update_failed_call_does_not_consume_a_step` | pass |
| T5 (B5) | `test_pi_output_none_then_value_across_fix_release_and_reset`, `test_pi_output_is_exactly_clamp_bound_in_saturation`, `test_pi_output_at_exact_clamp_edges_holds_the_integral`, `test_pi_output_reports_pi_demand_not_fixed_level_while_fixed`, `test_pi_output_reports_fractional_demand_not_binary_command_in_floor_mode`, `test_pi_output_is_read_only` | pass |
| T6 (B6) | `test_output_constants_at_package_root_are_the_module_objects` | pass |
| T7 (B7) | `test_constructor_and_setter_raise_identical_error_for_same_value`, `test_setters_on_success_change_only_their_own_attribute`, `test_every_pre_round_1_constructor_case_still_raises_value_error_with_attribute`, plus the eight pre-existing tests above that gained `match=` | pass |

Full-tree `pytest`: **525 passed, 0 failed** (was 403 passed / 2 failed before this step, on
the two round-1 cases guide step 14 predicted; net +120 cases, all new). `ruff check .`,
`ruff format --check .` and `mypy` all clean after the test additions and two docstring
fixes (below) — no gate failed twice, so the halting rule for a repeated gate never applied.

No bug was found in the shipped production code by these tests — round 2's implementation
(step 3) already matched the plan. Two docstring-only fixes were made, both outside the
Public API table per this round's brief:

- The class docstring's `Raises:` was missing `OverflowError`, which the constructor does
  raise through the setters (confirmed by both designers). Added, naming which attributes.
- `_finite`'s docstring claimed "numpy scalar passes" without qualification; confirmed with
  the venv's numpy 2.5.3 that `numpy.bool_` (a distinct type, not `numbers.Real` and not
  `bool`) is correctly rejected by the same `TypeError` path as a plain `bool`, so the
  claim was over-broad. Reworded to say most numpy scalar types pass and `numpy.bool_` does
  not, for the same reason `bool` does not.

Edge cases considered and deliberately skipped, with reasons:

- **`fixed_output = -0.0` / `kp = -0.0` etc. (both designers).** `-0.0` passes the range and
  finiteness checks and is returned sign-preserved in radiator mode. Section 1's out-of-scope
  list and this round's brief both say this is not this round's to change (round 1 already
  skipped it as float semantics); noted in `STRUCTURE.md` and left for a step 8 recommendation
  rather than pinned by a test.
- **Extreme finite inputs producing a `nan` `raw` PI value that clamps to `OUTPUT_MAX`
  (input-space #14).** E.g. `setpoint=-1e308`, `measured=1e308` makes `error = -inf`, and
  `kp * -inf` (or `0.0 * -inf`) is `nan`; `max(0.0, min(1.0, nan))` clamps to `1.0`. This is
  the pre-existing PI arithmetic, explicitly out of scope this round (section 1); noted in
  `STRUCTURE.md` as a considered quirk and left for step 8 rather than tested or fixed here.
- **Gain change mid-run does not rescale the already-accumulated integral
  (input-space #13).** Existing, unrelated-to-this-round behaviour of `kp`/`ki` becoming
  settable — the integral is a raw accumulator, not a function of the current gains, and
  nothing in section 1 asks that to change. Not tested; a candidate for a step 8 note if it
  ever surprises a caller.
- **`mode` switch mid-run keeping the pre-switch commands in the duty-cycle window
  (input-space #12, contract observation).** Already exercised indirectly by the two
  mode-switch tests above and by the pre-existing floor-heating history tests; a dedicated
  test of the exact fractional-then-binary transition would mostly restate
  `PIController._to_command`'s existing, unit-tested duty-cycle rule rather than prove
  anything new about this round's B1–B7.
- **Two-bad-argument constructor calls and constructor argument order.** The plan's Risks
  section explicitly forbids depending on which argument's error fires first when several
  are bad; every test here uses exactly one bad argument per case, so this was not a case to
  add, only a constraint to respect.
- **`history_length` as `True`/`1.5`/`"24"` (both designers).** `history_length` is
  explicitly out of scope for this round's numeric contract (section 1: "New validation on
  `history_length` beyond the existing 'at least 1' " is excluded) — its own check is
  untouched by this round's changes, so widening its coverage here would be testing code
  this round did not write.
- **A parametrize list of a dozen-plus additional wrong-type values per attribute (e.g. the
  input-space brief's 16-value list including `"x" * 10_000` and separate Unicode-digit and
  whitespace-padded string cases).** `test_numeric_setters_reject_non_real_types_naming_attribute_and_type`
  already parametrizes 12 representative wrong types (`str`, empty `str`, `None`, `list`,
  `tuple`, `dict`, `complex`, two `Decimal`s, `bytes`, `bool` × 2) across three attributes
  (36 cases); adding a 10,000-character string or more near-duplicate string variants proves
  the same `isinstance(value, numbers.Real)` branch again without adding information, which
  this round's brief asks to avoid ("a parametrize list of a dozen wrong-type values is
  enough, do not include a ten-thousand-character string").
- **Purity and idempotency checklist rows beyond what the state-preservation and
  state-isolation tests already prove.** `test_update_failed_call_leaves_every_piece_of_state_untouched`,
  `test_update_failed_call_does_not_consume_a_step` and
  `test_setters_on_success_change_only_their_own_attribute` already cover "does a failed or
  successful call touch anything beyond what it should"; a further explicit
  call-twice-get-the-same-answer test would not exercise anything these do not.

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

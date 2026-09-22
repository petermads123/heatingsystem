# Attribute surface: validated settings, PI demand, range constants

<!-- claude-plan step=2 status=active -->

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

> Written in step 2, accepted by the user before step 3 starts. Concrete enough that
> step 3 is transcription, not invention.

### Approach

One paragraph on the chosen approach, and one on what was rejected and why.

### Modules

| Path | New or changed | Purpose |
|---|---|---|

### Public API

> Every public class and function, with its full signature as it will be written.
> `Covers` links back to the acceptance criteria above.

| Signature | Module | Purpose | Covers |
|---|---|---|---|

### Implementation guide

Ordered. Each entry small enough to finish and check.

1.
2.

### Test intents

> High-level: what a test must prove, not how it is written. Step 5 turns each of these
> into concrete cases, including the edge cases.

| # | Must prove | Covers |
|---|---|---|
| T1 | | |

### Risks

What could make this harder than it looks, and what the build should do if it does —
including whether it should halt.

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

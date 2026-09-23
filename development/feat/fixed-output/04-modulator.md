# Modulator: the actuator mapping as its own object

<!-- claude-plan step=2 status=active -->

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

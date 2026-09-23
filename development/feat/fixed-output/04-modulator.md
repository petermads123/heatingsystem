# Modulator: the actuator mapping as its own object

<!-- claude-plan step=1 status=active -->

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
| 1 | Conceptualize | `/conceptualize` | with the user | in progress |
| 2 | Plan | `/plan` | with the user | pending |
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

> Written in step 1, agreed with the user before step 2 starts. Prose, not code. Steps 3
> to 7 run without the user, and the one thing that stops them is a finding that would
> change this section — so what is not decided here is decided by a halt.

### What this is

### Why it is worth building

### Inputs and outputs

### How it connects to the rest of the repo

Which existing modules it calls, which call it, what it does not touch.

### Explicitly out of scope

### Acceptance criteria

> Numbered, observable, and phrased so that step 6 can mark each one met or not met.
> These are the contract. Step 2 plans against them, step 5 tests them, step 6 audits
> against them. If a criterion cannot be observed from outside the code, rewrite it.

| # | The finished feature... |
|---|---|
| A1 | |
| A2 | |

### Open questions

> Must be empty before step 2 begins. An unanswered question here is a decision being
> made by accident later — and nobody is watching when it happens.

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

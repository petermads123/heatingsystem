# Attribute surface: validated settings, PI demand, range constants

<!-- claude-plan step=1 status=active -->

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

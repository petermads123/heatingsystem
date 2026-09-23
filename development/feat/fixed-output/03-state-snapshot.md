# State snapshot and restore

<!-- claude-plan step=1 status=active -->

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

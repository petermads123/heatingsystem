# Fixed output override

<!-- claude-plan step=2 status=active -->


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
  showcase `main`) and `tests/test_pi_controller.py`, plus `STRUCTURE.md`.
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

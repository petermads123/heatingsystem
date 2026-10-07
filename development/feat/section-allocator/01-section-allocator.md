# Section allocator

<!-- claude-plan step=2 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/section-allocator` |
| Round | `1` |
| Branch | `feat/section-allocator` |
| Started | `2026-10-07` |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
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

Nothing — this is the first round.

---

## 1. Concept

### What this is

A multi-room controller for on/off heating sections that may each heat more than one room.
It is built once from a fixed physical layout: named rooms, each with one temperature
measurement and a priority weight; named sections, each with the share of its heat that
reaches each room it covers (e.g. HS2 gives 50 % to R1 and 50 % to R2); and one global
evenness weight. Every `update` (driven externally on the 5-minute poll, as today) takes one
temperature per room and returns a `0.0`/`1.0` command per section, in three stages:

1. **Room demand.** Each room runs the existing PI control law (same anti-windup as
   `PIController`) and yields a demand in `[0, 1]`.
2. **Allocation.** Section duty cycles `u` in `[0, 1]` are chosen by bounded weighted least
   squares: minimise the priority-weighted squared mismatch between each room's demand and
   the heat it receives (Σ share × duty over its sections), plus the evenness weight times
   the spread of duty among the sections serving each room. Solved with
   `scipy.optimize.lsq_linear`, the evenness term stacked as extra rows.
3. **Actuation.** Each section owns a floor-heating `Modulator` that turns its allocated duty
   into on/off commands over the rolling window.

The evenness term answers the cold-half-floor problem: if R2 is always hungry, a plain fit
lets HS2 cover all of R1's need and HS1 never runs. Where the problem has slack (HS3 not
saturated), shifting R2's heat to HS3 and R1's to HS1 keeps temperatures and spreads the
floor heat; where it has none, the weight sets the trade-off. Any weight above 0 is a real
trade-off, not a free tie-break: it pulls the fit slightly off exact demand.

### Why it is worth building

Rooms that share a section cannot each run their own `PIController`: two controllers would
fight over the shared section, and the shared section can leave part of a floor permanently
cold. Min/max limits were considered and dropped in favour of priority-weighted deviation.

### Inputs and outputs

- **Construction (fixed):** rooms with a priority weight each; sections with a share per
  covered room; the evenness weight; the window length (one for all sections); per-room
  initial setpoint, `kp`, `ki`. Changing any of the layout means building a new controller —
  from Home Assistant's side a full reset, unlike a gain or setpoint change.
- **Runtime (settable, effective next `update`):** per-room setpoint, `kp`, `ki`, validated
  like `PIController`'s.
- **`update`:** a temperature (°C) for every room, keyed by room name → a `0.0`/`1.0` command
  for every section, keyed by section name. Afterwards the last allocated duty per section
  and the last demand per room are readable.
- **Snapshot:** `to_dict()` / `from_dict()` covering layout, settings and running state, as
  JSON-friendly built-in types.

### How it connects to the rest of the repo

Reuses the PI control law of `heatingsystem.pi_controller` and `Modulator` from
`heatingsystem.modulator` (floor-heating mode, one per section), plus the shared validation
in `heatingsystem._validation`. Neither `PIController` nor `Modulator` changes behaviour.
Re-exported from `heatingsystem/__init__.py` like the other models. Adds the package's first
runtime dependencies: `scipy` (and through it `numpy`). numpy alone (own bounded solver) and
scikit-learn (no upper bound on coefficients, more dependencies) were rejected.

### Explicitly out of scope

- Room min/max limits.
- Electricity prices / cost-aware heating.
- A thermal model or MPC.
- A cap on simultaneously active sections (there is none).
- AppDaemon / Home Assistant glue.
- Coordinating *when* sections fire relative to each other inside a window.
- Changing the layout, priorities or evenness weight at runtime.

Assumptions: a section's shares summing below 1 means the rest heats something unmeasured;
each room's PI keeps today's conditional-integration anti-windup unchanged; the default
evenness weight is chosen in step 2.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | Is built from rooms (each with a priority), sections (each with shares per covered room) and an evenness weight. A share outside `(0, 1]`, a section whose shares sum above 1, a section naming an unknown room, a room no section covers, a priority ≤ 0, or a negative or non-finite evenness weight each raises `ValueError` naming the offender, and no controller is produced. The layout is read-only afterwards. |
| A2 | Lets each room's setpoint, `kp` and `ki` be changed at any time, with the same validation (exception class and attribute named) as `PIController`, taking effect on the next `update`. |
| A3 | `update` takes a temperature for every room and returns a `0.0`/`1.0` command for every section, keyed by section name. A missing or unknown room, or a non-finite or non-numeric temperature, raises before any state changes. The last allocated duty per section and the last demand per room are readable afterwards. |
| A4 | With evenness weight 0, when some duties in `[0, 1]` deliver every room's demand exactly, the allocation delivers them within a numerical tolerance — e.g. with HS2 split 50/50 between R1 and R2, demands 0.1 and 0.6 are met exactly. |
| A5 | When demands cannot all be met, the higher-priority room ends with the smaller mismatch, and raising one room's priority never increases that room's mismatch. |
| A6 | In the "R2 always hungry" case with HS3 not saturated, evenness 0 may leave HS1 at 0 while HS2 heats R1; any evenness weight > 0 gives HS1 a duty > 0 and narrows the gap between HS1's and HS2's duty, and raising the weight never widens that gap. |
| A7 | A room with exactly one dedicated section (R3 with HS4) produces exactly the command sequence of a standalone `PIController` in floor-heating mode with the same gains, setpoint and window length, regardless of the other rooms or the evenness weight. |
| A8 | `scipy` (bringing `numpy`) is a declared runtime dependency; the package installs and imports with it; `PIController` and `Modulator` behave exactly as before and the existing suite stays green. |
| A9 | `to_dict()` returns layout (rooms, priorities, section shares), evenness weight, window length, each room's setpoint, `kp`, `ki` and integral, and each section's command history as `json.dumps`-accepted built-in types; `from_dict(to_dict())`, also through a JSON round trip, yields a controller producing identical commands thereafter; a malformed or invalid snapshot raises naming the offending key, validated as the constructor would, and produces no controller. |

### Open questions

None.

---

## 2. Plan

### Approach

One paragraph on the chosen approach, and one on what was rejected and why.

### Modules

| Path | New or changed | Purpose |
|---|---|---|

### Public API

| Signature | Module | Purpose | Covers |
|---|---|---|---|

### Implementation guide

Ordered. Each entry small enough to finish and check.

1.
2.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | | |

### Risks

What could make this harder than it looks, and what the build should do if it does —
including whether it should halt.

---

## 3. Implementation notes

---

## 4. Verification log

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

| Intent | Test names | Result |
|---|---|---|

Edge cases considered and deliberately skipped, with reasons:

---

## 6. Concept check

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | | | |

Drift found, and what was done about it:

### Earlier rounds still hold

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

| # | Recommendation | Why it is critical | Effort | Decision |
|---|---|---|---|---|
| R1 | | | | |

Decisions: `deferred`, `rejected`, or `next round` — a new numbered file in this folder,
taken back through steps 1 to 7 on the same branch.

---

## 9. Pull request

| Field | Value |
|---|---|
| URL | opened by step 9 — see the branch's pull request |
| Opened as | ready for review |

---

## Halted

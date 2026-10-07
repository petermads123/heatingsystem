# Section allocator

<!-- claude-plan step=5 status=active -->

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
| 2 | Plan | `/plan` | with the user | done |
| 3 | Implement | `/implement` | in `/build` | done |
| 4 | Verify | `/verify` | in `/build` | done |
| 5 | Test | `/test` | in `/build` | in progress (halted, see Halted) |
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
reaches each room it covers (e.g. HS2 gives 50 % to R1 and 50 % to R2); and, per room, an
evenness weight (0 = this room's floor evenness does not matter). Every `update` (driven externally on the 5-minute poll, as today) takes one
temperature per room and returns a `0.0`/`1.0` command per section, in three stages:

1. **Room demand.** Each room runs the existing PI control law (same anti-windup as
   `PIController`) and yields a demand in `[0, 1]`.
2. **Allocation.** Section duty cycles `u` in `[0, 1]` are chosen by bounded weighted least
   squares: minimise the priority-weighted squared mismatch between each room's demand and
   the heat it receives (Σ share × duty over its sections), plus, per room, that room's
   evenness weight times the spread of duty among the sections serving it. Solved with
   `scipy.optimize.lsq_linear`, the evenness term stacked as extra rows.
3. **Actuation.** Each section owns a floor-heating `Modulator` that turns its allocated duty
   into on/off commands over the rolling window.

The evenness term answers the cold-half-floor problem: if R2 is always hungry, a plain fit
lets HS2 cover all of R1's need and HS1 never runs. Evenness is **per room** because a
shared section is pulled both ways — an even R1 floor wants HS2 low (to match HS1), an even
R2 floor wants HS2 high (to match HS3). Step 2's prototype showed one global weight letting
R2's larger gap win and widening R1's (demands 0.1/0.6: HS1 stayed 0, R1's gap grew from
0.20 to 0.23), while R1 = 0.1, R2 = 0 gave HS1 = HS2 = 0.067, HS3 = 0.567 with both demands
met exactly. A weight above 0 is otherwise a real trade-off, not a free tie-break: it may
pull the fit off exact demand.

### Why it is worth building

Rooms that share a section cannot each run their own `PIController`: two controllers would
fight over the shared section, and the shared section can leave part of a floor permanently
cold. Min/max limits were considered and dropped in favour of priority-weighted deviation.

### Inputs and outputs

- **Construction (fixed):** rooms with a priority and an evenness weight each; sections
  with a share per covered room; the window length (one for all sections); initial setpoint,
  `kp`, `ki` (shared defaults, then adjustable per room). Changing any of the layout means building a new controller —
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
each room's PI keeps today's conditional-integration anti-windup unchanged.

Amended at step 2, with the user: evenness changed from one global weight to a weight per
room, and A5/A6 reworded, after the prototype showed the original A5 ("higher priority
always wins") failing when a section saturates and the original A6 failing with a global
weight. Reworded again after the plan-critic: A5's second clause scoped to equal shares,
zero evenness and no saturation (at an interior optimum mismatch scales with priority ×
share, e.g. shares 0.2/0.8 with priorities 2/1 leave the higher-priority room worse off);
A6's evenness-0 example softened to "may", since it depends on the solver's tie-break.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | Is built from rooms (each with a priority and an evenness weight) and sections (each with shares per covered room). A share outside `(0, 1]`, a section whose shares sum above 1, a section naming an unknown room, a room no section covers, a priority ≤ 0, or a negative evenness weight each raises `ValueError` naming the offender (a non-numeric or non-finite value raises as `PIController`'s settings do), and no controller is produced. The layout — rooms, priorities, evenness weights, shares — is read-only afterwards. |
| A2 | Lets each room's setpoint, `kp` and `ki` be changed at any time, with the same validation (exception class and attribute named) as `PIController`, taking effect on the next `update`. |
| A3 | `update` takes a temperature for every room and returns a `0.0`/`1.0` command for every section, keyed by section name. A missing or unknown room, or a non-finite or non-numeric temperature, raises before any state changes. The last allocated duty per section and the last demand per room are readable afterwards. |
| A4 | With evenness weight 0, when some duties in `[0, 1]` deliver every room's demand exactly, the allocation delivers them within a numerical tolerance — e.g. with HS2 split 50/50 between R1 and R2, demands 0.1 and 0.6 are met exactly. |
| A5 | Raising one room's priority never increases that room's mismatch (demand − delivered heat). When demands cannot all be met, the competing rooms receive equal shares from the sections they share, their evenness weights are 0 and no section ends at 0 or 1, the higher-priority room ends with the smaller mismatch. |
| A6 | Raising one room's evenness weight never widens that room's own spread of duty among its sections. In the "R2 always hungry" case (demands R1 0.1, R2 0.6, HS2 split 50/50, HS3 not saturated), evenness 0 may leave HS1 at 0 while HS2 heats R1 (which exact fit is returned is the solver's choice), and R1's evenness weight > 0 with R2's at 0 gives HS1 a duty > 0, a narrower HS1–HS2 gap, and both demands still met. |
| A7 | A room with exactly one dedicated section (R3 with HS4) produces exactly the command sequence of a standalone `PIController` in floor-heating mode with the same gains, setpoint and window length, regardless of the other rooms or the evenness weight. |
| A8 | `scipy` (bringing `numpy`) is a declared runtime dependency; the package installs and imports with it; `PIController` and `Modulator` behave exactly as before and the existing suite stays green. |
| A9 | `to_dict()` returns layout (rooms, priorities, evenness weights, section shares), window length, each room's setpoint, `kp`, `ki` and integral, and each section's command history as `json.dumps`-accepted built-in types; `from_dict(to_dict())`, also through a JSON round trip, yields a controller producing identical commands thereafter; a malformed or invalid snapshot raises naming the offending key, validated as the constructor would, and produces no controller. |

### Open questions

None.

---

## 2. Plan

### Approach

A new subpackage `heatingsystem.allocator` holding `SectionAllocator` (the multi-room
controller) and `Room` (a per-room handle). Each `Room` **composes a `PIController` in
radiator mode** (`history_length=1`), whose radiator command *is* the clamped PI demand — so
the control law, anti-windup, finite guard and every setter's validation are reused, not
re-implemented. Each section owns a `Modulator` in floor-heating mode. Allocation is split by
**connected component** of the room–section graph (components share no variables, so the
objective separates exactly): a component of one room and one section is solved in closed
form, `duty = min(1, demand / share)` — exact, which is what makes A7 hold bit-for-bit — and
every other component is one `scipy.optimize.lsq_linear(..., bounds=(0, 1), method="bvls")`
call on a stacked matrix: fit rows `√priority_r · (Σ share·u − demand_r)` and, for each room
with ≥ 2 sections and evenness `e_r > 0`, spread rows `√e_r · (u_s − mean of the room's
sections' u)`. A room's **spread** is defined as exactly the penalised quantity,
`Σ_{s ∈ room} (u_s − mean)²` (for two sections, `gap² / 2`); A6 and T7 use this metric and
no other. Before the square roots, each component's priorities and evenness weights are
divided by the largest of them — the minimiser is unchanged and `1e300`-sized weights
cannot overflow. A5's and A6's monotonicity are properties of any exact minimiser of
`fit + λ·term` (compare the two optimality inequalities), so they hold by construction; the
tests check them numerically to a tolerance.

Rejected: **re-implementing the PI law inside the allocator** (duplicates `pi_controller.py`
and its 100-odd validation tests' worth of contract; composing a radiator-mode
`PIController` gets it for free, at the cost of a one-slot history window per room nobody
reads). **One global least-squares solve without component splitting** (A7 would hold only
to solver tolerance, and a floor-heating `Modulator` compares the duty against its window
mean, so a 1e-12 difference can flip a tied slot). **Raw nested dicts as the per-room
handle** — settings would have no validation; a `Room` with validating properties keeps the
`PIController` contract. **A public `allocate()` function** — no criterion needs it outside
the class; it stays private and tests may call it directly, as `test_modulator.py` already
does for private helpers.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/heatingsystem/allocator/__init__.py` | new | Subpackage entry point; re-exports `SectionAllocator`, `Room`. |
| `src/heatingsystem/allocator/allocator.py` | new | `Room`, `SectionAllocator`, the private layout validation, component split and solver, and `main()`. |
| `src/heatingsystem/__init__.py` | changed | Re-export `SectionAllocator` and `Room`; add both to `__all__`. |
| `pyproject.toml` | changed | `dependencies = ["numpy>=2", "scipy>=1.14,<2"]` — scipy ships Python 3.13 wheels from 1.14; numpy is declared because the module imports it directly. Nothing else; mypy already has `ignore_missing_imports = true`. |
| `tests/test_allocator.py` | new | The suite for this round (step 5). |
| `STRUCTURE.md` | changed | Tree line, `__init__` export table, both new modules with signature tables, the test file. Stays flat — see Risks. |
| `README.md` | changed | A short `SectionAllocator` usage section after "PIController usage", and the scipy dependency under install. |

### Public API

`Map = collections.abc.Mapping`. Every name and number below is validated with
`heatingsystem._validation`, naming the offender as shown in quotes.

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `Room(name: str, *, priority: float, evenness: float, kp: float, ki: float, setpoint: float)` | `allocator.py` | One room: its fixed priority and evenness weight, and a composed radiator-mode `PIController(kp, ki, "radiator", setpoint, history_length=1)`. A handle type: built by `SectionAllocator`, construction documented as internal (no defaults; not tested standalone beyond what the allocator surfaces). `name` must be a non-empty `str` (`TypeError`/`ValueError` naming `name`); `priority` finite and `> 0`, `evenness` finite and `>= 0` (`ValueError` naming `priority`/`evenness`; non-numeric/`bool` → `TypeError`, huge `int` → `OverflowError`, via `_validation.finite`). | A1, A2 |
| `Room.name -> str` | | Read-only. | A1 |
| `Room.priority -> float` | | Read-only. | A1, A5 |
| `Room.evenness -> float` | | Read-only. | A1, A6 |
| `Room.setpoint -> float` (settable) | | Delegates to the composed `PIController.setpoint`; same contract and message. | A2 |
| `Room.kp -> float` (settable) | | Delegates to `PIController.kp`. | A2 |
| `Room.ki -> float` (settable) | | Delegates to `PIController.ki`. | A2 |
| `Room.integral -> float` | | Read-only. Delegates to `PIController.integral`. | A9 |
| `Room.demand -> float \| None` | | Read-only. The clamped PI demand of the last `update` (`PIController.pi_output`); `None` before the first and after `from_dict`. | A3 |
| `SectionAllocator(rooms: Mapping[str, Mapping[str, float]], sections: Mapping[str, Mapping[str, float]], *, kp: float = 0.3, ki: float = 0.015, setpoint: float = 21.0, history_length: int = 24)` | `allocator.py` | The controller. `rooms` maps each room name to a mapping with exactly the keys `"priority"` and `"evenness"`; `sections` maps each section name to `{room name: share}`. `kp`/`ki`/`setpoint` are every room's initial values. Validation order and errors are listed in the implementation guide, step 3. | A1 |
| `SectionAllocator.update(measured: Mapping[str, float]) -> dict[str, float]` | | One step: `measured` must be a `Mapping` with exactly the room names (`TypeError` naming `measured` for a non-mapping; `ValueError` naming the missing keys, else the unknown ones); each value via `_validation.finite("measured['R1']", v)`. All of that before any state changes. Then each room's demand, the allocation, each section's `Modulator.command(duty)`; if the allocation raises, every room's integral and demand are restored to their values before the call and the error propagates (no command, no `duty` written). Returns a fresh dict `{section: 0.0 \| 1.0}` in the constructor's section order. | A3, A4, A5, A6, A7 |
| `SectionAllocator.rooms -> Mapping[str, Room]` | | Read-only view (`MappingProxyType`) of the `Room` handles in constructor order. | A2, A3 |
| `SectionAllocator.sections -> dict[str, dict[str, float]]` | | Read-only layout: a fresh deep copy of the shares, as floats. | A1 |
| `SectionAllocator.history_length -> int` | | Read-only. | A1, A9 |
| `SectionAllocator.duty -> dict[str, float] \| None` | | The last allocated duty per section, in `[0, 1]`; `None` before the first `update` and after `from_dict`. A fresh dict. | A3, A4, A5, A6 |
| `SectionAllocator.history -> dict[str, tuple[float, ...]]` | | Each section's command window, oldest first (its `Modulator.history`). | A7, A9 |
| `SectionAllocator.to_dict() -> dict[str, object]` | | Snapshot, exactly `{"history_length": int, "rooms": {name: {"priority", "evenness", "setpoint", "kp", "ki", "integral"}}, "sections": {name: {"shares": {room: float}, "history": [float, ...]}}}` in constructor order; fresh containers, `json.dumps`-ready. | A9 |
| `SectionAllocator.from_dict(data: Mapping[str, object]) -> Self` (classmethod) | | Rebuild: every level's key set checked with `_validation.snapshot_mapping` (missing before unknown); the layout goes through the constructor (same errors); then each room's `setpoint`/`kp`/`ki` through its setters and `integral` through `PIController.integral`; each section's window through `Modulator._from_dict({"mode": "floor_heating", "history_length": n, "fixed_output": None, "history": h})`. Any error is re-raised as the same class with the path prefixed (`rooms['R1'].integral: ...`, `sections['HS2'].history: ...`). No controller on failure. | A9 |
| `main() -> None` | `allocator.py` | Showcase: the R1/R2/R3 example layout; the hungry-R2 run with R1 evenness 0 vs 0.1; a priority change; a setpoint change; a JSON snapshot round trip with identical next commands; an invalid layout refused. | — (convention) |

### Implementation guide

1. `pyproject.toml`: add the runtime dependency. Create `.venv` if missing
   (`python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]"`) and confirm
   `import scipy` works there.
2. `Room` in `allocator.py`: validate `name`, `priority` (`> 0`), `evenness` (`>= 0`) with
   `_validation.finite` plus the range check (a `ValueError` naming the attribute, repeating
   the caller's value, like `_validation.level`'s message); build the composed
   `PIController`; the delegating properties; a private `_step(measured: float) -> float`
   returning `self._pi.update(measured)` (the radiator command, equal to `pi_output`).
3. `SectionAllocator.__init__`, in this order, each failure naming its path, nothing built
   on failure:
   1. `rooms` is a `Mapping` (`TypeError` naming `rooms`), non-empty (`ValueError`); each key
      a non-empty `str` (`TypeError`/`ValueError` naming `rooms`); each value a `Mapping`
      with exactly `priority`, `evenness` (`TypeError`/`ValueError` naming `rooms['R1']`);
      build each `Room` (its errors prefixed `rooms['R1'].`).
   2. `sections` the same shape checks (non-empty; keys non-empty `str`); each value a
      non-empty `Mapping` (`ValueError` naming `sections['HS1']` when empty); each key a room
      name (`ValueError` naming the section and the unknown room); each share via
      `_validation.finite` then `0 < share <= 1` (`ValueError` naming
      `sections['HS1']['R1']`); per section `math.fsum(shares) <= 1 + 1e-9` (`ValueError`
      naming the section and the sum).
   3. Every room covered by at least one section (`ValueError` naming the uncovered rooms,
      sorted).
   4. `history_length` via `_validation.window_length`; one floor-heating `Modulator` per
      section.
   5. Precompute the connected components (rooms ↔ sections, stable order) and, per
      multi-variable component, the dense share matrix.
4. Private `_allocate(demand: Mapping[str, float]) -> dict[str, float]`: per component —
   1 room × 1 section: `min(1.0, d / share) + 0.0`; otherwise build `A`, `b` as in Approach,
   `lsq_linear(A, b, bounds=(0.0, 1.0), method="bvls")` (weights normalised per component
   first, see Approach); if any entry of the result is not finite, raise `ArithmeticError`
   naming the component's sections (never clamp a nan — `max(0.0, nan)` returns `0.0`);
   then `float(min(1.0, max(0.0, x))) + 0.0` per entry (guards round-off at the bounds; `Modulator.command` rejects
   anything outside `[0, 1]`). Cast every numpy value to `float` before it leaves the
   function so mypy's `warn_return_any` stays clean.
5. `update`: validate as in the table → record each room's `(integral, pi_output)` →
   `demand = {r: room._step(m)}` → `_allocate` (on any exception: write the recorded
   values back into each composed `PIController`'s `_integral`/`_pi_output`, re-raise) →
   commands from each section's modulator → store `duty` → return commands.
6. Read-only properties, `to_dict`, `from_dict` (as in the table; `from_dict` builds via
   `cls(...)` so a subclass round-trips as itself).
7. `allocator/__init__.py`, package `__init__.py`, `__all__` in both.
8. `main()` and `if __name__ == "__main__": main()`.
9. `STRUCTURE.md`, `README.md`.

### Test intents

All allocation tests drive demands directly with `kp=1.0, ki=0.0`, so demand =
`clamp(setpoint − measured)` (e.g. setpoint 21.0, measured 20.9 → 0.1 within round-off), or
call `_allocate` directly. Numeric comparisons use `abs=1e-9` unless stated. The reference
layout is R1 ← HS1 (1.0) + HS2 (0.5); R2 ← HS2 (0.5) + HS3 (1.0); R3 ← HS4 (1.0).

| # | Must prove | Covers |
|---|---|---|
| T1 | Every A1 refusal raises the documented class naming the offender, for both `rooms` and `sections` (share 0, share > 1, shares summing to 1 + 1e-6, unknown room, uncovered room, priority 0 and negative, evenness negative, nan/inf/`bool`/str values, empty mappings, non-str names, wrong room-spec keys); a share sum of exactly 1.0 from `0.1`-style float sums is accepted. | A1 |
| T2 | Layout is read-only: `rooms`, `sections`, `history_length`, `Room.priority`/`evenness` cannot be assigned, and mutating returned copies does not reach the controller. | A1 |
| T3 | A room's `setpoint`/`kp`/`ki` setters raise the same class and message as `PIController`'s for the same bad values, leave the old value on failure, and a valid change alters the very next `update`'s demand exactly as a standalone `PIController` with the same change would. | A2 |
| T4b | A monkeypatched solver that raises (and one returning nan) makes `update` raise and leaves the whole `to_dict()` and every `Room.demand` exactly as before the call; weights of `1e300` and `1e-300` still allocate finite duties equal to the same layout with weights scaled to 1. | A3 |
| T4 | `update` returns exactly the section names with values in `{0.0, 1.0}`; a missing room, an unknown room, a non-mapping, a nan/inf/str/`bool`/huge-int temperature each raise naming it, and leave every room's integral and demand, `duty`, and every section history unchanged (whole `to_dict()` equal before/after); `duty`/`demand` read `None` before the first update and are populated after. | A3 |
| T5 | Evenness 0, feasible demands: delivered heat (`Σ share × duty`) equals demand per room within tolerance — (0.1, 0.6), (0.3, 0.8), (0, 0), and a component with three rooms in a chain; all duties in `[0, 1]`. The closed-form branch: a one-room, one-section component with share 0.5 at demands 0, 0.3 and 0.6 gives duty 0.0, 0.6 and 1.0 exactly; a single section covering two rooms (and a room with one section that also covers another room) goes through least squares, not the closed form. | A4 |
| T6 | Priority: for the conflict layouts (R1, R2 sharing only HS2 with demands (0.2, 0.6); and the reference layout with infeasible demands), raising a room's priority over a sweep never increases its absolute mismatch (allowing 1e-9); with equal shares, evenness 0 and no duty at 0 or 1, the higher-priority room's mismatch is the smaller; the saturated shared-only case (demands 0, 1) and the unequal-share case (one section, shares R1 0.2 / R2 0.8, priorities 2 / 1, demands 1 / 0 → R1 mismatch ≈ 0.889 > R2 ≈ 0.444) are documented exceptions, tested as such. | A5 |
| T7 | Evenness: spread is `Σ (u_s − mean)²` over the room's sections. On the reference layout and on a room served by three sections, sweeping one room's evenness weight upward never increases that room's spread (several demand pairs, other room's weight 0 and > 0); the hungry-R2 case (0.1, 0.6) with R1 = 0.1, R2 = 0 gives HS1 > 0, an HS1–HS2 gap smaller than the weights-0 result's, and both demands met within tolerance (the weights-0 HS1 value itself is not asserted). | A6 |
| T8 | R3/HS4 (share 1.0) yields a command sequence identical (`==`, not approx) to `PIController(kp, ki, "floor_heating", setpoint, history_length=n)` over a long varied measurement sequence including saturation both ways and a mid-run setpoint/gain change, regardless of R1/R2's measurements and evenness weights; also a single-room, single-section allocator. | A7 |
| T9 | `scipy` is in `[project] dependencies`; the package, `heatingsystem.allocator` and `heatingsystem` import; `SectionAllocator`/`Room` are the identical objects from every import path and in both `__all__`; the existing suite passes untouched. | A8 |
| T10 | `to_dict` has exactly the documented shape and order and `json.dumps`-able types, fresh containers; `from_dict(to_dict())` and the JSON round trip give an allocator whose next 50 commands equal the original's, mid-run with partly filled and full windows; every malformed snapshot (missing/unknown keys at each level, bad values, over-long history, invalid layout, non-mapping) raises naming the path and produces nothing; a subclass round-trips as itself. | A9 |

### Coverage

- Every criterion has at least one Public API entry: A1 (constructor, `Room`, read-only
  layout), A2 (`Room` setters), A3 (`update`, `duty`, `Room.demand`), A4–A6 (`update`,
  `duty`), A7 (`update`, `history`), A8 (`pyproject.toml`, the package exports), A9
  (`to_dict`/`from_dict`, `Room.integral`).
- Every criterion has at least one test intent: A1 T1–T2, A2 T3, A3 T4/T4b, A4 T5, A5 T6, A6 T7,
  A7 T8, A8 T9, A9 T10.
- Nothing in the Public API lacks a criterion; `main()` is the repo's module convention.

### Risks

- **No `.venv` in the container, scipy not installed.** Step 3 creates it as in guide 1.
  If the scipy wheel cannot be installed at all, halt — A8 cannot be met.
- **`bvls` round-off.** Duties may land 1e-16 outside `[0, 1]`; guide 4 clamps. If `bvls`
  raises on a degenerate matrix, fall back to `method="trf"` with `tol=1e-12` inside
  `_allocate` and record it in section 3 — not a halt. If T5's tolerance cannot be met
  with either, halt (A4 as stated would be wrong).
- **A5/A6 monotonicity proven for exact minimisers only.** If a sweep in T6/T7 shows a
  violation larger than 1e-7, first tighten the solver tolerance; if it persists, halt — it
  would mean the criterion, not the code, is wrong.
- **mypy and scipy.** scipy ships partial types; with `ignore_missing_imports` any `Any`
  leaking into a return trips `warn_return_any` — cast at the boundary (guide 4). Do not add
  `scipy-stubs` without recording it.
- **STRUCTURE.md split.** `DEVELOPMENT.md` R6 defers the per-subpackage split "until a third
  subpackage arrives" — this is the third. It is **not** done in this round (it changes
  what the stop gate and session brief read, mid-build); STRUCTURE.md stays flat, and R6's
  entry is reworded to say the trigger has now arrived. Step 8 decides whether it is the
  next round.
- **Non-unique minimisers.** With evenness 0 a component can have a whole line of exact
  fits; which one is returned is the solver's choice. A6 says "may", so no test asserts
  the evenness-0 point — only that it fits exactly (T5) and that a positive weight narrows
  the gap relative to it (T7).
- **`Modulator._from_dict` called from another module.** Deliberate for this round:
  `SectionAllocator` is the first caller restoring a standalone `Modulator`, which is
  `DEVELOPMENT.md` round-4 R7's trigger. Not promoted here; R7's entry is reworded to say
  its trigger has arrived, alongside R6's.
- **`Room` exposing `PIController`'s `mode`/`fixed_output`.** It must not: only the
  properties in the table are public; the composed controller is `_pi`.


### Critique

`plan-critic` findings and what was done:

1. A5 clause 2 false for unequal shares → **applied**, put to the user: scoped to equal
   shares, zero evenness, no saturation; counterexample tested as an exception (T6).
2. A6 evenness-0 point is the solver's tie-break → **applied**, put to the user: softened
   to "may"; T7 no longer asserts it; Risks updated.
3. "Spread" undefined → **applied**: defined as `Σ (u_s − mean)²` in Approach and T7; a
   three-section room added to T7.
4. Partial state on solver raise; nan clamped to 0; weight overflow → **applied**: per-
   component weight normalisation, non-finite result raises `ArithmeticError`, room state
   restored on any allocation failure (guide 4–5, `update` row, T4b).
5. `Room` constructor defaults → **applied**: keywords required, construction internal.
6. `Modulator._from_dict` is R7's trigger → **applied**: Risks entry; R7 reworded.
7. `scipy>=1.11` has no 3.13 wheels → **applied**: `scipy>=1.14,<2`, `numpy>=2` declared.
8. Closed-form branch untested → **applied**: T5 rows for share 0.5 and for shared
   sections taking the least-squares path.

---

## 3. Implementation notes

The Public API table is unchanged; the code matches it. Departures and decisions the plan
left open:

- `lsq_linear` is called with `# type: ignore[operator]`: mypy resolves
  `scipy.optimize.lsq_linear` to the submodule rather than the function ("Module not
  callable"). The reason is on the same line; `scipy-stubs` was not added (Risks).
- No `method="trf"` fallback was written: `bvls` did not raise on any layout tried (the
  reference layout, a chain, a shared-only section), so the Risks fallback is not needed
  yet. If step 5 finds a degenerate matrix that raises, add it then and record it here.
- A private `_Component` holder class stores each component's rooms, sections, stacked
  matrix (`None` for the closed-form case), share and weight `scale`. The matrix is built
  once at construction; `_allocate` only builds the demand vector.
- Room spec key checks reuse `_validation.snapshot_mapping`, prefixed `rooms['R1']: `, so
  their message reads "rooms['R1']: snapshot is missing keys [...]". An error from
  building a `Room` (including the shared `kp`/`ki`/`setpoint` defaults) is prefixed
  `rooms['R1'].`, e.g. `rooms['R1'].kp must be a real number...`.
- `from_dict` prefixes mapping-shape errors `rooms['R1']: ` / `sections['HS1']: ` and
  value errors `rooms['R1'].` / `sections['HS1'].` (the modulator's own message already
  starts `history...`, so the section path reads `sections['HS2'].history[0] ...`).
- `update` restores room state on any `BaseException` from the demand/allocation block.
- `DEVELOPMENT.md` R6 and R7 reworded to say their triggers have arrived (third
  subpackage; first standalone `Modulator` restore); neither is done. `STRUCTURE.md` stays
  flat, and `tests/test_allocator.py` is not yet listed there (step 5 adds it with the
  file).
- Environment: `.venv` created with Python 3.13.16; scipy 1.18.1, numpy 2.5.3 installed.

Step 5 production changes (bugs the tests and the two readers found; all in
`allocator.py`, signatures unchanged):

- **Weight floor.** Dividing by the component's largest weight is exact only in real
  arithmetic: a room whose priority was about 1e-15 of another's (or a fit row dwarfed by an
  evenness weight) was dropped by `bvls`' rank cut, so its feasible demand stopped being met
  (priority 1e-30 left R1 at 0.12 instead of 0.1; one room with evenness 1e40 returned duties
  (0, 0)). The row weight is now `sqrt(max(weight / scale, 1e-12))`. Feasible demands are met
  to about 1e-10 at any ratio; the minimiser changes only for a weight more than 1e12 below
  the largest in its component.
- **Solver status.** `bvls` caps iterations at the number of variables and can report status 0
  on a problem it solved; `max_iter` is now 20 per section and a status below 1 raises
  `ArithmeticError` (as for a non-finite result), restoring every room.
- **Shared gain errors.** A bad top-level `kp`/`ki`/`setpoint` used to be reported as
  `rooms['R1'].kp`. They are now validated first through a probe `PIController` and raised
  exactly as `PIController` raises them, naming the argument. This moves the shared gains
  ahead of the room checks in the validation order.
- **`from_dict`.** A non-mapping `shares` is reported as `sections['HS1']['shares']`; the
  docstring now says layout errors keep the constructor's own path.

---

## 4. Verification log

| Check | Result |
|---|---|
| `ruff check .` | All checks passed! |
| `ruff format --check .` | 52 files already formatted |
| `mypy` | Success: no issues found in 20 source files |
| `pytest` | 730 passed (existing suite; `tests/test_allocator.py` is step 5's) |
| Plan completeness | every signature in the Public API table exists as written (checked with `inspect`: `Room` and its 8 properties, `SectionAllocator.__init__`, `update`, `rooms`, `sections`, `history_length`, `duty`, `history`, `to_dict`, `from_dict`, `main`); no Missing, Deviation or Unplanned. `Room`, `SectionAllocator` in both `__all__`; `pyproject.toml` declares `numpy>=2`, `scipy>=1.14,<2`. |
| `STRUCTURE.md` | in sync after four auditor edits (below) |
| `python -m heatingsystem.allocator.allocator` | exit 0; six labelled sections (hungry R2 at evenness 0 vs 0.1 giving HS1 0.0 vs 0.067, priority conflict, setpoint change, JSON round trip with identical commands, invalid layout refused). Showcase is in the required three-phase form. Expected `RuntimeWarning` only. |

Auditor findings (all four applied, each checked against the code as it stands):

1. `from_dict` row named private `Modulator._from_dict`: reworded to "restored into a fresh floor-heating `Modulator`".
2. Purpose paragraph named private `_allocate`: reworded to private helpers omitted per convention.
3. `_validation.py` purpose was stale: now names `Room`, `SectionAllocator` and `allocator.py` as callers.
4. `update` row named no exceptions: now lists `TypeError`/`ValueError`/`OverflowError` and `ArithmeticError` plus the restore and no-write guarantees (matches the code's `Raises:`).

Left alone: older test sections naming private names (`tests/test_modulator.py`,
`tests/test_pi_controller.py`) describe what those tests target; not this round's.
Step 5 must add a `### tests/test_allocator.py` entry.

No code changed in this step.

---

## 5. Test log

`tests/test_allocator.py`: 280 test cases (93 test functions), all passing; whole suite
1009 passed; ruff, format and mypy clean.

| Intent | Test names | Result |
|---|---|---|
| T1 (A1 refusals) | `test_construction_refuses_a_bad_share_naming_its_path`, `..._a_share_sum_above_one...`, `test_construction_accepts_share_sums_that_are_one_up_to_float_error`, `test_share_sum_tolerance_boundary_is_one_nanounit`, `..._unknown_room`, `..._uncovered_room...`, `..._a_bad_priority...`, `..._a_bad_evenness...`, `test_construction_accepts_the_edges_of_every_range`, `..._malformed_rooms_argument`, `..._malformed_sections_argument`, `test_error_order_...`, `test_bad_shared_gain_matches_pi_controller_and_names_the_argument`, `test_construction_refuses_a_bad_history_length_like_pi_controller`, `test_history_length_edges_...`, name-edge and aliasing tests | pass |
| T2 (read-only) | `test_layout_and_state_attributes_cannot_be_assigned`, `test_rooms_view_rejects_item_assignment_and_deletion`, `test_returned_copies_do_not_reach_the_controller`, `test_room_does_not_expose_the_pi_controller_surface`, `test_to_dict_containers_are_fresh_and_do_not_reach_back` | pass |
| T3 (A2 setters) | `test_room_setters_match_pi_controller_and_keep_the_old_value`, `..._accept_int_and_fraction...`, `test_room_setting_change_takes_effect_on_the_next_update_like_a_twin`, `test_a_setting_change_leaves_other_rooms_and_state_untouched`, `test_room_numeric_setters_normalise_negative_zero` | pass |
| T4 (A3 update) | `test_update_returns_exactly_the_section_names_with_binary_values`, `..._fresh_dict...`, `test_duty_and_demand_are_none_before_...`, `test_update_refuses_bad_measurements_and_changes_nothing` (16 cases), `test_update_reports_a_missing_room_before_a_bad_value`, `..._does_not_mutate...`, `..._numeric_variants...`, `-0.0` and exact `+0.0` duty tests | pass |
| T4b (solver failure) | `test_update_restores_every_room_and_keeps_the_previous_duty_on_solver_failure` (nan, inf, status 0, status -1, ValueError, LinAlgError), `test_update_failure_before_the_first_update_leaves_duty_none`, `test_update_restores_state_when_a_modulator_rejects_the_duty`, `test_extreme_but_uniform_weights_allocate_like_unit_weights` | pass; status-0 and status -1 cases failed before the status check was added |
| T5 (A4) | `test_feasible_demands_are_delivered_exactly_with_evenness_zero`, `test_the_documented_example_...`, chain tests (3 rooms, 60 rooms), closed-form tests, `test_shared_components_use_least_squares_not_the_closed_form`, sqrt-priority weighting, rank-deficient and tiny-share tests, weight-floor tests (`test_feasible_demands_survive_an_extreme_priority_ratio`, evenness-ratio and inert-evenness tests) | pass; the extreme-ratio cases failed (1.2e-2 off) before the weight floor |
| T6 (A5) | `test_raising_priority_never_increases_that_rooms_mismatch_shared_section`, `test_the_higher_priority_room_ends_with_the_smaller_mismatch`, `..._trade_off...`, sweep over four demand pairs, `test_documented_exception_saturated_section_ties_the_mismatches`, `test_documented_exception_unequal_shares_favour_the_larger_share`, `test_signed_mismatch_can_grow_when_priority_rises` | pass; uses absolute mismatch (see Halted) |
| T7 (A6) | `test_raising_evenness_never_widens_the_rooms_spread_reference_layout`, `..._three_section_room`, `test_three_section_spread_is_...`, `test_hungry_r2_with_r1_evenness_meets_both_demands_evenly`, `test_evenness_on_a_shared_section_is_a_real_trade_off` | pass |
| T8 (A7) | `test_dedicated_room_produces_the_command_sequence_of_a_standalone_controller` (windows 1, 6, 24, evenness 0 and large), `..._through_the_finite_guard`, `test_a_single_room_single_section_allocator_...`, `test_equivalence_needs_a_dedicated_share_of_one`, `test_other_rooms_do_not_leak_...` | pass (`==`, not approx) |
| T9 (A8) | `test_scipy_and_numpy_are_declared_runtime_dependencies`, `test_package_and_models_import_together`, `test_public_names_are_identical_across_every_import_path`; existing suite untouched and green | pass |
| T10 (A9) | `test_to_dict_has_exactly_the_documented_shape_and_order`, `test_a_restored_allocator_produces_the_same_next_fifty_commands` (direct/JSON x 0/3/30 steps), `test_from_dict_refuses_a_malformed_snapshot_naming_the_path` (35 cases), non-mapping, missing-before-unknown, mixed-type unknown keys, mapping types, subclass, fractional history, failed restore | pass |
| showcase | `test_main_runs_and_reports_each_section`, `test_row_weight_never_vanishes_or_overflows` | pass |

Edge cases considered and deliberately skipped, with reasons:

- Idempotency of `update`: the windows and integrals advance by design.
- Very long strings as names: names are only dict keys and appear in reprs.
- The composed `PIController`'s one-slot radiator history is not restored when an allocation
  fails: nothing public reads it, so it is unobservable (note for any future `Room.history`).
- A 100-room chain as proposed by the input-space reader: its demands were infeasible (the
  exact solution needs duties from -0.7 to 1.7), so a feasible 60-room chain is used instead.
- A `from_dict` history of non-binary floor-heating values is accepted (the `Modulator`
  contract) and pinned by a test, not rejected.
- `Room` constructed directly with an empty name: unreachable through the allocator, which
  rejects empty names first; construction is documented as internal.

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

Halted at step 5 on a question about section 1's wording. The suite is written, green and
committed (1009 passed); four criteria read differently from what the code, the plan's test
intents and the concept's own examples do. Tests follow the plan's test intents (T6 absolute
mismatch, T8 share 1.0, share-sum tolerance 1e-9); only section 1 needs the user's decision.

1. **A5 (signed mismatch).** A5 defines mismatch as "demand - delivered heat" and says raising
   a room's priority "never increases" it. Read as signed that is false: shared section
   `HS1 = {R1: 0.5, R2: 0.5}`, demands 0.2 / 0.6, R1 priority 1 -> 2 moves R1's signed mismatch
   from -0.200 (over-served) to -0.133, an increase (checked by
   `test_signed_mismatch_can_grow_when_priority_rises`). It is true of the absolute (or
   squared) mismatch, which T6 uses. **Question:** reword A5 to "absolute mismatch
   |demand - delivered|"?
2. **A7 (dedicated share).** A7 says a room "with exactly one dedicated section" reproduces a
   standalone `PIController`. That holds only for share 1.0 (R3 with HS4 in every example). With
   a dedicated share of 0.5 the section is asked for `min(1, demand / 0.5)`, twice the standalone
   duty, by the concept's own stage 2 (pinned by `test_equivalence_needs_a_dedicated_share_of_one`).
   **Question:** reword A7 to "exactly one dedicated section with share 1.0", or should a
   dedicated section with share below 1 also reproduce the standalone controller (which would
   mean the allocator ignores the share there, contradicting A4)?
3. **A1 (share-sum tolerance).** A1 says a section whose shares sum above 1 raises; the plan
   (guide 3.2) accepts sums up to `1 + 1e-9` so `0.1`-style float sums pass. **Question:** state
   the tolerance in A1 ("above 1 by more than 1e-9")?
4. **A4 (extreme priority ratios, informational).** Found and fixed as a code defect (see
   section 3, weight floor): a weight more than 1e12 below the largest in its connected
   component is treated as 1e-12 of it. A4's "exact whenever one exists" now holds at any ratio
   to about 1e-10, but a trade-off between rooms whose priorities differ by more than 1e12 is
   decided as if they differed by exactly 1e12. **Question:** is that acceptable, or should
   priorities beyond a ratio be refused at construction (an A1 change)?

On the answers, `/build` resumes at step 5's close: amend section 1 as decided (no code or
test change is expected for 1-3), mark step 5 done, set the marker to step 6.

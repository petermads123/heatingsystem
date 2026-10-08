# SectionAllocator ready for Home Assistant — a room without a temperature

<!-- claude-plan step=5 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/allocator-home-assistant` |
| Round | 2 |
| Branch | `feat/allocator-home-assistant` |
| Started | 2026-10-08 |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
| 2 | Plan | `/plan` | with the user | done |
| 3 | Implement | `/implement` | in `/build` | done |
| 4 | Verify | `/verify` | in `/build` | done |
| 5 | Test | `/test` | in `/build` | pending |
| 6 | Concept check | `/concept-check` | in `/build` | pending |
| 7 | Ship | `/ship` | in `/build` | pending |
| 8 | Recommend | `/recommend` | with the user | pending |
| 9 | Pull request | `/create-pr` | with the user | pending |
| 10 | Review | `/watch-pr` | on the pull request | pending |

Statuses: `pending`, `in progress`, `done`.

Gates: the user asked (2026-10-08, after PR #8 opened) to "continue with round 2 on the same
PR and the rest of the rounds automatically from there". That is their acceptance of steps 1
and 2 for rounds 2–4 in advance; the concept and plan below are recorded and criticised as
usual, and the build still halts on anything that would amend section 1.

## Builds on

| Round | File | What it delivered |
|---|---|---|
| 1 | `01-coverage-and-holds.md` | Room-coverage semantics with per-room validation and normalisation; `hold`/`holds` with held sections as constants; snapshot `coverage`/`history`/`hold`; version 1.1.0 |

This round is not a step 8 recommendation: it is Change 3 of the spec, agreed in round 1's
section 1 ("Later rounds on this branch") as round 2:

> Round 2 (Change 3): `None` accepted as a room's temperature; that room's PI takes no step
> (integral unchanged, demand stays at its last value, 0.0 before the first) and the
> allocation runs for every room.

What is already on the branch that this round must not break: round 1's A1–A11 — in
particular A3 (one-section room ≡ standalone `PIController`), A8 (PIs unaffected by holds),
A9 (a raising `update` changes nothing) and A10 (snapshot shape, unchanged by this round).

---

## 1. Concept

### What this is

`SectionAllocator.update` accepts `None` as a room's temperature, meaning "no reading this
step". That room's PI takes no step: its integral and its last PI demand are untouched. The
allocation still runs for every room, using that room's last demand — or 0.0 if the room has
never had a reading (fresh, or restored by `from_dict`). Every other room steps normally, and
every section still issues a command. The caller decides when a dead sensor has gone on too
long and can then hold the room's sections with `hold`.

### Why it is worth building

One dead sensor today stops every room's step, because `update` needs a finite temperature
for every room. The consumer's workaround — feeding the setpoint as the measurement — is
close to "no step" but not the same: it still runs the PI (the integral is held only because
the error is zero, and the P term drops to zero, changing the demand).

### Inputs and outputs

- `update(measured: Mapping[str, float | None]) -> dict[str, float]` — the value type widens
  to admit `None`; the return is unchanged.
- A room's key must still be present: `None` is an explicit "no reading", a missing key is
  still the `ValueError` it is today. Every other refusal (non-mapping, unknown room,
  non-numeric, `bool`, non-finite, overflow) is unchanged.
- `Room.demand` keeps its meaning — the clamped demand of the room's last PI step — so it
  stays at its last value through `None` steps and stays `None` until the room's first real
  reading. The 0.0 used by the allocation before a first reading is internal, not reported.

### How it connects to the rest of the repo

Only `allocator.py` (`update`, `Room`'s private step). `PIController` is not changed: a
`None` room simply does not call it. Holds, snapshots and the allocation are untouched.

### Explicitly out of scope

- Counting missing readings, timeouts, or closing a room's sections automatically (the
  caller's job, with `hold`).
- `None` anywhere else (setpoints, gains, `PIController.update`).
- Persisting the last demand in the snapshot: after `from_dict` a `None` room allocates with
  0.0 until its first reading, as `demand` is `None` there by round 1's A10.
- Changing the layout (round 3) and P/I terms (round 4).

### Acceptance criteria

| # | The finished feature... |
|---|---|
| B1 | Given `None` for a room, that room's integral and `demand` are exactly what they were before the call, while every other room steps exactly as it would without the `None` (twin comparison). |
| B2 | The allocation uses a `None` room's last demand: on the round-1 E layout (HS1–HS5 over R1–R4, as in `main()`, `kp=1.0`, `ki=0.0`, other rooms at the setpoint), a step with R3 at demand 0.5 followed by a step with R3 `None` (other rooms unchanged) allocates E1 again (HS3 1.0, HS4 ≈ 0.215). Before the room's first reading it uses 0.0, and `demand` stays `None`. |
| B3 | A room's integral and demand sequence over a run with `None` steps interleaved equals a standalone `PIController` (same gains, setpoint) fed only that room's non-`None` readings. |
| B4 | `None` for every room is accepted (each room keeps its last demand, every section still gets a command, holds still apply); a missing key still raises `ValueError`; every other `update` refusal is unchanged in class and message. |
| B5 | A raising `update` with some rooms `None` (bad temperature elsewhere, or a failing solver) leaves every room, hold, window and `duty` unchanged. |
| B6 | Docstrings, `README.md` and `STRUCTURE.md` describe `None` readings, and round 1's A1–A11 still hold. |

### Open questions

None. Decided: `Room.demand` stays `None` before a first reading (it reports the PI, not the
allocation's input); the last demand is not added to the snapshot.

---

## 2. Plan

### Approach

Minimal and local. In `update`, after the existing missing/unknown checks, validate each
temperature only when it is not `None` (`_validation.finite` as today, same path). In the
`try`, a `None` room does not call `_step`; its allocation demand is `room.demand` or `0.0`
when that is `None`. Nothing else moves: the save/restore block already covers integrals and
PI outputs, and a `None` room's are untouched anyway. Rejected: feeding the setpoint as the
measurement internally (the workaround the concept exists to replace); a separate
`skip`/`missing` argument (a second way to say the same thing, and the consumer already builds
the mapping per room).

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/heatingsystem/allocator/allocator.py` | changed | `update` accepts `None`; docstrings; showcase gets a `None` case |
| `tests/test_allocator.py` | changed | B1–B5 tests |
| `README.md` | changed | "SectionAllocator usage": a `None` reading |
| `STRUCTURE.md` | changed | `update` row and test summary |

### Public API

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `SectionAllocator.update(measured: Mapping[str, float \| None]) -> dict[str, float]` | `allocator.py` | `None` = no reading: no PI step for that room, allocation uses its last demand (0.0 before its first). Missing/unknown checks first as today; then each non-`None` value through `_validation.finite(f"measured[{name!r}]", ...)`. Docstring `Args`/`Raises` updated. | B1–B5 |
| `Room.demand -> float \| None` | `allocator.py` | Unchanged signature; docstring says it is the last PI step's demand, unchanged by a `None` reading. | B2 |
| `main() -> None` | `allocator.py` | Showcase gains one case: R3 `None` after a reading, duty unchanged from the previous step. | B6 |

### Implementation guide

1. `update`: build `temperatures: dict[str, float | None]`, `None` passed through, others via
   `_validation.finite` (same order, same paths).
2. Inside the `try`: `demand[name] = room._step(t)` if `t is not None`, else
   `room.demand if room.demand is not None else 0.0`. In the same commit, remove only the
   `None` row from the refusal parametrize list in `tests/test_allocator.py` (around line 691,
   `{"R1": 20.0, "R2": 20.0, "R3": None}` → `TypeError`) and add a positive test in its place:
   same allocator, R3 `None`, no raise, R3's `integral`/`demand` unchanged. Record in section 3
   that B4 reversed this case (a replacement, not a deletion).
3. Docstrings: `update` Args (`None` = no reading) and the `TypeError` line (a `None` is no
   longer refused); `Room.demand`.
4. `main()`: one labelled case after E1 — the same allocator, R3 `None`, print duty.
5. README usage section: one paragraph and a line of the example; `STRUCTURE.md` `update` row.
6. Tests (step 5 extends): B1–B5 as below. The one reversed refusal case is already
   replaced at entry 2, so the tree stays green from step 3.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | Twin = same allocator history; on the `None` step the twin gets the same readings for every other room and any finite reading for the `None` room. Every other room's `integral` and `demand` equal the twin's; the `None` room's `integral` and `demand` are identical (`==`, `is None` when `None`) to before the call. Duty is not compared (B2's job). | B1 |
| T2 | E layout (not the test file's `ref()`), `kp=1.0`, `ki=0.0`, R1/R2/R4 at the setpoint, R3 20.5 then `None`: duty both steps HS3 1.0, HS4 ≈ 0.2154, HS5 0.0; fresh allocator with R3 `None` → R3 allocated as demand 0.0, `demand` is `None`; same after `from_dict`. | B2 |
| T3 | Interleaved `None` run equals a standalone `PIController` fed only the real readings (integral and `pi_output` per real step). | B3 |
| T4 | All rooms `None`; with holds; missing key still `ValueError`; every other refusal unchanged (class and message). | B4 |
| T5 | Raising `update` with `None` rooms present (NaN elsewhere; failing solver) leaves `to_dict()`, `duty`, `holds`, `history` unchanged. | B5 |
| T6 | Showcase runs; round-1 suite still green. | B6 |

Coverage: B1–B6 each have a Public API row and a test intent; every row cites a criterion.

### Risks

- The existing test passing `None` expecting `TypeError` reverses under this round — replaced
  at guide entry 2; not a halt.
- If anything in the round-1 suite depends on `None` being refused beyond those tests, it is
  the same reversal; not a halt.

### Critique

plan-critic (verdict: accept with changes; approach confirmed against the code):

1. B2/T2 underspecified the layout and gains (the test file's `ref()` gives HS4 0.5) —
   **applied**: B2 and T2 name the E layout, `kp=1.0`, `ki=0.0`, other rooms at the setpoint.
   B2's substance is unchanged; this pins the existing example.
2. T1 did not say what the twin is fed — **applied**: T1 rewritten.
3. The reversed refusal row goes red at step 3 — **applied**: moved to guide entry 2 with a
   positive replacement test.

---

## 3. Implementation notes

Guide entries 1-5 implemented as planned; no deviation from the Public API table.

- B4 reversed one refusal: `tests/test_allocator.py` parametrize row
  `{"R1": 20.0, "R2": 20.0, "R3": None}` -> `TypeError` was removed and replaced by
  `test_update_accepts_none_as_no_reading_and_leaves_that_room_untouched` (R3 `None`, no raise,
  R3's `integral`/`demand` unchanged). A replacement, not a deletion.
- `update` builds `temperatures: dict[str, float | None]`; inside the `try` a `None` room skips
  `_step` and allocates `room.demand` or 0.0. The save/restore block is unchanged.
- Docstrings (`update`, `Room.demand`), `main()` (a `None` R3 case after the release), README
  ("No reading" paragraph and a line in the example) and `STRUCTURE.md` (`update`, `Room.demand`,
  `main` rows) updated.
- Showcase output: R3 demand 0.5, duty HS3 1.0, HS4 0.2154 (E1 again).
- Local run: ruff and format clean, mypy clean after dropping a now-unused `type: ignore`,
  pytest 1517 passed.

---

## 4. Verification log

| Check | Result |
|---|---|
| `ruff check .` | All checks passed! |
| `ruff format --check .` | 55 files already formatted |
| `mypy` | Success: no issues found in 21 source files |
| `pytest` | 1517 passed |
| Plan completeness | every signature in the Public API table exists as written: `SectionAllocator.update(measured: Mapping[str, float \| None]) -> dict[str, float]`, `Room.demand -> float \| None`, `main() -> None`; no missing, deviating or unplanned surface |
| `STRUCTURE.md` | in sync after one edit (below) |
| `python -m heatingsystem.allocator.allocator` | runs; expected RuntimeWarning; the "R3 has no reading" case prints R3 demand 0.5 and duty HS3 1.0, HS4 0.2154 (E1 again) |

structure-auditor: allocator.py entries matched the code; one finding, applied: the
`tests/test_allocator.py` summary now says a `None` temperature is accepted as no reading
(no raise, that room's `integral` and `demand` unchanged). Step 5 should extend that
sentence if it adds T1-T5.

---|---|
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


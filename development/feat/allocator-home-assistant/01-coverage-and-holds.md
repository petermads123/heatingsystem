# SectionAllocator ready for Home Assistant — coverage and holds

<!-- claude-plan step=2 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/allocator-home-assistant` |
| Round | 1 |
| Branch | `feat/allocator-home-assistant` |
| Started | 2026-10-08 |

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

Nothing — this is the first round. (The allocator itself is 1.0.0, merged in PR #7 from
`feat/section-allocator`; its round file `development/feat/section-allocator/01-section-allocator.md`
holds the criteria A1–A12 this round restates.)

Source: the change spec for the Home Assistant consumer (Changes 1–5). This round takes
Changes 1 and 2; Changes 3, 4 and 5 are agreed as rounds 2, 3 and 4 on this branch.

---

## 1. Concept

### What this is

`SectionAllocator.sections[s][r]` changes meaning: it is now the fraction of room *r*'s
floor heating that section *s* provides, in `(0, 1]` (it was the share of *s*'s heat that
reaches *r*). Validation moves from per section to **per room**: the coverages of every room
sum to at most 1 (tolerance 1e-9); a section may cover several rooms with any sum. Internally
each room's coverages are **normalised by its controlled total**, so a demand of 1.0 means
"every loop of this room fully on" and the uncovered remainder (R1's other half) is an outside
disturbance like the weather. The old meaning is replaced, not kept behind a keyword (nothing
has adopted 1.0.0's). Ships as version **1.1.0**.

Each section can be **held** at a fixed level: `hold(section, level | None)`, with a
read-only `holds` view. A held section is a constant in the allocation — its contribution
(normalised coverage × level) is subtracted from each room's demand and its column leaves the
matrix (`lsq_linear` refuses equal bounds, so bounds cannot pin it); evenness rows use its
fixed level; a component whose sections are all held calls no solver. The held section's
floor-heating `Modulator` gets `fixed_output = level`, so its window records the commands the
relay actually got, and `duty` reports the level. Every room's PI keeps running during a hold,
as `PIController` does under `fixed_output`; the burst after release is documented.

### Why it is worth building

The consumer's real layout (HS4 covers 70 % of R3 and 40 % of R4) is refused today, and even
with converted shares the per-section unit lets the solver believe a small loop can heat a
whole room (HS3 at 0.5 instead of 1.0 plus HS4 ≈ 0.215). And the consumer must hold relays —
automatic off, fixed open/closed, the circulation-pump lock — which today cannot be expressed
without corrupting the duty tracking and without the other sections compensating.

### Inputs and outputs

- Constructor: same shape, `sections: Mapping[str, Mapping[str, float]]`, values now room
  coverages.
- New `SectionAllocator.hold(section: str, level: float | None) -> None`: `level` validated
  like `Modulator.fixed_output` (`[0, 1]`, same `TypeError`/`ValueError`/`OverflowError`
  classes, message naming the section); `None` releases; an unknown section raises
  `ValueError` naming it; a raising call changes nothing.
- New `SectionAllocator.holds -> dict[str, float | None]`: read-only, a fresh copy, every
  section present.
- `update`, `duty`, `history`, `rooms`, `sections` keep their signatures; `sections` returns
  the coverages as given (not normalised).
- Snapshot: each section's entry becomes `coverage` (as given), `history`, `hold`. The key is
  renamed from `shares` on purpose, so a 1.0.0 snapshot is refused (missing/unknown key)
  rather than silently misread under the new meaning.

Decided along the way (stated, not asked):

- Evenness unchanged: `spread_r = sum((u_s - mean)**2)` over the room's sections, unweighted;
  a held section enters with its fixed level.
- A one-room, one-section component keeps a closed form, now `min(1, demand)` since its
  normalised coefficient is 1.
- The version bump to 1.1.0 lands in this round; rounds 2–4 stay within 1.1.0.

### How it connects to the rest of the repo

Only `src/heatingsystem/allocator/allocator.py` changes (plus tests, `pyproject.toml`
version, `STRUCTURE.md`, `README.md`). It reuses `PIController`, `Modulator` and
`heatingsystem._validation` unchanged.

### Explicitly out of scope

- Any change to `PIController` or `Modulator` behaviour; a `dt` parameter.
- Making scipy optional or the allocator import lazy.
- Room min/max limits, prices, cost-aware heating.
- `None` temperatures (round 2), changing the layout keeping state (round 3), P and I terms
  per room and on `PIController` (round 4).

### Worked examples (reference layout)

Layout HS1 {R1 0.5}, HS2 {R2 1.0}, HS3 {R3 0.3}, HS4 {R3 0.7, R4 0.4}, HS5 {R4 0.6};
priorities 1, evenness 0 unless stated. Computed with `lsq_linear(method="bvls")`.

| # | Demand | Allocation |
|---|---|---|
| E1 | R3 0.5, R4 0 | HS3 1.0, HS4 ≈ 0.215, HS5 0.0 |
| E2 | as E1, HS3 held at 0.0 | HS4 ≈ 0.538, HS5 0.0 |
| E3 | R1 0.4 | HS1 0.4 (normalised; does not saturate at 0.5) |
| E4 | R3 0.5, R4 0.5, evenness R3 = R4 = 1 | HS3 = HS4 = HS5 = 0.5 |
| E5 | HS3 1.0, HS4 0.0, HS5 0.5 all held | no solver; heat R3 0.3, R4 0.3 |

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | Constructs the reference layout. A room whose coverages sum above 1 + 1e-9 raises `ValueError` naming the room and the sum; a section covering several rooms with a sum above 1 is accepted; a coverage outside `(0, 1]` still raises naming its path (`sections['HS4']['R3']`); the other 1.0.0 layout refusals (unknown or uncovered room, types, empty mappings) are unchanged. |
| A2 | Normalises by each room's controlled total: E1 and E3 above come out as stated. |
| A3 | A room served by one section of any coverage in `(0, 1]` produces exactly (`==`) the command sequence of a standalone `PIController(kp, ki, "floor_heating", setpoint, history_length=n)`, across setpoint and gain changes (1.0.0's A7, generalised). |
| A4 | Restates 1.0.0's A4–A6 and A10–A12 with `h_r = sum over s of normalised coverage × u_s`: the cost's KKT conditions and an independent solve hold on random coverage layouts, scaling every priority leaves the allocation unchanged, A5/A6 hold as scoped in 1.0.0, and the worked examples are recomputed in the new terms (E1–E5 above included). |
| A5 | `hold(section, level)` validates `level` like `fixed_output` (same exception classes, message naming the section), raises `ValueError` naming an unknown section, releases on `None`, and a raising call changes nothing; `holds` reports every section's level or `None` as a fresh dict. |
| A6 | A held section's command is its level at every `update` whatever the demand, its window records those commands and `duty` reports the level; the free sections compensate (E2: HS4 ≈ 0.538); after release the next `update` allocates freely again (E1). |
| A7 | A component whose sections are all held calls no solver (shown with a `lsq_linear` that raises). |
| A8 | Every room's PI keeps running during a hold: its integral and demand equal an un-held twin's, step for step. |
| A9 | A raising `update` (bad measurement or solver failure) leaves holds, windows, rooms and `duty` unchanged. |
| A10 | `to_dict` gives each section `coverage` (as given), `history`, `hold`; `from_dict` restores holds; a restored allocator issues the identical next 50 commands, directly and through JSON, with a hold set; a 1.0.0-shaped snapshot (`shares`) is refused naming the key. |
| A11 | The package version is 1.1.0, and `STRUCTURE.md` and `README.md` describe the coverage meaning, normalisation and `hold`, including the demand burst after a release. |

### Later rounds on this branch

- Round 2 (Change 3): `None` accepted as a room's temperature; that room's PI takes no step
  (integral unchanged, demand stays at its last value, 0.0 before the first) and the
  allocation runs for every room.
- Round 3 (Change 4): a method returning a new allocator with a new layout and/or
  `history_length`, carrying over each room's settings and integral, and each section's window
  (trimmed to its newest slots) and hold, where names match; `duty`/`demand` not reset to `None`.
- Round 4 (Change 5): `error`, `p_term`, `i_term` of the last update on `Room` and on
  `PIController` (new read-only properties; no behaviour change).

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


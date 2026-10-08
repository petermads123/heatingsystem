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
| A6 | A held section's duty is its level at every `update` whatever the demand, and `duty` reports it; its commands are the floor-heating modulation of that level (exactly the level for 0.0 and 1.0, a 0/1 pattern averaging the level over a window otherwise) and its window records them; the free sections compensate (E2: HS4 ≈ 0.538); after release the next `update` allocates freely again (E1). |
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

Keep the module and its shape; change what the matrix entries mean and let the held columns
leave the problem at solve time. At construction the coverages are validated per entry and
per room, and each room's coefficients are stored normalised (`c̃_rs = c_rs / Σ_s c_rs`)
in a private table the components are built from. Each multi-variable component still
precomputes its full stacked matrix (demand rows, then evenness rows) over **all** its
sections. At `update`, the held sections' columns are split off: the right-hand side is
`b = [w_r·d_r ; 0] − A[:, held] @ levels` and the solver sees `A[:, free]` only — this one
subtraction covers the demand rows and the evenness rows alike, so evenness rows "use the
held level" without a second code path. No free column → no solver. The one-room,
one-section closed form becomes `min(1, demand)` (its normalised coefficient is exactly 1.0,
since `x / x == 1.0` for any finite non-zero float), or the level when held. Holds live in
one place, the section's own `Modulator.fixed_output`; `holds` reads them back, so the
window records the held commands by construction.

Rejected: rebuilding (or caching per held set) each component's matrix from scratch when
holds change — more code, a cache to invalidate, same numbers. Pinning a held section with
near-equal bounds (`[level, level + ε]`) — `lsq_linear` refuses equal bounds, and an ε
makes the held duty and every compensation inexact, which breaks A6's "the level" and A8's
twin equality. Storing holds in a separate dict beside the modulators — two sources of truth
for one fact.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/heatingsystem/allocator/allocator.py` | changed | Coverage semantics, per-room validation, normalisation, `hold`/`holds`, held columns at solve time, snapshot keys, docstrings, showcase |
| `tests/test_allocator.py` | changed | Migrate every layout to the coverage meaning; add the hold, normalisation and snapshot tests |
| `pyproject.toml` | changed | `version = "1.1.0"` |
| `README.md` | changed | "SectionAllocator usage": coverage meaning, normalisation, `hold`, burst after release, snapshot keys |
| `STRUCTURE.md` | changed | Allocator entry and its test summary |

No new module: everything here is the allocator's own state.

### Public API

Unchanged signatures are listed where their contract changes.

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `SectionAllocator(rooms: Mapping[str, Mapping[str, float]], sections: Mapping[str, Mapping[str, float]], *, kp: float = 0.3, ki: float = 0.015, setpoint: float = 21.0, history_length: int = 24)` | `allocator.py` | Same signature; `sections[s][r]` is now room *r*'s coverage by *s*. Validation order: shared gains; rooms; each section entry (name, mapping, non-empty, known room, coverage finite and in `(0, 1]`, path `sections['HS4']['R3']`); uncovered rooms (`rooms [...] are covered by no section.`, as 1.0.0); then per room, in room order, `ValueError: rooms['R3'] coverages sum to 1.1, more than 1.` when the `math.fsum` exceeds `1 + 1e-9`; then `history_length`. The 1.0.0 per-section sum check is removed. | A1, A2 |
| `SectionAllocator.sections -> dict[str, dict[str, float]]` | `allocator.py` | Fresh deep copy of the coverages as given (as floats), not normalised. | A1, A10 |
| `SectionAllocator.hold(section: str, level: float \| None) -> None` | `allocator.py` | Checks `section` first: a non-`str` raises `TypeError` naming `section` and its type; an unknown name raises `ValueError: unknown section 'HS9'; known sections are [...]`. Then `level`: `None` releases; otherwise `_validation.level(f"holds[{section!r}]", level, OUTPUT_MIN, OUTPUT_MAX)` — `TypeError`/`ValueError`/`OverflowError` naming `holds['HS3']` and repeating the caller's value. Only after both pass is the section modulator's `fixed_output` assigned (stored as `float`, `-0.0` as `+0.0`). Takes effect on the next `update`; leaves `duty` and `history` untouched. | A5, A6 |
| `SectionAllocator.holds -> dict[str, float \| None]` | `allocator.py` | Fresh dict, every section in constructor order, its held level or `None`. | A5 |
| `SectionAllocator.update(measured: Mapping[str, float]) -> dict[str, float]` | `allocator.py` | Same signature and validation. A held section's duty is its level and its commands are the floor-heating modulation of that level (the modulator's `fixed_output` replaces the duty it is handed, and it is handed the level) — exactly the level for 0.0 and 1.0; the free sections are solved with the held contributions subtracted; an all-held component calls no solver. Every room's PI steps as before, hold or not. Failure atomicity unchanged (holds are not written by `update`). | A2, A3, A4, A6, A7, A8, A9 |
| `SectionAllocator.duty -> dict[str, float] \| None` | `allocator.py` | Unchanged signature; a held section reports its level. | A6 |
| `SectionAllocator.to_dict() -> dict[str, object]` | `allocator.py` | Per section, in this order: `coverage` (as given), `history`, `hold` (`float` or `None`). Top level and rooms unchanged. | A10 |
| `SectionAllocator.from_dict(data: Mapping[str, object]) -> Self` (classmethod) | `allocator.py` | Section key set is exactly `coverage`, `history`, `hold` (missing before unknown; `snapshot_mapping` stops at the missing keys, so a 1.0.0 entry raises `sections['HS1']: snapshot is missing keys ['coverage', 'hold'].` and `shares` is not named). The layout goes through the constructor (`coverage` mapping path `sections['HS1']['coverage']`); after the constructor, in the per-section loop and before `Modulator._from_dict`, `hold` is validated with `_validation.level(f"sections[{name!r}].hold", ...)` (so a layout error is reported before a hold error) and restored into the rebuilt floor-heating `Modulator`'s `fixed_output` alongside its window. `duty` and every `demand` are `None` afterwards, as in 1.0.0. | A10 |
| `main() -> None` | `allocator.py` | Showcase rebuilt on the reference layout (HS1–HS5 over R1–R4): E1 free allocation, E2 HS3 held at 0 and released, E3 R1's normalisation, E4 evenness, a JSON snapshot round trip carrying a hold, an invalid room sum. | A11 |
| `version = "1.1.0"` | `pyproject.toml` | Release number. | A11 |

`hold`'s `TypeError` for a non-`str` section is a deliberate refinement of A5's "unknown section raises `ValueError`": it matches how `_name` treats non-`str` names, and a non-hashable section could not be looked up anyway.

`Room` and its properties are unchanged. `_Component` (private) loses its `share` field; the
closed form needs no coefficient.

### Implementation guide

1. Replace `_build_shares` with a coverage builder: the same per-entry checks and paths (the
   message for a value outside `(0, 1]` stays `sections['HS4']['R3'] must be in (0, 1], got …`),
   no per-section sum. Rename `_SHARE_SUM_TOLERANCE` to a coverage-sum tolerance (same 1e-9).
2. In `__init__`, after the uncovered-rooms check, compute each room's `math.fsum` of
   coverages in room order and raise the per-room `ValueError` above. Store `self._coverage`
   (as given) and a private normalised table `c̃[s][r] = c[s][r] / total_r`.
3. Rebuild `_component` from the normalised table: matrix entries `weight * c̃[s][r]`,
   evenness rows as today; the closed form is chosen as today (one room, one section) and
   carries no share.
4. `_allocate(demand)` reads the holds from the modulators. Closed form: the level if held,
   else `min(OUTPUT_MAX, demand) + 0.0`. Matrix component: `held`/`free` column index lists
   in section order; `b` built as today, then `b -= matrix[:, held] @ levels` when anything
   is held; if `free` is empty, every duty is its level and no solver runs; otherwise solve
   `matrix[:, free]` with `max_iter = 20 * len(free)`, same status/finiteness check (error
   naming the component's free sections), clamp, `+ 0.0`. Held sections' duty is their level.
5. Add `hold` and `holds` as specified. `hold` writes only after validating both arguments.
6. `update` unchanged apart from passing each section its duty (the level, for a held one);
   confirm that no code path writes a hold.
7. Snapshot: `_SECTION_SNAPSHOT_KEYS = {"coverage", "history", "hold"}`; `to_dict` writes them
   in that order; `from_dict` validates the hold in the per-section loop after the constructor, then passes it as the
   modulator's `fixed_output` in `Modulator._from_dict`.
8. Docstrings: module docstring (coverage, normalisation, `h_r = Σ c̃_rs u_s`, holds as
   constants), class docstring Args/Raises, `update`, `to_dict`, `from_dict`, `hold`, `holds`.
9. `main()` rebuilt as in the Public API row, following `.claude/rules/python.md`'s showcase form.
10. Migrate `tests/test_allocator.py`: every layout that is invalid or means something else
    under coverage is rewritten to a valid coverage layout that keeps the test's intent, and
    expected numbers are recomputed (by hand or an independent solve, never by reading the new
    code's output back). Replaced on purpose, because this round reverses their intent (a
    replacement, not a deletion): the per-section share-sum tests (around lines 163, 170, 184)
    become per-room-sum tests (T1); `test_equivalence_needs_a_dedicated_share_of_one` (around
    line 1514, coverage 0.5 runs hotter than 1.0) becomes "coverage 0.5 and 1.0 give the
    identical command sequence" (T3).
11. `pyproject.toml` to 1.1.0; README's "SectionAllocator usage" section; `STRUCTURE.md`.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | The reference layout constructs; a room sum above `1 + 1e-9` raises `ValueError` naming the room and the sum (just above and just at the tolerance, several rooms → the first in room order); a section covering two rooms with sum > 1 is accepted; coverage refusals per entry keep their paths; the other 1.0.0 layout refusals and their order are unchanged; nothing is built on failure. | A1 |
| T2 | E1 and E3 to 1e-3 of the stated numbers and to 1e-9 of an independent normalised solve; a room with coverages summing below 1 gives the same allocation as the same layout scaled to sum 1. | A2 |
| T3 | For coverages such as 0.5, 0.3, 1.0, 1e-9 and 1.0 − 1e-12, a one-section room's command sequence `==` a standalone floor-heating `PIController`'s over a long run with setpoint and gain changes, alone and beside an unrelated multi-room component. | A3 |
| T4 | 1.0.0's A4–A6 and A10–A12 tests carried over to coverage layouts: KKT conditions and an independent solve on random coverage layouts, priority-scaling invariance, A5/A6 monotonicity with their documented scope, the worked examples recomputed, E1–E5. | A4 |
| T5 | `hold` accepts `0`, `1`, `0.5`, `int`, `Fraction`, `None`; refuses `bool`, non-numeric, `nan`/`inf`, out-of-range (one ULP outside) and huge `int` with the right class naming `holds['HS3']`; unknown and non-`str` sections; a raising call leaves `holds`, `history`, `duty` and `to_dict()` unchanged; `holds` is fresh and complete. | A5 |
| T6 | Held at 0.0 and at 1.0 under every demand, a section's command equals the level at every update and its window records it; held at 0.25 for one full window, commands are in {0, 1}, the window mean is 0.25 and `duty` is 0.25 at every step; `duty` reports the level; E2; release → E1 on the next update; a held section in a closed-form component; a hold changed mid-run. | A6 |
| T7 | With `lsq_linear` monkeypatched to raise, an all-held component updates fine (E5), while a component with one free section does call it. | A7 |
| T8 | A held allocator and an un-held twin fed the same temperatures have identical room integrals and demands every step. | A8 |
| T9 | A bad measurement and a failing solver each leave holds, windows, rooms and `duty` exactly as before (`to_dict()` and `duty` compared). | A9 |
| T10 | `to_dict` section shape and key order; JSON round trip with a hold set gives identical next 50 commands; `hold` restored; a bad `hold` in a snapshot raises naming `sections['HS1'].hold`; a 1.0.0-shaped entry is refused with the missing-keys message naming `coverage` and `hold` (only); `sections` returns coverages as given. | A10 |
| T11 | `importlib.metadata.version("heatingsystem") == "1.1.0"` (or the `pyproject.toml` value) and the module showcase runs. | A11 |

Coverage check (section 5 of `/plan`): every criterion A1–A11 has a Public API row and a test
intent; every Public API row cites a criterion. README/STRUCTURE.md content for A11 is checked
by step 4 and step 6, not by a test.

### Risks

- **Test migration is most of the work.** Many 1.0.0 tests use layouts like
  HS1 {R1 1.0}, HS2 {R1 0.5, R2 0.5}, which now sum to 1.5 for R1 and are refused. Rewrite
  each to a valid coverage layout preserving the test's intent; never delete a test to make
  room, and never derive an expected number from the new code. Not a halt.
- **A5/A6 under normalisation.** If a restated monotonicity test fails in a way 1.0.0's
  documented exceptions do not cover, that changes what A4 promises — **halt**.
- **A3 exactness.** If `c / c` or the closed form breaks `==` with the standalone controller
  for some coverage, **halt** (the criterion is exact by agreement).
- **Single free column.** `lsq_linear(method="bvls")` handles one column (checked: E2 gives
  status 3, x = 7/13). If a degenerate case fails the status check, a private one-column
  closed form (`clip(a·b / a·a, 0, 1)`) is the build's call, not a halt.
- **README** may describe the old semantics in more places than the usage section; fix
  every mention.

### Critique

plan-critic (verdict: accept with changes; E1–E5 independently recomputed and confirmed):

1. A held fractional level is duty-modulated, not output as the level — **applied**: A6 reworded
   (duty is the level; commands are its modulation, exactly the level for 0/1), the `update`
   row aligned, a 0.25-hold case added to T6. Flagged to the user at plan acceptance.
2. `snapshot_mapping` stops at missing keys, so `shares` is never named — **applied**: T10 and
   the `from_dict` row assert the missing-keys message only.
3. One 1.0.0 test asserts the opposite of the generalised A3, and the migration rule forbade
   removing it — **applied**: the reversed tests are named in guide step 10 as replacements.
4. `from_dict` hold-validation order was ambiguous — **applied**: after the constructor,
   before `Modulator._from_dict`. Non-`str` section `TypeError` — **kept**, documented as a
   deliberate refinement of A5.

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


# Project Frame

## Reservation domain
Parking places in a company car park. A user reserves one concrete parking
place for a time interval (start–end).

## Purpose
Employees and visitors of a company can reserve a parking place ahead of
time instead of hunting for a free one. The system guarantees that a
confirmed place is not double-booked and keeps places from being blocked
for days.

## Users / Stakeholders
- **Driver** – creates, confirms and cancels their own reservations.
- **Facility manager** – manages parking places, can cancel any reservation.

## Core concepts
Reservation, Resource (= ParkingPlace), User.

## Core operations
- Create reservation
- Confirm / approve reservation
- Cancel reservation
- Check availability

## Persistent state
- **Reservation**: id, place_id, user_id, start, end, state.
- **ParkingPlace**: id, label (e.g. `A-12`).

## State-changing operation
`DRAFT → CONFIRMED` (confirm) and `DRAFT | CONFIRMED → CANCELLED` (cancel).

## Common business rule
Confirmed reservations for the same parking place must not overlap.

## Domain-specific business rule
A parking place reservation may not be longer than 24 hours – places are
for parking, not for long-term storage of a vehicle.

## External / system boundary
Notification Service – the driver is notified (e-mail) when a reservation is
confirmed or cancelled. Modelled as a dependency behind an interface; not
implemented in C01.

## Assumption
All times are handled in UTC; drivers do not need reservations that cross a
daylight-saving change to be displayed in local time.

## Unknown
How many places / reservations per day the car park really has, i.e.
whether SQLite is enough beyond CP1 or a client–server database is needed.

## Selected future pressure
Category: **C** (Changeability)

Concrete pressure: a new resource type – *EV charging place* – with its own
rule (max 4 hours, only for users with an electric vehicle).

Why it is relevant to our reservation system: the company is installing
chargers next year; the overlap rule and the persistence must work for both
place types without duplicating the reservation logic.

---

## C08 / CR-08 — Controlled Change Card: EV charging place (max 4 h)

**Change source and concrete project need.** The "Selected future pressure"
above (C01): the company installs chargers next year and wants an *EV
charging place* bookable in the same system. Facility manager's need: a car
must not block a charger for a whole day, so a charging place is reserved for
at most 4 hours. Today a charger would be just another place with the
24-hour limit of BR-04.

**Before (baseline).** Commit `743fb2a` (`main`, after the C07 merge);
specification v0.2 (`docs/specification.md`, BR-01..BR-05, REQ-01..REQ-08);
ADR-04 and D3 (`docs/architecture-and-decisions.md`); schema = the C01
`reservation` table in `src/parking/repository.py` (no version number, no
migrations); places are code (`src/parking/places.py`, `p1`..`p5`), not
stored in the database. Full baseline record and test runs:
`docs/evidence-and-evolution.md`, section C08.

**Previous observable behavior.** Every place accepts a reservation of up to
24 h (BR-04, REQ-01). There is no EV place and no per-place duration limit.

**New intended behavior.**
- A new place `p6` / `EV-01` (EV charging), no approval required.
- **BR-06:** a reservation of an EV charging place has `end - start <= 4 h`.
  Checked at Create, like BR-04 (the interval cannot change after Create).
- All other places keep BR-04 (24 h). Overlap (BR-02), lead time (BR-05),
  Confirm, Cancel and Approve behave the same on the EV place as on any
  place without approval.

**Out of scope.**
- *Only users with an electric vehicle* (second half of the C01 pressure):
  **DEFER**. The system has no user model, vehicle data or authentication
  (C07 R-05). Missing input: where user/vehicle identity comes from. Owner:
  R. Gřegoř. Next decision when login or a user registry is decided (R-05
  trigger).
- Charger hardware, pricing, a place-administration UI.

**Acceptance conditions** (`now` = 06:00 UTC, EV-01 unless stated):

| AC | Kind | Condition |
|---|---|---|
| AC-01 | positive | 08:00–10:00 on EV-01 → `DRAFT` |
| AC-02 | negative | 08:00–12:01 (4 h 1 min) on EV-01 → rejected, nothing stored, error names the 4-hour limit |
| AC-03 | boundary | exactly 4 h (08:00–12:00) → `DRAFT`; 4 h + 1 s → rejected (inclusive, like BR-04) |
| AC-04 | regression | A-01: 24 h → `DRAFT`, 25 h → rejected (BR-04 unchanged) |
| AC-05 | regression | BR-02 on EV-01: overlapping Confirm rejected, adjacent Confirm succeeds; TC-C08-01/02 on A-01 still pass |
| AC-06 | observable | EV-01 is listed by the place catalog (GUI list, HTTP `place_id = "p6"`) |

**Impact analysis method.** Started from AC-02 ("a 5-hour EV reservation is
refused") and followed the owner of the decision in the current architecture:
client (`gui.py`, `http_api.py`) → `ReservationService.create` →
`domain.create_reservation` (owns BR-04) → `PlaceCatalog.find` (owns place
properties) → `ReservationRepository.save`. Then every reader of the place
list (`grep PLACES / places()`): GUI, `confirm_latency.py`, tests. Inspected
at `743fb2a`: the files below, the statechart v0.2, ADR-04, D4, the C07 risk
register.

| Dimension / exact artifact | CHANGE / CHECK / NO IMPACT and why | Action / check | Owner / evidence |
|---|---|---|---|
| Spec: `specification.md` BR-04, REQ-01, OP-01 examples | **CHANGE** – new rule BR-06, REQ-01 gets a per-place limit | add BR-06, revise REQ-01 (v0.3), add examples at 4 h / 4 h + 1 s | R. Gřegoř; spec diff in C09 |
| State model: statechart v0.2 | **NO IMPACT** – no new state or transition; BR-06 acts before `[*] → DRAFT` | none | – |
| Architecture: ADR-04, C03 G2 | **CHECK** – the rule must stay in the domain, place data in the catalog; no new component, no new writer path | keep rule in `create_reservation`; `test_architecture` must pass | architecture test in C09 |
| Detailed design: `domain.ParkingPlace`, `create_reservation` (DD-4), `places.PLACES` | **CHANGE** – the limit becomes a property of the place instead of one global constant | `max_duration` on the place, default 24 h; `p6` with 4 h | unit tests at the boundary |
| Client `gui.py` | **CHECK** – the place list comes from the catalog (EV-01 appears by itself); hint labels mention only VIP; `refresh()` looks up `labels[r.place_id]` | add a hint for EV-*; no other change | manual check + existing GUI tests |
| Client `http_api.py` | **CHECK** – `RuleViolation` is already 422; request/response unchanged | none; integration test with `p6` | HTTP test in C09 |
| Data / schema: `reservation` table | **NO IMPACT** – no column added; places are not stored. Old rows: the 4-h limit applies only to `p6`, which has no rows before the change, so no stored reservation becomes invalid | none | – |
| Config / CI / release | **CHECK** – the limit is a code constant (as BR-04/BR-05), no config file exists; no CI exists | no config; C09 adds one automated check command | C09 record |
| Measurement `confirm_latency.py` (C04, D4) | **CHECK** – it seeds history on *every* place in `PLACES`; 6 instead of 5 places = 20 % more rows, and `for_place` scans the whole table (no index, D4) → QC-1 numbers may shift | re-run QC-1 after the change or record NOT RUN | C09 record |
| Notification (`notification.py`) | **NO IMPACT** – notifies on state change, place-independent | none | – |
| Tests | **CHANGE** – new boundary oracle (AC-01..AC-03, AC-06); regression AC-04, AC-05 | new tests + full suite | C09 test table |
| Security / roles (R-05) | **NO IMPACT** – no new state-changing HTTP operation; EV-user eligibility is deferred | none; R-05 monitor unchanged | – |
| Operation / recovery | **CHECK** – rolling back the code while `p6` reservations exist: `PlaceCatalog.find("p6")` fails ("parking place does not exist") on Confirm, and the GUI `labels[...]` lookup in `refresh()` raises `KeyError` | document: before a code rollback, remove `p6` rows; rollback not automated. *C09 update: tried it – Cancel works on old code, but the GUI lookup fails even for a CANCELLED `p6` row, so the rows must be deleted (see C09 rollback).* | C09 release record |

**Alternatives** (common criteria: meets AC, scope, failure/rollback,
verification):

| Option | Meets AC? | Benefits | Cost / failure / rollback | How to verify |
|---|---|---|---|---|
| **A: limit as a place property** – `ParkingPlace.max_duration` (default 24 h), catalog entry `p6` with 4 h, `create_reservation` uses `place.max_duration` | yes, AC-01..06 | smallest change (domain + catalog), no schema change, BR-04 becomes a default, not a second copy of the rule | rollback = revert code, but see operation/recovery; message must name the place's limit | boundary unit tests, HTTP integration test, full regression |
| **B: place kinds with a rule set** – `PlaceKind` (STANDARD / VIP / EV) mapped to rules incl. a future EV-user check | yes | ready for the deferred EV-user rule | larger diff: `requires_approval` would move into the kind, more tests touched; speculative structure for a deferred requirement | same as A + re-test approval |
| **C: no code change** – EV-01 as a normal place, the Facility manager cancels anything longer than 4 h by hand | **no** – AC-02/AC-03 fail, the system accepts 24 h | nothing to implement | manual work, a charger can stay blocked until someone notices; the driver learns about it only by a cancellation | process check only |

**Consequence of inaction.** Chargers would be bookable for 24 h, i.e. one
car per charger per day, which defeats the purpose of the installation.
Option C is the only no-code compensation and does not meet the need.

**Peer reviewer and challenged assumption.** *PENDING – to be done by a
team member other than the author:* name, date, which assumption was
challenged (suggested: "BR-06 only at Create is enough, no path changes the
interval later" and "the rollback limitation is acceptable"), outcome.

**Decision.** **ACCEPT option A** (proposed by the owner R. Gřegoř,
2026-11-16). Rationale: meets all ACs with the smallest delta, keeps one
copy of the duration rule, no schema migration. B is postponed until the
deferred EV-user rule is decided; C does not meet AC-02. The decision is
final after the peer review above.

**Hand-off to C09** (bounded delta and test oracles):

| Hand-off item | Content |
|---|---|
| Before | `743fb2a`, spec v0.2, TC-C08-01/02 executed (evidence C08) |
| Accepted change | CR-08: EV-01 with BR-06 (≤ 4 h), checked at Create |
| Bounded delta | `domain.py` (`ParkingPlace.max_duration`, `create_reservation`), `places.py` (`p6`), GUI hint, spec BR-06/REQ-01; nothing else |
| New oracle | EV-01: 2 h → `DRAFT`; exactly 4 h → `DRAFT`; 4 h + 1 s and 4 h 1 min → rejected, nothing stored; via HTTP 201 / 422 with an independent DB read |
| Regression | TC-C08-01/02, A-01 24 h / 25 h, BR-02 on EV-01, all existing tests |
| Unchanged | schema, states, Confirm/Approve/Cancel, HTTP contract, ADR-04 |
| Risk | rollback with existing `p6` rows (not automated, documented); QC-1 shift from the extra place (re-measure) |
| C09 work | implementation commit, review, automated check command, clean checkout, release record, decision |

**Open uncertainty / next trigger.** EV-user eligibility (DEFER, trigger:
login or user registry). Whether 4 h is the right value is the Facility
manager's call; it is one constant in `places.py`.

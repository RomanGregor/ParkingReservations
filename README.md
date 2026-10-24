# ParkingReservation

Reservation system for **parking places**. Course project for SWI (460-4163), team *ParkingCrew*.

## Team

- RomanGregor
- Ondra-lab
- Kirisok
- olehkulcyckyj

Repository: https://github.com/RomanGregor/ParkingReservations

## Stack

Python 3.12+ standard library only (`sqlite3`, `unittest`, `dataclasses`).
Rationale: every team member already has Python installed, there is nothing to
download, and a clean checkout runs with a single command. See
`docs/architecture-and-decisions.md`.

## Run

GUI (tkinter, creates `parking.db` in the current directory):

```
python3 run.py            # or: python3 run.py path/to/other.db
```

Tests:

```
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

C04 measurement (a few minutes):

```
PYTHONPATH=src python3 measurements/confirm_latency.py
```

## Layout

```
README.md
docs/
  intent-and-change.md          # Project Frame + future pressure
  architecture-and-decisions.md # stack, C03 architecture (ADR-04), C05 detailed design
  specification.md              # C02: baseline v0.1/v0.2 behavior spec + diagrams
  evidence-and-evolution.md     # evidence C01 spike, C02, C03, C05
run.py                          # launches the GUI
measurements/confirm_latency.py # C04: Confirm time vs. history size, lock errors
src/parking/                    # service (state changes), domain rules, place catalog,
                                # persistence, notification stub, tkinter GUI
tests/                          # unit, service, concurrency and architecture tests
  test_detailed_design.py       # C05: unit/component tests derived from the detailed design
```

## CP1 walking skeleton

One end-to-end path, runnable after C03 / before C04:

```
POST /reservations {place_id, user_id, start, end}
→ validate (end > start, max 24 h, place exists)
→ persist as DRAFT in SQLite
→ return {"id": "<reservation id>", "state": "DRAFT"}
→ automated check: test posts a reservation, reads it back by id, asserts state == DRAFT
```

Status: the HTTP adapter is not built yet (D1: a framework is added when the
skeleton needs it). The same path without HTTP is `ReservationService.create`
→ SQLite → read back by id as `DRAFT`, checked by
`tests/test_service.py::test_cp1_created_reservation_is_read_back_by_id_as_draft`.

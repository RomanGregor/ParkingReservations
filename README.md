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

HTTP API (CP1 walking skeleton, same `parking.db` as the GUI, only on localhost):

```
python3 run_api.py        # or: python3 run_api.py path/to/other.db --port 8000
curl -X POST http://127.0.0.1:8000/reservations -H "Content-Type: application/json" \
     -d '{"place_id": "p1", "user_id": "alice", "start": "2026-12-01T08:00:00+00:00", "end": "2026-12-01T10:00:00+00:00"}'
curl http://127.0.0.1:8000/reservations/<id>
```

Times need a UTC offset (`+00:00`). Places are `p1`..`p4` (`A-01`..`A-04`)
and `p5` (`VIP-01`, needs approval).

Tests:

```
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

C04 measurement (a few minutes; 1 instance, then 2 processes confirming at once):

```
PYTHONPATH=src python3 measurements/confirm_latency.py
```

The same on Windows PowerShell (`python` instead of `python3`):

```
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python measurements/confirm_latency.py
```

## Layout

```
README.md
docs/
  intent-and-change.md          # Project Frame + future pressure
  architecture-and-decisions.md # stack, C03 architecture (ADR-04), C05 detailed design
  specification.md              # C02: baseline v0.1/v0.2 behavior spec + diagrams
  evidence-and-evolution.md     # evidence C01 spike, C02-C05, C06 findings, C07 risk register
run.py                          # launches the GUI
run_api.py                      # launches the HTTP API
measurements/confirm_latency.py # C04: Confirm time vs. history size, lock errors
src/parking/                    # service (state changes), domain rules, place catalog,
                                # persistence, notification stub, tkinter GUI, HTTP API
tests/                          # unit, service, concurrency, HTTP API and architecture tests
  test_detailed_design.py       # C05: unit/component tests derived from the detailed design
  test_http_api.py              # CP1 walking skeleton end to end over HTTP
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

Status: built in C07 (`src/parking/http_api.py`, standard library only, D5),
with `GET /reservations/<id>` for the read-back. Checked end to end by
`tests/test_http_api.py::HttpApi::test_cp1_post_creates_a_draft_that_is_read_back_by_id`.
A rule violation (e.g. more than 24 h, unknown place) is a 422, a time without
a UTC offset a 400. The same path without HTTP is still checked by
`tests/test_service.py::test_cp1_created_reservation_is_read_back_by_id_as_draft`.

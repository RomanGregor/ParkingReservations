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

Times need a UTC offset (`+00:00`). Places are `p1`..`p4` (`A-01`..`A-04`),
`p5` (`VIP-01`, needs approval) and `p6` (`EV-01`, EV charging, at most 4 h,
C08 CR-08). All other places allow at most 24 h.

Build and test check (C09, the same command runs in CI,
`.github/workflows/check.yml`): prints commit, Python, SQLite and OS,
byte-compiles the sources, runs all tests, lists skipped ones, exit code 0
only on success. Nothing to install (standard library only). Each test
creates its own SQLite file in a temporary directory, so no database or
configuration has to be prepared and no environment variable is needed.

```
python3 check.py                  # or: python3 check.py --log check.log
```

Tests only:

```
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Clean checkout (C09 reproduction):

```
git clone https://github.com/RomanGregor/ParkingReservations.git pr-clean
cd pr-clean
git checkout <commit or tag>
python3 check.py --log check.log
python3 run_api.py smoke.db --port 8000     # smoke: POST + GET as above, then Ctrl+C
```

C04 measurement (a few minutes; 1 instance, then 2 processes confirming at once):

```
PYTHONPATH=src python3 measurements/confirm_latency.py
```

C10 operational trials (success, conflict, storage outage, locked DB,
restart) against the real API process, on a disposable database in a new
temporary directory (never `parking.db`); prints responses, an independent
read of the database after each step and the API log:

```
python3 measurements/c10_operations.py      # optional: --workdir DIR --port 8766
```

The API logs to stderr in UTC (`2026-11-16T11:58:13.564Z ERROR parking.http: ...`).
A database that cannot be opened or is locked is answered with 503.

The same on Windows PowerShell (`python` instead of `python3`):

```
python check.py
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python measurements/confirm_latency.py
```

## Layout

```
README.md
docs/
  intent-and-change.md          # Project Frame + future pressure, C08 change card CR-08
  architecture-and-decisions.md # stack, C03 architecture (ADR-04), C05 detailed design
  specification.md              # C02: baseline v0.1/v0.2 behavior spec + diagrams, v0.3 (CR-08)
  evidence-and-evolution.md     # evidence C01 spike, C02-C05, C06 findings, C07 risk register,
                                # C08 baseline tests, C09 release record, C10 operations
run.py                          # launches the GUI
run_api.py                      # launches the HTTP API
check.py                        # C09: build + all tests, the same locally and in CI
.github/workflows/check.yml     # C09: CI running check.py (Linux + Windows)
measurements/confirm_latency.py # C04: Confirm time vs. history size, lock errors
measurements/c10_operations.py  # C10: operational trials against the running API
src/parking/                    # service (state changes), domain rules, place catalog,
                                # persistence, notification stub, tkinter GUI, HTTP API
tests/                          # unit, service, concurrency, HTTP API and architecture tests
  test_detailed_design.py       # C05: unit/component tests derived from the detailed design
  test_http_api.py              # CP1 walking skeleton end to end over HTTP
  test_c08_baseline.py          # C08: TC-C08-01/02 against a SQLite file, independent read
  test_cr08_ev_place.py         # C09: CR-08 EV place, 4 h boundary incl. over HTTP
  test_c10_operations.py        # C10: locked / unavailable database -> StoreBusy / 503
```

## CP1 walking skeleton

One end-to-end path, runnable after C03 / before C04:

```
POST /reservations {place_id, user_id, start, end}
→ validate (end > start, max 24 h or 4 h on EV-01, place exists)
→ persist as DRAFT in SQLite
→ return {"id": "<reservation id>", "state": "DRAFT"}
→ automated check: test posts a reservation, reads it back by id, asserts state == DRAFT
```

Status: built in C07 (`src/parking/http_api.py`, standard library only, D5),
with `GET /reservations/<id>` for the read-back. Checked end to end by
`tests/test_http_api.py::HttpApi::test_cp1_post_creates_a_draft_that_is_read_back_by_id`.
A rule violation (e.g. more than 24 h, more than 4 h on EV-01, unknown place)
is a 422, a time without a UTC offset a 400, a database that cannot be opened
or is locked a 503 (C10). The same path without HTTP is still checked by
`tests/test_service.py::test_cp1_created_reservation_is_read_back_by_id_as_draft`.

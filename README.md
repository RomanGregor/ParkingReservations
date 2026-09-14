# ParkingReservation

Reservation system for **parking places**. Course project for SWI (460-4163), team *ParkingCrew*.

## Team

- RomanGregor
- Ondra-lab
- Kirisok

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

## Layout

```
README.md
docs/
  intent-and-change.md          # Project Frame + future pressure
  architecture-and-decisions.md # stack + decisions
  evidence-and-evolution.md     # spike evidence
run.py                          # launches the GUI
src/parking/                    # domain model, persistence, tkinter GUI
tests/                          # unit tests (spike evidence lives here)
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

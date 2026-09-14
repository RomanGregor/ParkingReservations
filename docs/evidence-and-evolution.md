# Evidence and evolution

# C01 Engineering Spike

Spike variant: **A – Persistence** (issue #1, PR from `spike/sqlite-persistence`).

Question / unknown:
Can a `Reservation` (with a `State` enum and timezone-aware `datetime`s) be
stored in SQLite and loaded back *identically* in a new connection, so that
the overlap rule can be evaluated against persisted data? SQLite has no
datetime or enum column type, so we were not sure the round trip is lossless.

What we did:
Wrote `src/parking/repository.py` (`ReservationRepository` with `save`,
`get`, `for_place`) and `tests/test_persistence_spike.py`, which saves a
confirmed reservation to a temporary `.db` file, closes the connection,
opens a fresh one, loads by id and compares with the original. A second test
confirms a new reservation against reservations loaded from the DB.

Run: `PYTHONPATH=src python3 -m unittest discover -s tests -v`

Observed result:
```
test_loaded_reservations_feed_the_overlap_rule ... ok
test_reservation_survives_round_trip ... ok
Ran 5 tests in 0.003s
OK
```
`datetime.isoformat()` / `datetime.fromisoformat()` preserves the UTC offset,
`loaded == original` holds (dataclass equality), and `State(row)` restores the
enum. Note: `end` is a reserved word in SQLite, so the column is quoted.

Decision / what changes because of the result:
- Times are stored as ISO-8601 strings in UTC (recorded as D3 in
  `docs/architecture-and-decisions.md`); no custom adapters needed.
- The overlap rule stays in the domain and takes loaded reservations as
  input; the repository does not duplicate the rule.
- SQLite is sufficient for the CP1 walking skeleton. The `Unknown` in the
  Project Frame (real load) stays open.

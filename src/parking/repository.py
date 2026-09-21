"""SQLite persistence for reservations (C01 engineering spike A)."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Callable

from parking.domain import Reservation, RuleViolation, State

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reservation (
    id       TEXT PRIMARY KEY,
    place_id TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    start    TEXT NOT NULL,   -- ISO-8601, UTC
    "end"    TEXT NOT NULL,   -- ISO-8601, UTC
    state    TEXT NOT NULL
)
"""


class ReservationRepository:
    def __init__(self, path: str):
        # Autocommit mode: single statements commit on their own and
        # `apply` opens its transactions explicitly.
        self._db = sqlite3.connect(path, isolation_level=None)
        self._db.execute(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def save(self, r: Reservation) -> None:
        self._db.execute(
            'INSERT OR REPLACE INTO reservation (id, place_id, user_id, start, "end", state) '
            "VALUES (?, ?, ?, ?, ?, ?)",
            (r.id, r.place_id, r.user_id, r.start.isoformat(), r.end.isoformat(), r.state.value),
        )

    def apply(self, reservation_id: str, operation: Callable[[Reservation, list[Reservation]], object]) -> Reservation:
        """Run a state-changing operation (confirm, approve, reject, cancel,
        expire) atomically.

        The reservation and the other reservations of its place are read, the
        domain rule is checked and the result is written under one SQLite
        write lock (BEGIN IMMEDIATE). Another connection cannot slip its own
        write between our check and our write, so two conflicting confirmations
        cannot both succeed (REQ-04) and a concurrent Confirm cannot overwrite
        a Cancel.
        """
        with self._write_lock():
            r = self.get(reservation_id)
            if r is None:
                raise RuleViolation("reservation does not exist")
            operation(r, self.for_place(r.place_id))
            self.save(r)
        return r

    @contextmanager
    def _write_lock(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def get(self, reservation_id: str) -> Reservation | None:
        row = self._db.execute(
            'SELECT id, place_id, user_id, start, "end", state FROM reservation WHERE id = ?',
            (reservation_id,),
        ).fetchone()
        return self._to_reservation(row) if row else None

    def for_place(self, place_id: str) -> list[Reservation]:
        rows = self._db.execute(
            'SELECT id, place_id, user_id, start, "end", state FROM reservation WHERE place_id = ?',
            (place_id,),
        ).fetchall()
        return [self._to_reservation(row) for row in rows]

    @staticmethod
    def _to_reservation(row) -> Reservation:
        id_, place_id, user_id, start, end, state = row
        return Reservation(
            place_id, user_id,
            datetime.fromisoformat(start), datetime.fromisoformat(end),
            State(state), id_,
        )

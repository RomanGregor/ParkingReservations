"""SQLite persistence for reservations (C01 engineering spike A)."""
import sqlite3
from datetime import datetime

from parking.domain import Reservation, State

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
        self._db = sqlite3.connect(path)
        self._db.execute(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def save(self, r: Reservation) -> None:
        with self._db:
            self._db.execute(
                'INSERT OR REPLACE INTO reservation (id, place_id, user_id, start, "end", state) '
                "VALUES (?, ?, ?, ?, ?, ?)",
                (r.id, r.place_id, r.user_id, r.start.isoformat(), r.end.isoformat(), r.state.value),
            )

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

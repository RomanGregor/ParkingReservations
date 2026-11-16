"""C08 baseline test cases TC-C08-01 and TC-C08-02 (docs/evidence-and-evolution.md, C08).

Executed through the real ReservationService against a SQLite file (not an
in-memory DB, not a mock). Every check of the stored state reads the file
over its own new sqlite3 connection, independent of the service and of
ReservationRepository. Each case runs twice from a fresh file (step 5:
repeatable after reset).

Fixed clock: now = 2026-12-01 06:00 UTC, so a reservation at 10:00 satisfies
BR-05 (start >= now + 1 h). Place p1 (A-01) does not require approval, so
Confirm commits DRAFT -> CONFIRMED directly (OP-03).
"""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from parking.domain import RuleViolation, State, User
from parking.repository import ReservationRepository
from parking.service import ReservationService

NOW = datetime(2026, 12, 1, 6, 0, tzinfo=timezone.utc)
DRIVER_1 = User("tc-driver-1", "TC driver 1")
DRIVER_2 = User("tc-driver-2", "TC driver 2")


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 12, 1, hour, minute, tzinfo=timezone.utc)


def read_committed(path: str) -> dict[str, tuple[str, str, str]]:
    """Independent oracle: id -> (start, end, state) straight from the file."""
    db = sqlite3.connect(path)
    try:
        rows = db.execute('SELECT id, start, "end", state FROM reservation WHERE place_id = ?', ("p1",)).fetchall()
    finally:
        db.close()
    return {id_: (start, end, state) for id_, start, end, state in rows}


class C08Baseline(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def _prepare(self, run: int, b2_start: datetime, b2_end: datetime):
        """Step 1: fresh isolated file with B1 CONFIRMED [10:00, 11:00) and B2 DRAFT."""
        path = os.path.join(self.dir.name, f"c08-run{run}.db")
        service = ReservationService(ReservationRepository(path), clock=lambda: NOW)
        b1 = service.create("p1", DRIVER_1, at(10), at(11))
        service.confirm(b1.id)
        b2 = service.create("p1", DRIVER_2, b2_start, b2_end)
        return path, service, b1.id, b2.id

    def test_tc_c08_01_adjacent_reservation_can_be_confirmed(self):
        for run in (1, 2):
            with self.subTest(run=run):
                path, service, b1, b2 = self._prepare(run, at(11), at(12))
                try:
                    # Step 2: independent read of the initial committed state.
                    before = read_committed(path)
                    self.assertEqual(before[b1][2], "CONFIRMED")
                    self.assertEqual(before[b2][2], "DRAFT")
                    self.assertEqual(before[b1][1], before[b2][0])  # touching, [10,11) and [11,12)
                    # Step 3: real Confirm(B2).
                    self.assertEqual(service.confirm(b2).state, State.CONFIRMED)
                finally:
                    service.close()
                # Step 4: committed state over a new connection.
                after = read_committed(path)
                self.assertEqual({k: v[2] for k, v in after.items()}, {b1: "CONFIRMED", b2: "CONFIRMED"})

    def test_tc_c08_02_overlapping_confirm_is_rejected_and_nothing_changes(self):
        for run in (1, 2):
            with self.subTest(run=run):
                path, service, b1, b2 = self._prepare(run, at(10, 30), at(11, 30))
                try:
                    before = read_committed(path)
                    self.assertEqual({k: v[2] for k, v in before.items()}, {b1: "CONFIRMED", b2: "DRAFT"})
                    with self.assertRaises(RuleViolation) as rejected:
                        service.confirm(b2)
                    self.assertIn(b1, str(rejected.exception))  # the conflict is named
                finally:
                    service.close()
                after = read_committed(path)
                self.assertEqual(after, before)  # B1 unchanged, B2 still DRAFT, nothing written


if __name__ == "__main__":
    unittest.main()

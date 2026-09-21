"""REQ-04 and the Cancel x Confirm race, run against the real SQLite file.

Each thread has its own connection (as two app instances sharing parking.db
would). The operation sleeps between the rule check and the write, which is
exactly the window a lost update needs.
"""
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

from parking.domain import ParkingPlace, RuleViolation, State, User, cancel, confirm, create_reservation
from parking.repository import ReservationRepository

PLACE = ParkingPlace("p1", "A-12")
ALICE = User("u1", "Alice")
BOB = User("u2", "Bob")
H = timedelta(hours=1)
T0 = datetime(2026, 9, 14, 8, tzinfo=timezone.utc)
NOW = T0 - 2 * H
WINDOW = 0.2  # seconds between check and write


def slow(operation):
    def run(r, existing):
        operation(r, existing)
        time.sleep(WINDOW)
    return run


class ConcurrentOperations(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "race.db")

    def tearDown(self):
        self.dir.cleanup()

    def _save(self, *reservations):
        repo = ReservationRepository(self.path)
        for r in reservations:
            repo.save(r)
        repo.close()

    def _run_in_parallel(self, *jobs):
        """Run each (reservation_id, operation) in its own thread and connection.
        Returns the exception raised by each job, or None."""
        results = [None] * len(jobs)
        start = threading.Barrier(len(jobs))

        def worker(i, reservation_id, operation):
            repo = ReservationRepository(self.path)
            try:
                start.wait()
                repo.apply(reservation_id, operation)
            except RuleViolation as e:
                results[i] = e
            finally:
                repo.close()

        threads = [threading.Thread(target=worker, args=(i, *job)) for i, job in enumerate(jobs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def _state(self, reservation_id):
        repo = ReservationRepository(self.path)
        try:
            return repo.get(reservation_id).state
        finally:
            repo.close()

    def test_two_concurrent_conflicting_confirmations_confirm_at_most_one(self):
        # REQ-04
        a = create_reservation(PLACE, ALICE, T0, T0 + 2 * H, NOW)
        b = create_reservation(PLACE, BOB, T0 + H, T0 + 3 * H, NOW)
        self._save(a, b)

        errors = self._run_in_parallel(
            (a.id, slow(lambda r, existing: confirm(r, existing, PLACE, NOW))),
            (b.id, slow(lambda r, existing: confirm(r, existing, PLACE, NOW))),
        )

        states = [self._state(a.id), self._state(b.id)]
        self.assertEqual(states.count(State.CONFIRMED), 1)
        self.assertEqual(states.count(State.DRAFT), 1)
        self.assertEqual(sum(e is not None for e in errors), 1)

    def test_concurrent_cancel_and_confirm_end_cancelled(self):
        # OP-04: a successfully cancelled reservation must not stay CONFIRMED.
        r = create_reservation(PLACE, ALICE, T0, T0 + H, NOW)
        self._save(r)

        errors = self._run_in_parallel(
            (r.id, slow(lambda r, existing: confirm(r, existing, PLACE, NOW))),
            (r.id, slow(lambda r, _: cancel(r, NOW))),
        )

        self.assertIsNone(errors[1])  # Cancel always succeeds before start
        self.assertEqual(self._state(r.id), State.CANCELLED)

    def test_unknown_reservation_is_rejected(self):
        repo = ReservationRepository(self.path)
        try:
            with self.assertRaises(RuleViolation):
                repo.apply("missing", lambda r, _: cancel(r, NOW))
        finally:
            repo.close()


if __name__ == "__main__":
    unittest.main()

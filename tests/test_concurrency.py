"""REQ-04, REQ-06 and the Cancel x Confirm race, run against the real SQLite file.

Each thread has its own ReservationService and connection (as two app
instances sharing parking.db would). The repository sleeps before every
write, i.e. between the rule check and the write, which is exactly the
window a lost update needs.
"""
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from parking.domain import ParkingPlace, RuleViolation, State, User, create_reservation
from parking.places import PlaceCatalog
from parking.repository import ReservationRepository
from parking.service import ReservationService, StoreBusy

PLACE = ParkingPlace("p1", "A-12")
APPROVAL_PLACE = ParkingPlace("p2", "VIP-01", requires_approval=True)
ALICE = User("u1", "Alice")
BOB = User("u2", "Bob")
H = timedelta(hours=1)
T0 = datetime(2026, 9, 14, 8, tzinfo=timezone.utc)
NOW = T0 - 2 * H
WINDOW = 0.2  # seconds between check and write


class SlowRepository(ReservationRepository):
    def save(self, r):
        time.sleep(WINDOW)
        super().save(r)


class ConcurrentOperations(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "race.db")

    def tearDown(self):
        self.dir.cleanup()

    def _service(self, repo):
        return ReservationService(repo, PlaceCatalog([PLACE, APPROVAL_PLACE]), clock=lambda: NOW)

    def _save(self, *reservations):
        repo = ReservationRepository(self.path)
        for r in reservations:
            repo.save(r)
        repo.close()

    def _run_in_parallel(self, *jobs):
        """Run each job (a function of the service) in its own thread and
        connection. Returns the exception raised by each job, or None."""
        results = [None] * len(jobs)
        start = threading.Barrier(len(jobs))

        def worker(i, job):
            service = self._service(SlowRepository(self.path))
            try:
                start.wait()
                job(service)
            except RuleViolation as e:
                results[i] = e
            finally:
                service.close()

        threads = [threading.Thread(target=worker, args=(i, job)) for i, job in enumerate(jobs)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def test_lock_timeout_is_store_busy_and_changes_nothing(self):
        # C04: a Confirm that does not get the write lock in time fails with
        # StoreBusy before reading anything, and the reservation stays DRAFT.
        service = self._service(ReservationRepository(self.path))
        x = service.create(PLACE.id, ALICE, T0, T0 + H)
        service.close()
        holder = ReservationRepository(self.path)
        waiting = self._service(ReservationRepository(self.path, lock_timeout=0.1))
        try:
            with holder.transaction():
                with self.assertRaises(StoreBusy):
                    waiting.confirm(x.id)
        finally:
            holder.close()
            waiting.close()
        self.assertEqual(self._state(x.id), State.DRAFT)

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

        errors = self._run_in_parallel(lambda s: s.confirm(a.id), lambda s: s.confirm(b.id))

        states = [self._state(a.id), self._state(b.id)]
        self.assertEqual(states.count(State.CONFIRMED), 1)
        self.assertEqual(states.count(State.DRAFT), 1)
        self.assertEqual(sum(e is not None for e in errors), 1)

    def test_two_concurrent_conflicting_approvals_confirm_at_most_one(self):
        # REQ-06 + REQ-04: the C03 scenario, two managers in two instances.
        x = create_reservation(APPROVAL_PLACE, ALICE, T0, T0 + H, NOW)
        y = create_reservation(APPROVAL_PLACE, BOB, T0 + H / 2, T0 + 2 * H, NOW)
        x.state = y.state = State.PENDING_APPROVAL
        self._save(x, y)

        errors = self._run_in_parallel(lambda s: s.approve(x.id), lambda s: s.approve(y.id))

        states = [self._state(x.id), self._state(y.id)]
        self.assertEqual(states.count(State.CONFIRMED), 1)
        self.assertEqual(states.count(State.PENDING_APPROVAL), 1)
        self.assertEqual(sum(e is not None for e in errors), 1)

    def test_concurrent_cancel_and_confirm_end_cancelled(self):
        # OP-04: a successfully cancelled reservation must not stay CONFIRMED.
        r = create_reservation(PLACE, ALICE, T0, T0 + H, NOW)
        self._save(r)

        errors = self._run_in_parallel(lambda s: s.confirm(r.id), lambda s: s.cancel(r.id))

        self.assertIsNone(errors[1])  # Cancel always succeeds before start
        self.assertEqual(self._state(r.id), State.CANCELLED)

    def test_unknown_reservation_is_rejected(self):
        service = self._service(ReservationRepository(self.path))
        try:
            with self.assertRaises(RuleViolation):
                service.cancel("missing")
        finally:
            service.close()


class ExpiryWhileStoreBusy(unittest.TestCase):
    """C06 F2: the expiry check (REQ-08) writes through transaction() like any
    other state change. While another instance holds the write lock, StoreBusy
    must reach the user as "Busy" or postpone the check, never escape the GUI."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "expiry.db")
        self.now = [NOW]
        service = self._service(ReservationRepository(self.path))
        self.request = service.create(APPROVAL_PLACE.id, ALICE, T0, T0 + H)
        service.confirm(self.request.id)  # PENDING_APPROVAL
        service.close()
        self.holder = ReservationRepository(self.path)

    def tearDown(self):
        self.holder.close()
        self.dir.cleanup()

    def _service(self, repo):
        return ReservationService(repo, PlaceCatalog([PLACE, APPROVAL_PLACE]), clock=lambda: self.now[0])

    def _state(self):
        repo = ReservationRepository(self.path)
        try:
            return repo.get(self.request.id).state
        finally:
            repo.close()

    def test_busy_expiry_check_is_store_busy_and_changes_nothing(self):
        service = self._service(ReservationRepository(self.path, lock_timeout=0.1))
        self.now[0] = T0 + H  # the request's start has passed: it is due to expire
        try:
            with self.holder.transaction():
                with self.assertRaises(StoreBusy):
                    service.expire_due()
        finally:
            service.close()
        self.assertEqual(self._state(), State.PENDING_APPROVAL)

    def test_gui_reports_a_busy_expiry_check_and_keeps_running(self):
        try:
            import tkinter
            from parking.gui import App
        except ImportError:
            self.skipTest("tkinter is not available")
        service = self._service(ReservationRepository(self.path, lock_timeout=0.1))
        try:
            with mock.patch("parking.gui.messagebox") as box:
                try:
                    app = App(service)
                except tkinter.TclError:
                    self.skipTest("no display for tkinter")
                app.withdraw()
                try:
                    self.now[0] = T0 + H
                    with self.holder.transaction():
                        app._tick()  # timer: the check is only postponed, no dialog
                        box.showwarning.assert_not_called()
                        app._guard(lambda: None)  # refresh after any button action
                        box.showwarning.assert_called_once()
                        self.assertEqual(box.showwarning.call_args.args[0], "Busy")
                    self.assertEqual(self._state(), State.PENDING_APPROVAL)
                    app.refresh()  # lock released: the next check expires the request
                    self.assertEqual(self._state(), State.EXPIRED)
                finally:
                    app.destroy()
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()

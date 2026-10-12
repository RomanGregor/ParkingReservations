"""ReservationService (Reservation Management, ADR-04): the C03 scenario
Confirm on a place that requires approval -> later Approve, plus the
alternative outcomes from the design sequence diagram (H1)."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from parking.domain import ParkingPlace, RuleViolation, State, User
from parking.places import PlaceCatalog
from parking.repository import ReservationRepository
from parking.service import ReservationService

PLACE = ParkingPlace("p1", "A-12")
APPROVAL_PLACE = ParkingPlace("p2", "VIP-01", requires_approval=True)
ALICE = User("u1", "Alice")
BOB = User("u2", "Bob")
H = timedelta(hours=1)
T0 = datetime(2026, 9, 14, 8, tzinfo=timezone.utc)


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def reservation_changed(self, reservation):
        self.sent.append((reservation.id, reservation.state))


class FailingNotifier:
    def reservation_changed(self, reservation):
        raise ConnectionError("Notification Service is down")


class ReservationServiceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "service.db")
        self.now = T0 - 2 * H
        self.notifier = RecordingNotifier()
        self.service = self._open(self.notifier)

    def tearDown(self):
        self.service.close()
        self.dir.cleanup()

    def _open(self, notifier):
        return ReservationService(ReservationRepository(self.path), PlaceCatalog([PLACE, APPROVAL_PLACE]),
                                  notifier, clock=lambda: self.now)

    def _state(self, reservation_id):
        return next(r.state for r in self.service.reservations() if r.id == reservation_id)

    def test_confirm_on_approval_place_waits_and_later_approve_confirms(self):
        x = self.service.create(APPROVAL_PLACE.id, ALICE, T0, T0 + H)
        self.assertEqual(self.service.confirm(x.id).state, State.PENDING_APPROVAL)

        # The initiating request is over: a new instance makes the later decision.
        self.service.close()
        self.now = T0 - H
        self.service = self._open(self.notifier)
        self.assertEqual(self.service.approve(x.id).state, State.CONFIRMED)

        self.assertEqual(self.notifier.sent, [(x.id, State.PENDING_APPROVAL), (x.id, State.CONFIRMED)])

    def test_approve_with_conflict_is_rolled_back_and_not_notified(self):
        x = self.service.create(APPROVAL_PLACE.id, ALICE, T0, T0 + H)
        y = self.service.create(APPROVAL_PLACE.id, BOB, T0 + H / 2, T0 + 2 * H)
        self.service.confirm(x.id)
        self.service.confirm(y.id)
        self.service.approve(y.id)
        self.notifier.sent.clear()

        with self.assertRaises(RuleViolation):
            self.service.approve(x.id)

        self.assertEqual(self._state(x.id), State.PENDING_APPROVAL)
        self.assertEqual(self.notifier.sent, [])

    def test_failing_notification_does_not_undo_the_approval(self):
        x = self.service.create(APPROVAL_PLACE.id, ALICE, T0, T0 + H)
        self.service.confirm(x.id)
        self.service.close()
        self.service = self._open(FailingNotifier())

        with self.assertLogs("parking.service", "ERROR"):
            self.assertEqual(self.service.approve(x.id).state, State.CONFIRMED)

        self.assertEqual(self._state(x.id), State.CONFIRMED)

    def test_expire_due_expires_only_undecided_requests_after_start(self):
        due = self.service.create(APPROVAL_PLACE.id, ALICE, T0, T0 + H)
        later = self.service.create(APPROVAL_PLACE.id, BOB, T0 + 3 * H, T0 + 4 * H)
        draft = self.service.create(PLACE.id, ALICE, T0, T0 + H)
        self.service.confirm(due.id)
        self.service.confirm(later.id)
        self.notifier.sent.clear()

        self.now = T0
        expired = self.service.expire_due()

        self.assertEqual([r.id for r in expired], [due.id])
        self.assertEqual(self._state(due.id), State.EXPIRED)
        self.assertEqual(self._state(later.id), State.PENDING_APPROVAL)
        self.assertEqual(self._state(draft.id), State.DRAFT)
        self.assertEqual(self.notifier.sent, [(due.id, State.EXPIRED)])

    def test_service_clock_decides_the_start_boundary(self):
        x = self.service.create(PLACE.id, ALICE, T0, T0 + H)
        self.now = T0
        with self.assertRaises(RuleViolation):
            self.service.confirm(x.id)
        self.assertEqual(self._state(x.id), State.DRAFT)

    def test_place_comes_from_the_catalog(self):
        with self.assertRaises(RuleViolation):
            self.service.create("unknown", ALICE, T0, T0 + H)
        self.assertTrue(self.service.check(PLACE.id, T0, T0 + H))


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime, timedelta, timezone

from parking.domain import (
    ParkingPlace, User, State, RuleViolation,
    create_reservation, confirm, approve, reject, expire_if_due, cancel, is_available,
)

PLACE = ParkingPlace("p1", "A-12")
APPROVAL_PLACE = ParkingPlace("p2", "VIP-01", requires_approval=True)
ALICE = User("u1", "Alice")
BOB = User("u2", "Bob")
T0 = datetime(2026, 9, 14, 8, tzinfo=timezone.utc)
H = timedelta(hours=1)
BEFORE_START = T0 - H
AFTER_START = T0 + H
NOW = T0 - 2 * H  # BR-05: fixed "now" used to create reservations, always >= 1h before T0


class OP01CreateReservation(unittest.TestCase):
    def test_valid_interval_creates_draft(self):
        r = create_reservation(PLACE, ALICE, T0, T0 + 2 * H, NOW)
        self.assertEqual(r.state, State.DRAFT)

    def test_start_equals_end_is_rejected(self):
        with self.assertRaises(RuleViolation):
            create_reservation(PLACE, ALICE, T0, T0, NOW)

    def test_longer_than_24_hours_is_rejected(self):
        create_reservation(PLACE, ALICE, T0, T0 + 24 * H, NOW)  # boundary: exactly 24h is fine
        with self.assertRaises(RuleViolation):
            create_reservation(PLACE, ALICE, T0, T0 + 25 * H, NOW)

    def test_start_less_than_one_hour_from_now_is_rejected(self):
        create_reservation(PLACE, ALICE, NOW + H, NOW + 2 * H, NOW)  # boundary: exactly 1h is fine
        with self.assertRaises(RuleViolation):
            create_reservation(PLACE, ALICE, NOW + 30 * timedelta(minutes=1), NOW + 2 * H, NOW)

    def test_start_in_the_past_is_rejected(self):
        with self.assertRaises(RuleViolation):
            create_reservation(PLACE, ALICE, NOW - H, NOW + H, NOW)


class OP02CheckAvailability(unittest.TestCase):
    def setUp(self):
        self.confirmed = create_reservation(PLACE, ALICE, T0, T0 + H, NOW)
        confirm(self.confirmed, [], PLACE, NOW)

    def test_disjoint_before_is_available(self):
        self.assertTrue(is_available(PLACE.id, T0 - H, T0, [self.confirmed]))

    def test_overlapping_is_unavailable(self):
        self.assertFalse(is_available(PLACE.id, T0 + 30 * timedelta(minutes=1), T0 + 90 * timedelta(minutes=1), [self.confirmed]))

    def test_touching_at_end_is_available(self):
        self.assertTrue(is_available(PLACE.id, T0 + H, T0 + 2 * H, [self.confirmed]))

    def test_invalid_interval_is_rejected(self):
        with self.assertRaises(RuleViolation):
            is_available(PLACE.id, T0 + H, T0 + H, [self.confirmed])

    def test_pending_approval_does_not_block(self):
        pending = create_reservation(APPROVAL_PLACE, BOB, T0, T0 + H, NOW)
        confirm(pending, [], APPROVAL_PLACE, NOW)
        self.assertEqual(pending.state, State.PENDING_APPROVAL)
        self.assertTrue(is_available(APPROVAL_PLACE.id, T0, T0 + H, [pending]))


class OP03ConfirmReservation(unittest.TestCase):
    def test_confirm_without_overlap_succeeds(self):
        r = create_reservation(PLACE, ALICE, T0, T0 + 2 * H, NOW)
        confirm(r, [], PLACE, NOW)
        self.assertEqual(r.state, State.CONFIRMED)

    def test_confirm_with_overlap_is_rejected_and_stays_draft(self):
        a = create_reservation(PLACE, ALICE, T0, T0 + 2 * H, NOW)
        confirm(a, [], PLACE, NOW)
        b = create_reservation(PLACE, BOB, T0 + H, T0 + 3 * H, NOW)
        with self.assertRaises(RuleViolation):
            confirm(b, [a], PLACE, NOW)
        self.assertEqual(b.state, State.DRAFT)

    def test_touching_reservations_can_both_be_confirmed(self):
        a = create_reservation(PLACE, ALICE, T0, T0 + 2 * H, NOW)
        confirm(a, [], PLACE, NOW)
        c = create_reservation(PLACE, BOB, T0 + 2 * H, T0 + 3 * H, NOW)
        confirm(c, [a], PLACE, NOW)
        self.assertEqual(c.state, State.CONFIRMED)

    def test_cannot_confirm_a_non_draft_reservation(self):
        r = create_reservation(PLACE, ALICE, T0, T0 + H, NOW)
        confirm(r, [], PLACE, NOW)
        with self.assertRaises(RuleViolation):
            confirm(r, [], PLACE, NOW)

    def test_cannot_confirm_after_start(self):
        r = create_reservation(PLACE, ALICE, T0, T0 + H, NOW)
        with self.assertRaises(RuleViolation):
            confirm(r, [], PLACE, T0)  # boundary: now == start is already too late
        self.assertEqual(r.state, State.DRAFT)

    def test_confirm_on_approval_place_goes_pending_not_confirmed(self):
        r = create_reservation(APPROVAL_PLACE, ALICE, T0, T0 + H, NOW)
        confirm(r, [], APPROVAL_PLACE, NOW)
        self.assertEqual(r.state, State.PENDING_APPROVAL)


class OP05ApproveReservation(unittest.TestCase):
    def test_approve_without_overlap_confirms(self):
        r = create_reservation(APPROVAL_PLACE, ALICE, T0, T0 + H, NOW)
        confirm(r, [], APPROVAL_PLACE, NOW)
        approve(r, [], NOW)
        self.assertEqual(r.state, State.CONFIRMED)

    def test_approve_with_new_overlap_is_rejected_and_stays_pending(self):
        a = create_reservation(APPROVAL_PLACE, ALICE, T0, T0 + 2 * H, NOW)
        confirm(a, [], APPROVAL_PLACE, NOW)
        approve(a, [], NOW)
        b = create_reservation(APPROVAL_PLACE, BOB, T0 + H, T0 + 3 * H, NOW)
        confirm(b, [a], APPROVAL_PLACE, NOW)
        with self.assertRaises(RuleViolation):
            approve(b, [a], NOW)
        self.assertEqual(b.state, State.PENDING_APPROVAL)

    def test_cannot_approve_a_draft_reservation(self):
        r = create_reservation(APPROVAL_PLACE, ALICE, T0, T0 + H, NOW)
        with self.assertRaises(RuleViolation):
            approve(r, [], NOW)

    def test_cannot_approve_or_reject_after_start(self):
        r = create_reservation(APPROVAL_PLACE, ALICE, T0, T0 + H, NOW)
        confirm(r, [], APPROVAL_PLACE, NOW)
        with self.assertRaises(RuleViolation):
            approve(r, [], T0)
        with self.assertRaises(RuleViolation):
            reject(r, T0)
        self.assertEqual(r.state, State.PENDING_APPROVAL)

    def test_reject_moves_pending_to_rejected(self):
        r = create_reservation(APPROVAL_PLACE, ALICE, T0, T0 + H, NOW)
        confirm(r, [], APPROVAL_PLACE, NOW)
        reject(r, NOW)
        self.assertEqual(r.state, State.REJECTED)

    def test_expire_after_start_time_passed(self):
        r = create_reservation(APPROVAL_PLACE, ALICE, T0, T0 + H, NOW)
        confirm(r, [], APPROVAL_PLACE, NOW)
        self.assertFalse(expire_if_due(r, BEFORE_START))
        self.assertEqual(r.state, State.PENDING_APPROVAL)
        self.assertTrue(expire_if_due(r, AFTER_START))
        self.assertEqual(r.state, State.EXPIRED)


class OP04CancelReservation(unittest.TestCase):
    def test_cancel_draft_before_start(self):
        r = create_reservation(PLACE, ALICE, T0, T0 + H, NOW)
        cancel(r, BEFORE_START)
        self.assertEqual(r.state, State.CANCELLED)

    def test_cancel_confirmed_before_start_frees_the_place(self):
        r = create_reservation(PLACE, ALICE, T0, T0 + H, NOW)
        confirm(r, [], PLACE, NOW)
        cancel(r, BEFORE_START)
        self.assertEqual(r.state, State.CANCELLED)
        self.assertTrue(is_available(PLACE.id, T0, T0 + H, [r]))

    def test_cancel_pending_approval_before_start(self):
        r = create_reservation(APPROVAL_PLACE, ALICE, T0, T0 + H, NOW)
        confirm(r, [], APPROVAL_PLACE, NOW)
        cancel(r, BEFORE_START)
        self.assertEqual(r.state, State.CANCELLED)

    def test_cancel_after_start_is_rejected(self):
        r = create_reservation(PLACE, ALICE, T0, T0 + H, NOW)
        confirm(r, [], PLACE, NOW)
        with self.assertRaises(RuleViolation):
            cancel(r, AFTER_START)
        self.assertEqual(r.state, State.CONFIRMED)

    def test_cannot_cancel_twice(self):
        r = create_reservation(PLACE, ALICE, T0, T0 + H, NOW)
        cancel(r, BEFORE_START)
        with self.assertRaises(RuleViolation):
            cancel(r, BEFORE_START)


if __name__ == "__main__":
    unittest.main()

"""Tests derived from the C05 detailed design (docs/architecture-and-decisions.md).

Each test names the DD decision it verifies: a decision-table row (DT-A*,
DT-C*, DT-P*), a contract clause (DD-4), a responsibility (DR-*) or the order
of checks (DD-8). Unit level: the pure rules of domain.py. Component level:
ReservationService with a real ReservationRepository on a temporary SQLite
file. Concurrency (REQ-04, REQ-06) is the integration level and stays in
test_concurrency.py.
"""
import copy
import dataclasses
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from parking.domain import (
    Availability, AvailabilityResult, ParkingPlace, Reservation, RuleViolation, State, User,
    approve, confirm, evaluate_availability, is_available,
)
from parking.places import PlaceCatalog
from parking.repository import ReservationRepository
from parking.service import ReservationService

P = ParkingPlace("p1", "A-12")
Q = ParkingPlace("p2", "A-13")
VIP = ParkingPlace("p5", "VIP-01", requires_approval=True)
ALICE = User("u1", "Alice")
BOB = User("u2", "Bob")
DAY = datetime(2026, 9, 14, tzinfo=timezone.utc)


def at(hour: int, minute: int = 0) -> datetime:
    return DAY + timedelta(hours=hour, minutes=minute)


def reservation(place, start, end, state=State.CONFIRMED, user=ALICE) -> Reservation:
    return Reservation(place.id, user.id, start, end, state)


class DTAvailability(unittest.TestCase):
    """DD-8 decision table for evaluate_availability: B = 10:00-11:00 on P."""

    def setUp(self):
        self.b = reservation(P, at(10), at(11))

    def evaluate(self, start, end, existing=None, exclude_id=None):
        return evaluate_availability(P.id, start, end, [self.b] if existing is None else existing, exclude_id)

    def test_DT_A1_touching_on_the_left_is_available(self):
        self.assertEqual(self.evaluate(at(9), at(10)), AvailabilityResult(Availability.AVAILABLE))

    def test_DT_A2_touching_on_the_right_is_available(self):
        self.assertEqual(self.evaluate(at(11), at(12)), AvailabilityResult(Availability.AVAILABLE))

    def test_DT_A3_partial_overlap_is_unavailable(self):
        self.assertEqual(self.evaluate(at(10, 30), at(11, 30)),
                         AvailabilityResult(Availability.UNAVAILABLE, (self.b.id,)))

    def test_DT_A4_query_inside_is_unavailable(self):
        self.assertEqual(self.evaluate(at(10, 15), at(10, 45)),
                         AvailabilityResult(Availability.UNAVAILABLE, (self.b.id,)))

    def test_DT_A5_query_containing_is_unavailable(self):
        self.assertEqual(self.evaluate(at(9), at(12)),
                         AvailabilityResult(Availability.UNAVAILABLE, (self.b.id,)))

    def test_DT_A6_same_interval_is_unavailable(self):
        self.assertEqual(self.evaluate(at(10), at(11)),
                         AvailabilityResult(Availability.UNAVAILABLE, (self.b.id,)))

    def test_DT_A7_confirmed_on_another_place_does_not_block(self):
        other = reservation(Q, at(10), at(11))
        self.assertEqual(self.evaluate(at(10, 30), at(11, 30), [other]), AvailabilityResult(Availability.AVAILABLE))

    def test_DT_A8_only_confirmed_blocks(self):
        for state in State:
            if state == State.CONFIRMED:
                continue
            with self.subTest(state=state):
                b = reservation(P, at(10), at(11), state)
                self.assertEqual(self.evaluate(at(10, 30), at(11, 30), [b]),
                                 AvailabilityResult(Availability.AVAILABLE))

    def test_DT_A9_excluded_reservation_does_not_block_itself(self):
        self.assertEqual(self.evaluate(at(10, 30), at(11, 30), exclude_id=self.b.id),
                         AvailabilityResult(Availability.AVAILABLE))
        self.assertEqual(self.evaluate(at(10, 30), at(11, 30)).outcome, Availability.UNAVAILABLE)

    def test_DT_A10_all_conflicts_in_existing_order(self):
        c = reservation(P, at(10, 45), at(12), user=BOB)
        self.assertEqual(self.evaluate(at(9, 30), at(11, 30), [self.b, c]),
                         AvailabilityResult(Availability.UNAVAILABLE, (self.b.id, c.id)))
        self.assertEqual(self.evaluate(at(9, 30), at(11, 30), [c, self.b]).conflicts, (c.id, self.b.id))

    def test_DT_A11_invalid_interval_is_an_outcome_before_any_comparison(self):
        # 10:30-10:30 lies inside B: no conflict is reported for an invalid interval.
        for start, end in [(at(11), at(11)), (at(11), at(10)), (at(10, 30), at(10, 30))]:
            with self.subTest(start=start, end=end):
                self.assertEqual(self.evaluate(start, end), AvailabilityResult(Availability.INVALID_INTERVAL))

    def test_DT_A12_no_reservations_is_available(self):
        self.assertEqual(self.evaluate(at(10, 30), at(11, 30), []), AvailabilityResult(Availability.AVAILABLE))

    def test_DD4_BR01_matches_a_shared_hour_on_a_grid(self):
        """BR-01 against an independent oracle: on a whole-hour grid two
        [start, end) intervals overlap iff they share an hour slot."""
        hours = range(8, 13)
        intervals = [(s, e) for s in hours for e in hours if s < e]
        for bs, be in intervals:
            b = reservation(P, at(bs), at(be))
            for qs, qe in intervals:
                with self.subTest(b=(bs, be), query=(qs, qe)):
                    shared = any(bs <= h < be and qs <= h < qe for h in hours)
                    result = evaluate_availability(P.id, at(qs), at(qe), [b])
                    self.assertEqual(result.outcome == Availability.UNAVAILABLE, shared)
                    self.assertEqual(result.conflicts, (b.id,) if shared else ())

    def test_DD4_existing_reservations_are_not_changed(self):
        existing = [self.b, reservation(P, at(8), at(9), State.PENDING_APPROVAL, BOB)]
        before = copy.deepcopy(existing)
        self.evaluate(at(8), at(12), existing)
        self.assertEqual(existing, before)

    def test_DD4_result_is_an_immutable_value(self):
        result = self.evaluate(at(10), at(11))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.outcome = Availability.AVAILABLE

    def test_DD4_naive_datetime_violates_the_precondition(self):
        with self.assertRaises(TypeError):
            self.evaluate(datetime(2026, 9, 14, 10, 30), datetime(2026, 9, 14, 11, 30))


class DD4IsAvailable(unittest.TestCase):
    """DR-6: is_available is the yes/no answer of OP-02 over evaluate_availability."""

    def setUp(self):
        self.b = reservation(P, at(10), at(11))

    def test_DD4_available_is_true_unavailable_is_false(self):
        self.assertTrue(is_available(P.id, at(11), at(12), [self.b]))
        self.assertFalse(is_available(P.id, at(10, 30), at(11, 30), [self.b]))

    def test_DT_A11_invalid_interval_gets_no_answer(self):
        with self.assertRaises(RuleViolation):
            is_available(P.id, at(11), at(11), [self.b])


class DTConfirm(unittest.TestCase):
    """DD-8 decision table for confirm: X = 08:00-09:00, now 07:00 unless stated."""

    def draft(self, place=P):
        return reservation(place, at(8), at(9), State.DRAFT)

    def blocker(self, place=P):
        return reservation(place, at(8, 30), at(10), user=BOB)

    def test_DT_C1_free_place_confirms(self):
        x = self.draft()
        confirm(x, [x], P, at(7))  # the service passes for_place, which contains X itself
        self.assertEqual(x.state, State.CONFIRMED)

    def test_DT_C2_conflict_is_named_and_draft_stays(self):
        x, y = self.draft(), self.blocker()
        with self.assertRaises(RuleViolation) as caught:
            confirm(x, [x, y], P, at(7))
        self.assertIn(y.id, str(caught.exception))
        self.assertEqual(x.state, State.DRAFT)

    def test_DT_C3_approval_place_goes_pending(self):
        x = self.draft(VIP)
        confirm(x, [x], VIP, at(7))
        self.assertEqual(x.state, State.PENDING_APPROVAL)

    def test_DT_C4_approval_place_defers_the_conflict_check(self):
        x = self.draft(VIP)
        confirm(x, [x, self.blocker(VIP)], VIP, at(7))
        self.assertEqual(x.state, State.PENDING_APPROVAL)

    def test_DT_C5_at_or_after_start_is_too_late_even_with_a_conflict(self):
        # DD-8 order: time is checked before the approval branch and the conflict.
        for place in (P, VIP):
            for now in (at(8), at(8, 30)):
                with self.subTest(place=place.label, now=now):
                    x = self.draft(place)
                    with self.assertRaises(RuleViolation) as caught:
                        confirm(x, [x, self.blocker(place)], place, now)
                    self.assertIn("already started", str(caught.exception))
                    self.assertEqual(x.state, State.DRAFT)

    def test_DT_C6_only_draft_can_be_confirmed_and_state_is_checked_first(self):
        # DD-8 order: the state is checked before the time, so after start
        # the error still names the state.
        for state in State:
            if state == State.DRAFT:
                continue
            for now in (at(7), at(8)):
                with self.subTest(state=state, now=now):
                    x = reservation(P, at(8), at(9), state)
                    with self.assertRaises(RuleViolation) as caught:
                        confirm(x, [x], P, now)
                    self.assertIn(state.value, str(caught.exception))
                    self.assertEqual(x.state, state)

    def test_DT_A11_invalid_interval_of_the_reservation_is_rejected(self):
        x = reservation(P, at(9), at(8), State.DRAFT)
        with self.assertRaises(RuleViolation) as caught:
            confirm(x, [x], P, at(7))
        self.assertIn("end must be after start", str(caught.exception))
        self.assertEqual(x.state, State.DRAFT)

    def test_DR2_stored_copy_of_itself_does_not_block(self):
        x = self.draft()
        stored = dataclasses.replace(x, state=State.CONFIRMED)  # same id
        confirm(x, [stored], P, at(7))
        self.assertEqual(x.state, State.CONFIRMED)

    def test_DD4_naive_now_violates_the_precondition_and_changes_nothing(self):
        x = self.draft()
        with self.assertRaises(TypeError):
            confirm(x, [x], P, datetime(2026, 9, 14, 7))
        self.assertEqual(x.state, State.DRAFT)


class DTApprove(unittest.TestCase):
    """DD-8 decision table for approve: X = 08:00-09:00 on VIP-01, now 07:00 unless stated."""

    def pending(self):
        return reservation(VIP, at(8), at(9), State.PENDING_APPROVAL)

    def test_DT_P1_no_confirmed_conflict_confirms_other_requests_do_not_block(self):
        x = self.pending()
        other_request = reservation(VIP, at(8, 30), at(10), State.PENDING_APPROVAL, BOB)
        approve(x, [x, other_request], at(7))
        self.assertEqual(x.state, State.CONFIRMED)

    def test_DT_P2_every_conflict_is_named_and_request_stays_pending(self):
        x = self.pending()
        y = reservation(VIP, at(8, 30), at(10), user=BOB)
        z = reservation(VIP, at(7), at(8, 15), user=BOB)
        with self.assertRaises(RuleViolation) as caught:
            approve(x, [x, y, z], at(7))
        self.assertIn(y.id, str(caught.exception))
        self.assertIn(z.id, str(caught.exception))
        self.assertEqual(x.state, State.PENDING_APPROVAL)

    def test_DT_P3_at_or_after_start_is_too_late_even_with_a_conflict(self):
        for now in (at(8), at(8, 30)):
            with self.subTest(now=now):
                x = self.pending()
                y = reservation(VIP, at(8, 30), at(10), user=BOB)
                with self.assertRaises(RuleViolation) as caught:
                    approve(x, [x, y], now)
                self.assertIn("already started", str(caught.exception))
                self.assertEqual(x.state, State.PENDING_APPROVAL)

    def test_DT_P4_only_pending_approval_can_be_approved_and_state_is_checked_first(self):
        for state in State:
            if state == State.PENDING_APPROVAL:
                continue
            for now in (at(7), at(8)):
                with self.subTest(state=state, now=now):
                    x = reservation(VIP, at(8), at(9), state)
                    with self.assertRaises(RuleViolation) as caught:
                        approve(x, [x], now)
                    self.assertIn(state.value, str(caught.exception))
                    self.assertEqual(x.state, state)

    def test_DT_A11_invalid_interval_of_the_request_is_rejected(self):
        x = reservation(VIP, at(9), at(8), State.PENDING_APPROVAL)
        with self.assertRaises(RuleViolation) as caught:
            approve(x, [x], at(7))
        self.assertIn("end must be after start", str(caught.exception))
        self.assertEqual(x.state, State.PENDING_APPROVAL)


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def reservation_changed(self, reservation):
        self.sent.append((reservation.id, reservation.state))


class DDServiceCollaboration(unittest.TestCase):
    """Component level: the rules inside ReservationService and a real store
    (DD-4 "Kolaborace s hraničním prvkem")."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "dd.db")
        self.now = at(6)
        self.notifier = RecordingNotifier()
        self.service = ReservationService(ReservationRepository(self.path), PlaceCatalog([P, VIP]),
                                          self.notifier, clock=lambda: self.now)

    def tearDown(self):
        self.service.close()
        self.dir.cleanup()

    def _stored(self):
        repo = ReservationRepository(self.path)
        try:
            return {r.id: r.state for r in repo.all()}
        finally:
            repo.close()

    def test_DT_C1_for_place_contains_the_reservation_itself_and_confirm_still_succeeds(self):
        x = self.service.create(P.id, ALICE, at(8), at(9))
        repo = ReservationRepository(self.path)
        try:
            self.assertIn(x.id, [r.id for r in repo.for_place(P.id)])
        finally:
            repo.close()
        self.assertEqual(self.service.confirm(x.id).state, State.CONFIRMED)
        self.assertEqual(self._stored()[x.id], State.CONFIRMED)

    def test_DT_C4_DT_P2_conflict_is_deferred_to_approve_and_named_there(self):
        # H1 from C03: X is approved first, Y was requested on the same VIP place.
        x = self.service.create(VIP.id, ALICE, at(8), at(9))
        y = self.service.create(VIP.id, BOB, at(8, 30), at(10))
        self.service.confirm(x.id)
        self.service.approve(x.id)
        self.assertEqual(self.service.confirm(y.id).state, State.PENDING_APPROVAL)  # DT-C4
        self.notifier.sent.clear()

        with self.assertRaises(RuleViolation) as caught:
            self.service.approve(y.id)  # DT-P2

        self.assertIn(x.id, str(caught.exception))
        self.assertEqual(self._stored(), {x.id: State.CONFIRMED, y.id: State.PENDING_APPROVAL})
        self.assertEqual(self.notifier.sent, [])

    def test_DT_A8_only_confirmed_rows_in_the_store_block_check(self):
        confirmed = self.service.create(P.id, ALICE, at(8), at(9))
        self.service.confirm(confirmed.id)
        self.service.create(P.id, BOB, at(10), at(11))  # stays DRAFT
        cancelled = self.service.create(P.id, BOB, at(12), at(13))
        self.service.confirm(cancelled.id)
        self.service.cancel(cancelled.id)

        self.assertFalse(self.service.check(P.id, at(8, 30), at(9, 30)))
        self.assertTrue(self.service.check(P.id, at(9), at(10)))  # DT-A2, touching
        self.assertTrue(self.service.check(P.id, at(10), at(11)))  # DRAFT
        self.assertTrue(self.service.check(P.id, at(12), at(13)))  # CANCELLED

    def test_DT_A11_check_rejects_an_invalid_interval(self):
        with self.assertRaises(RuleViolation):
            self.service.check(P.id, at(9), at(9))


if __name__ == "__main__":
    unittest.main()

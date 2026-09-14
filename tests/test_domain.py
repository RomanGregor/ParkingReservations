import unittest
from datetime import datetime, timedelta, timezone

from parking.domain import (
    ParkingPlace, User, State, RuleViolation,
    create_reservation, confirm, cancel, is_available,
)

PLACE = ParkingPlace("p1", "A-12")
ALICE = User("u1", "Alice")
BOB = User("u2", "Bob")
T0 = datetime(2026, 9, 14, 8, tzinfo=timezone.utc)
H = timedelta(hours=1)


class DomainRules(unittest.TestCase):
    def test_create_confirm_cancel(self):
        r = create_reservation(PLACE, ALICE, T0, T0 + 2 * H)
        self.assertEqual(r.state, State.DRAFT)
        confirm(r, [])
        self.assertEqual(r.state, State.CONFIRMED)
        cancel(r)
        self.assertEqual(r.state, State.CANCELLED)
        with self.assertRaises(RuleViolation):
            cancel(r)

    def test_confirmed_reservations_must_not_overlap(self):
        a = create_reservation(PLACE, ALICE, T0, T0 + 2 * H)
        confirm(a, [])
        b = create_reservation(PLACE, BOB, T0 + H, T0 + 3 * H)
        self.assertFalse(is_available(PLACE.id, b.start, b.end, [a]))
        with self.assertRaises(RuleViolation):
            confirm(b, [a])
        c = create_reservation(PLACE, BOB, T0 + 2 * H, T0 + 3 * H)  # touching is fine
        confirm(c, [a])

    def test_max_24_hours(self):
        create_reservation(PLACE, ALICE, T0, T0 + 24 * H)
        with self.assertRaises(RuleViolation):
            create_reservation(PLACE, ALICE, T0, T0 + 25 * H)


if __name__ == "__main__":
    unittest.main()

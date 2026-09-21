"""C01 engineering spike A: Reservation -> real DB -> load again -> verify."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from parking.domain import ParkingPlace, User, State, RuleViolation, confirm, create_reservation
from parking.repository import ReservationRepository

PLACE = ParkingPlace("p1", "A-12")
ALICE = User("u1", "Alice")
T0 = datetime(2026, 9, 14, 8, tzinfo=timezone.utc)
NOW = T0 - timedelta(hours=2)  # BR-05: fixed "now" used to create reservations


class PersistenceSpike(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "spike.db")

    def tearDown(self):
        self.dir.cleanup()

    def test_reservation_survives_round_trip(self):
        original = create_reservation(PLACE, ALICE, T0, T0 + timedelta(hours=2), NOW)
        confirm(original, [], PLACE, NOW)

        repo = ReservationRepository(self.path)
        repo.save(original)
        repo.close()

        # fresh connection = the data really is in the file, not in memory
        repo = ReservationRepository(self.path)
        loaded = repo.get(original.id)
        repo.close()

        self.assertEqual(loaded, original)
        self.assertEqual(loaded.state, State.CONFIRMED)
        self.assertEqual(loaded.start.tzinfo, timezone.utc)

    def test_loaded_reservations_feed_the_overlap_rule(self):
        repo = ReservationRepository(self.path)
        a = create_reservation(PLACE, ALICE, T0, T0 + timedelta(hours=2), NOW)
        confirm(a, [], PLACE, NOW)
        repo.save(a)

        b = create_reservation(PLACE, ALICE, T0 + timedelta(hours=1), T0 + timedelta(hours=3), NOW)
        with self.assertRaises(RuleViolation):
            confirm(b, repo.for_place(PLACE.id), PLACE, NOW)
        repo.close()


if __name__ == "__main__":
    unittest.main()

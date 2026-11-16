"""C08 CR-08 / C09: EV charging place EV-01 with BR-06 (at most 4 h).

Oracles come from the acceptance conditions AC-01..AC-06 of the Change Card
(docs/intent-and-change.md), written before this code. The HTTP and service
tests use a real SQLite file and check the stored state over their own
sqlite3 connection, not through the code under test.
"""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from parking import domain
from parking.domain import RuleViolation, State, User
from parking.http_api import make_server
from parking.places import PlaceCatalog
from parking.repository import ReservationRepository
from parking.service import ReservationService

CATALOG = PlaceCatalog()
EV = CATALOG.find("p6")
A01 = CATALOG.find("p1")
DRIVER = User("tc-driver-1", "TC driver 1")
NOW = datetime(2026, 12, 1, 6, 0, tzinfo=timezone.utc)
H = timedelta(hours=1)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 12, 1, hour, minute, second, tzinfo=timezone.utc)


def stored_rows(path: str) -> list[tuple[str, str, str, str]]:
    db = sqlite3.connect(path)
    try:
        return db.execute('SELECT place_id, start, "end", state FROM reservation ORDER BY start').fetchall()
    finally:
        db.close()


class EvPlaceRule(unittest.TestCase):
    """BR-06 at the domain level (DD-4 create_reservation)."""

    def create(self, place, start, end):
        return domain.create_reservation(place, DRIVER, start, end, NOW)

    def test_ac06_catalog_lists_ev_place_with_4_hours_and_no_approval(self):
        self.assertEqual((EV.label, EV.max_duration, EV.requires_approval), ("EV-01", 4 * H, False))
        self.assertIn(EV, CATALOG.all())

    def test_ac01_two_hours_on_ev_is_a_draft(self):
        self.assertEqual(self.create(EV, at(8), at(10)).state, State.DRAFT)

    def test_ac03_exactly_four_hours_is_allowed(self):
        self.assertEqual(self.create(EV, at(8), at(12)).state, State.DRAFT)

    def test_ac03_four_hours_and_one_second_is_rejected(self):
        with self.assertRaises(RuleViolation) as e:
            self.create(EV, at(8), at(12, 0, 1))
        self.assertIn("4 hours", str(e.exception))

    def test_ac02_four_hours_and_one_minute_is_rejected(self):
        with self.assertRaises(RuleViolation) as e:
            self.create(EV, at(8), at(12, 1))
        self.assertIn("EV-01", str(e.exception))

    def test_ac04_other_places_keep_24_hours(self):
        self.assertEqual(self.create(A01, at(8), at(8) + 24 * H).state, State.DRAFT)
        with self.assertRaises(RuleViolation) as e:
            self.create(A01, at(8), at(8) + 25 * H)
        self.assertIn("24 hours", str(e.exception))
        self.assertTrue(all(p.max_duration == 24 * H for p in CATALOG.all() if p.id != "p6"))


class EvPlaceOverlap(unittest.TestCase):
    """AC-05: BR-02 holds on EV-01 like on any place without approval."""

    def test_ac05_overlap_rejected_and_adjacent_confirmed_on_ev(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ev.db")
            service = ReservationService(ReservationRepository(path), clock=lambda: NOW)
            try:
                b1 = service.create("p6", DRIVER, at(10), at(11))
                service.confirm(b1.id)
                overlapping = service.create("p6", DRIVER, at(10, 30), at(11, 30))
                with self.assertRaises(RuleViolation):
                    service.confirm(overlapping.id)
                adjacent = service.create("p6", DRIVER, at(11), at(12))
                service.confirm(adjacent.id)
            finally:
                service.close()
            self.assertEqual([r[3] for r in stored_rows(path)], ["CONFIRMED", "DRAFT", "CONFIRMED"])


class EvPlaceInGui(unittest.TestCase):
    """AC-06, GUI part: EV-01 is offered in the place list and a p6
    reservation is shown with its label (gui.refresh looks labels up)."""

    def test_ac06_gui_offers_ev_01_and_shows_its_reservation(self):
        try:
            import tkinter
            from tkinter import ttk
            from unittest import mock
            from parking.gui import App
        except ImportError:
            self.skipTest("tkinter is not available")
        with tempfile.TemporaryDirectory() as d:
            service = ReservationService(ReservationRepository(os.path.join(d, "gui.db")), clock=lambda: NOW)
            try:
                service.create("p6", DRIVER, at(8), at(12))
                with mock.patch("parking.gui.messagebox"):
                    try:
                        app = App(service)
                    except tkinter.TclError:
                        self.skipTest("no display for tkinter")
                    app.withdraw()
                    try:
                        def widgets(w):
                            yield w
                            for child in w.winfo_children():
                                yield from widgets(child)
                        boxes = [w for w in widgets(app) if isinstance(w, ttk.Combobox)]
                        self.assertEqual(len(boxes), 1)
                        self.assertIn("EV-01", boxes[0].cget("values"))
                        rows = [app.table.item(i, "values") for i in app.table.get_children()]
                        self.assertEqual([(r[1], r[5]) for r in rows], [("EV-01", "DRAFT")])
                    finally:
                        app.destroy()
            finally:
                service.close()


class EvPlaceOverHttp(unittest.TestCase):
    """TC-CR08-03: AC-01..AC-03 through the real HTTP adapter and SQLite file."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "api.db")
        self.server = make_server(self.path, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        # The API uses the real clock (BR-05), so the interval lies 3 h ahead.
        self.start = (datetime.now(timezone.utc) + 3 * H).replace(minute=0, second=0, microsecond=0)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.dir.cleanup()

    def post(self, duration: timedelta):
        body = {"place_id": "p6", "user_id": "tc-driver-1",
                "start": self.start.isoformat(), "end": (self.start + duration).isoformat()}
        req = urllib.request.Request(self.base + "/reservations", data=json.dumps(body).encode(),
                                     method="POST", headers={"Content-Type": "application/json"})
        try:
            with OPENER.open(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_tc_cr08_03_boundary_over_http_with_independent_read(self):
        status, body = self.post(4 * H)  # AC-03: exactly 4 h
        self.assertEqual((status, body["state"]), (201, "DRAFT"))
        self.assertEqual(stored_rows(self.path),
                         [("p6", self.start.isoformat(), (self.start + 4 * H).isoformat(), "DRAFT")])

        for too_long in (4 * H + timedelta(seconds=1), 4 * H + timedelta(minutes=1)):  # AC-03, AC-02
            status, body = self.post(too_long)
            self.assertEqual(status, 422)
            self.assertIn("4 hours", body["error"])
        self.assertEqual(len(stored_rows(self.path)), 1)  # nothing stored by the rejected requests


if __name__ == "__main__":
    unittest.main()

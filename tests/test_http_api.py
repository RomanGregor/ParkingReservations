"""CP1 walking skeleton end to end: HTTP request -> ReservationService ->
SQLite -> read back by id (README), plus the request checks of the adapter.

The server runs in a thread of the test process and listens on a free local
port; every request opens its own service and connection, like the app does.
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from parking.domain import State
from parking.http_api import make_server
from parking.repository import ReservationRepository
from parking.service import open_service

# No proxy for the local test server, whatever the machine's settings are.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _in_hours(hours: int) -> datetime:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(minute=0, second=0, microsecond=0)


class HttpApi(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "api.db")
        self.server = make_server(self.path, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.dir.cleanup()

    def _request(self, method: str, path: str, body=None, raw: bytes | None = None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with OPENER.open(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def _stored(self):
        repo = ReservationRepository(self.path)
        try:
            return repo.all()
        finally:
            repo.close()

    def _create_body(self, **overrides):
        start = _in_hours(3)
        body = {"place_id": "p1", "user_id": "alice",
                "start": start.isoformat(), "end": (start + timedelta(hours=2)).isoformat()}
        body.update(overrides)
        return body

    def test_cp1_post_creates_a_draft_that_is_read_back_by_id(self):
        # README walking skeleton: POST -> persist as DRAFT -> {"id", "state"} -> read back.
        body = self._create_body()
        status, created = self._request("POST", "/reservations", body)
        self.assertEqual(status, 201)
        self.assertEqual(created["state"], "DRAFT")

        status, read = self._request("GET", f"/reservations/{created['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(read, {"id": created["id"], "place_id": "p1", "user_id": "alice",
                                "start": body["start"], "end": body["end"], "state": "DRAFT"})

    def test_the_gui_side_service_continues_with_a_reservation_created_over_http(self):
        # AD-04: both clients share Reservation Management and one SQLite file.
        _, created = self._request("POST", "/reservations", self._create_body())
        service = open_service(self.path)
        try:
            self.assertEqual(service.confirm(created["id"]).state, State.CONFIRMED)
        finally:
            service.close()
        self.assertEqual(self._request("GET", f"/reservations/{created['id']}")[1]["state"], "CONFIRMED")

    def test_time_with_an_offset_is_stored_in_utc(self):
        start = _in_hours(3).astimezone(timezone(timedelta(hours=1)))
        body = self._create_body(start=start.isoformat(), end=(start + timedelta(hours=1)).isoformat())
        _, created = self._request("POST", "/reservations", body)
        _, read = self._request("GET", f"/reservations/{created['id']}")
        self.assertEqual(read["start"], start.astimezone(timezone.utc).isoformat())

    def test_time_without_utc_offset_is_400_and_nothing_is_stored(self):
        # C05 design risk: a naive datetime must not reach the domain (DD-4 Pre).
        naive = _in_hours(3).replace(tzinfo=None)
        status, body = self._request("POST", "/reservations", self._create_body(start=naive.isoformat()))
        self.assertEqual(status, 400)
        self.assertIn("UTC offset", body["error"])
        self.assertEqual(self._stored(), [])

    def test_business_rule_violation_is_422_and_nothing_is_stored(self):
        start = _in_hours(3)
        body = self._create_body(end=(start + timedelta(hours=25)).isoformat())  # BR-04: max 24 h
        status, answer = self._request("POST", "/reservations", body)
        self.assertEqual(status, 422)
        self.assertIn("24 hours", answer["error"])
        self.assertEqual(self._stored(), [])

    def test_unknown_place_is_422(self):
        status, _ = self._request("POST", "/reservations", self._create_body(place_id="nope"))
        self.assertEqual(status, 422)

    def test_malformed_requests_are_400(self):
        self.assertEqual(self._request("POST", "/reservations", raw=b"{not json")[0], 400)
        self.assertEqual(self._request("POST", "/reservations", body=["a list"])[0], 400)
        incomplete = self._create_body()
        del incomplete["end"]
        status, body = self._request("POST", "/reservations", incomplete)
        self.assertEqual(status, 400)
        self.assertIn("end", body["error"])
        self.assertEqual(self._stored(), [])

    def test_unknown_reservation_and_path_are_404(self):
        self.assertEqual(self._request("GET", "/reservations/missing")[0], 404)
        self.assertEqual(self._request("GET", "/other")[0], 404)
        self.assertEqual(self._request("POST", "/other", self._create_body())[0], 404)


if __name__ == "__main__":
    unittest.main()

"""C10 findings: a database that cannot be opened or is locked exclusively
gives an explicit outcome (StoreUnavailable / StoreBusy) and, over HTTP, a
503 with a log record, instead of a raw sqlite3 error and a dropped
connection (docs/evidence-and-evolution.md, C10)."""
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from parking.http_api import make_server
from parking.repository import ReservationRepository, StoreBusy, StoreUnavailable

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class OpeningTheStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "c10.db")

    def test_exclusive_lock_of_another_connection_is_store_busy(self):
        ReservationRepository(self.path).close()  # the file and table exist
        holder = sqlite3.connect(self.path, isolation_level=None)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            with self.assertRaises(StoreBusy):
                ReservationRepository(self.path, lock_timeout=0.1)
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        ReservationRepository(self.path).close()  # usable again after the lock

    def test_missing_directory_is_store_unavailable(self):
        with self.assertRaises(StoreUnavailable):
            ReservationRepository(os.path.join(self.dir.name, "gone", "c10.db"))


class HttpWithUnavailableStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.server = make_server(os.path.join(self.dir.name, "gone", "c10.db"), port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.dir.cleanup()

    def request(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with OPENER.open(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def test_post_answers_503_and_logs_operation_and_failure(self):
        body = {"place_id": "p1", "user_id": "c10", "start": "2030-01-01T08:00:00+00:00",
                "end": "2030-01-01T09:00:00+00:00"}
        with self.assertLogs("parking.http", "ERROR") as logs:
            status, answer = self.request("POST", "/reservations", body)
        self.assertEqual((status, answer), (503, {"error": "reservation store unavailable"}))
        self.assertIn("POST /reservations failed: cannot open the reservation store", logs.output[0])

    def test_get_answers_503(self):
        with self.assertLogs("parking.http", "ERROR"):
            self.assertEqual(self.request("GET", "/reservations/x")[0], 503)


if __name__ == "__main__":
    unittest.main()

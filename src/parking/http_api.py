"""HTTP API: the second client of Reservation Management (AD-04, CP1 walking skeleton).

A thin adapter over ReservationService, like the GUI (ADR-04): it parses and
checks the request, calls the service and maps the outcome to HTTP. Every
request opens its own service and SQLite connection, as another instance in
G4 would, so the write lock of ADR-04 also orders the API against the GUI.

    POST /reservations       {"place_id", "user_id", "start", "end"}  -> 201 {"id", "state"}
    GET  /reservations/<id>                                           -> 200 reservation

Times are ISO-8601 with a UTC offset (D3). A time without an offset is a 400:
the domain requires timezone-aware datetimes (DD-4), and comparing a naive
with an aware datetime would otherwise fail as a TypeError (C05 design risk).
"""
import json
import logging
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from parking.domain import Reservation, RuleViolation, User
from parking.service import StoreBusy, open_service

log = logging.getLogger("parking.http")

CREATE_FIELDS = ("place_id", "user_id", "start", "end")


class BadRequest(Exception):
    """The request itself is malformed; nothing was read or changed."""


def parse_time(name: str, value) -> datetime:
    if not isinstance(value, str):
        raise BadRequest(f"{name} must be an ISO-8601 string")
    try:
        t = datetime.fromisoformat(value)
    except ValueError:
        raise BadRequest(f"{name} is not an ISO-8601 time") from None
    if t.tzinfo is None:
        raise BadRequest(f"{name} must include a UTC offset, e.g. 2026-11-10T08:00:00+00:00")
    return t.astimezone(timezone.utc)


def reservation_json(r: Reservation) -> dict:
    return {"id": r.id, "place_id": r.place_id, "user_id": r.user_id,
            "start": r.start.isoformat(), "end": r.end.isoformat(), "state": r.state.value}


class ReservationHandler(BaseHTTPRequestHandler):
    db_path = "parking.db"  # make_server sets it on a subclass

    def do_POST(self):
        if self.path.rstrip("/") != "/reservations":
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        try:
            body = self._read_json()
            missing = [f for f in CREATE_FIELDS if f not in body]
            if missing:
                raise BadRequest(f"missing field(s): {', '.join(missing)}")
            place_id, user_id = body["place_id"], body["user_id"]
            if not isinstance(place_id, str) or not isinstance(user_id, str) or not user_id.strip():
                raise BadRequest("place_id and user_id must be non-empty strings")
            start, end = parse_time("start", body["start"]), parse_time("end", body["end"])
            r = self._with_service(lambda s: s.create(place_id, User(user_id, user_id), start, end))
        except BadRequest as e:
            return self._send(HTTPStatus.BAD_REQUEST, {"error": str(e)})
        except RuleViolation as e:
            return self._send(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(e)})
        except StoreBusy as e:  # C04: another instance held the write lock too long
            return self._send(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(e)}, {"Retry-After": "1"})
        self._send(HTTPStatus.CREATED, {"id": r.id, "state": r.state.value})

    def do_GET(self):
        prefix = "/reservations/"
        if not self.path.startswith(prefix) or "/" in self.path[len(prefix):]:
            return self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
        r = self._with_service(lambda s: s.reservation(self.path[len(prefix):]))
        if r is None:
            return self._send(HTTPStatus.NOT_FOUND, {"error": "reservation does not exist"})
        self._send(HTTPStatus.OK, reservation_json(r))

    # --- helpers -------------------------------------------------------
    def _with_service(self, call):
        service = open_service(self.db_path)
        try:
            return call(service)
        finally:
            service.close()

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"null")
        except (ValueError, UnicodeDecodeError):
            raise BadRequest("body is not valid JSON") from None
        if not isinstance(body, dict):
            raise BadRequest("body must be a JSON object")
        return body

    def _send(self, status: HTTPStatus, payload: dict, headers: dict | None = None) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)


def make_server(db_path: str, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    handler = type("BoundReservationHandler", (ReservationHandler,), {"db_path": db_path})
    return ThreadingHTTPServer((host, port), handler)


def main(db_path: str = "parking.db", host: str = "127.0.0.1", port: int = 8000) -> None:
    server = make_server(db_path, host, port)
    log.info("HTTP API on http://%s:%d, database %s", host, server.server_address[1], db_path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

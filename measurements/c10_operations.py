#!/usr/bin/env python3
"""C10: operational trials against the real HTTP API process (docs/evidence-and-evolution.md, C10).

Usage: python3 measurements/c10_operations.py [--port 8766] [--workdir DIR]

Runs on a disposable database in a new temporary directory (or --workdir),
never on the project's parking.db. The API runs as its own process
(`run_api.py`) with its log (stderr) appended to api.log in the work
directory. Confirm is not offered over HTTP, so it runs as a separate
process calling ReservationService.confirm, like the GUI does. Every state
check reads the SQLite file over a fresh sqlite3 connection.

1   successful confirmation
2   conflicting confirmation rejected
3a  storage unavailable (the database directory is renamed while the API runs), then restored
3b  database locked by another process longer than LOCK_TIMEOUT
4   hard stop and restart of the API on the same database
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
CONFIRM = """
import logging, sys
sys.path.insert(0, {src!r})
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
from parking.service import open_service
from parking.domain import RuleViolation
s = open_service({db!r})
try:
    print("confirm ->", s.confirm({rid!r}).state.value)
except RuleViolation as e:
    print("confirm -> RuleViolation:", e)
finally:
    s.close()
"""
LOCK = """
import sqlite3, sys, time
c = sqlite3.connect({db!r}, isolation_level=None)
c.execute("BEGIN EXCLUSIVE")
print("locked", flush=True)
time.sleep({seconds})
c.execute("ROLLBACK")
"""


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def say(*args) -> None:
    print(f"[{utc()} UTC]", *args, flush=True)


class Trials:
    def __init__(self, workdir: str, port: int):
        self.data = os.path.join(workdir, "data")
        os.makedirs(self.data, exist_ok=True)
        self.db = os.path.join(self.data, "c10.db")
        self.log = os.path.join(workdir, "api.log")
        self.port = port
        self.server = None
        start = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
        self.t0 = start

    # --- the application and its environment ---------------------------
    def start_api(self) -> None:
        log = open(self.log, "a", encoding="utf-8")
        self.server = subprocess.Popen([sys.executable, os.path.join(ROOT, "run_api.py"), self.db,
                                        "--port", str(self.port)], cwd=ROOT, stdout=log, stderr=log)
        log.close()
        for _ in range(100):
            try:
                self.http("GET", "/reservations/ping")
                return say(f"API started, pid {self.server.pid}")
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("API did not start")

    def kill_api(self) -> None:
        self.server.terminate()  # Windows: TerminateProcess, i.e. a hard stop
        self.server.wait()
        say(f"API process {self.server.pid} terminated, exit code {self.server.returncode}")

    def http(self, method: str, path: str, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with OPENER.open(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read()), dict(resp.headers)
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"null"), dict(e.headers)

    def post(self, user: str, start_h: int, end_h: int):
        body = {"place_id": "p1", "user_id": user, "start": (self.t0 + timedelta(hours=start_h)).isoformat(),
                "end": (self.t0 + timedelta(hours=end_h)).isoformat()}
        t = time.perf_counter()
        try:
            status, answer, headers = self.http("POST", "/reservations", body)
        except Exception as e:  # noqa: BLE001 - a dropped connection is an observation here
            say(f"POST {user} -> no HTTP answer: {type(e).__name__}: {e} ({time.perf_counter() - t:.2f} s)")
            return None
        retry = f", Retry-After: {headers['Retry-After']}" if "Retry-After" in headers else ""
        say(f"POST {user} -> {status} {answer}{retry} ({time.perf_counter() - t:.2f} s)")
        return answer.get("id") if status == 201 else None

    def confirm(self, rid: str) -> None:
        out = subprocess.run([sys.executable, "-c", CONFIRM.format(src=os.path.join(ROOT, "src"), db=self.db, rid=rid)],
                             capture_output=True, text=True)
        for line in (out.stdout + out.stderr).splitlines():
            say("  confirm process:", line)

    def stored(self) -> None:
        db = sqlite3.connect(self.db)
        try:
            rows = db.execute('SELECT id, user_id, start, "end", state FROM reservation ORDER BY start').fetchall()
        finally:
            db.close()
        say("independent DB read:", [(r[0][:8], r[1], r[2][11:16], r[3][11:16], r[4]) for r in rows] or "no rows")

    # --- the trials ------------------------------------------------------
    def run(self) -> None:
        self.start_api()

        print("\n== 1 successful confirmation ==")
        b1 = self.post("c10-driver-1", 0, 2)
        self.confirm(b1)
        status, body, _ = self.http("GET", f"/reservations/{b1}")
        say(f"GET b1 -> {status} state={body['state']}")
        self.stored()

        print("\n== 2 conflicting confirmation ==")
        b2 = self.post("c10-driver-2", 1, 3)
        self.confirm(b2)
        say(f"(b1 id starts with {b1[:8]})")
        self.stored()

        print("\n== 3a storage unavailable ==")
        offline = self.data + "-offline"
        os.rename(self.data, offline)
        say("database directory renamed away")
        self.post("c10-driver-2", 4, 5)
        say(f"API process still running: {self.server.poll() is None}")
        os.rename(offline, self.data)
        say("database directory restored")
        self.post("c10-driver-2", 4, 5)
        self.stored()

        print("\n== 3b database locked by another process ==")
        holder = subprocess.Popen([sys.executable, "-c", LOCK.format(db=self.db, seconds=8)],
                                  stdout=subprocess.PIPE, text=True)
        say("lock holder:", holder.stdout.readline().strip())
        self.post("c10-driver-2", 6, 7)
        holder.wait()
        say("lock released")
        self.post("c10-driver-2", 6, 7)
        self.stored()

        print("\n== 4 restart ==")
        self.kill_api()
        self.start_api()
        status, body, _ = self.http("GET", f"/reservations/{b1}")
        say(f"GET b1 after restart -> {status} state={body['state']}")
        self.post("c10-driver-1", 8, 9)
        self.stored()
        self.kill_api()

        print(f"\n== API log ({self.log}) ==")
        with open(self.log, encoding="utf-8") as f:
            print(f.read())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--workdir", help="new empty directory for the disposable DB and the log")
    args = parser.parse_args()
    workdir = args.workdir or tempfile.mkdtemp(prefix="c10-")
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
    print(f"commit {commit or 'unknown'}{' + local changes' if dirty else ''}, "
          f"Python {sys.version.split()[0]}, workdir {workdir}, port {args.port}")
    trials = Trials(workdir, args.port)
    try:
        trials.run()
    finally:
        if trials.server and trials.server.poll() is None:
            trials.server.terminate()


if __name__ == "__main__":
    main()

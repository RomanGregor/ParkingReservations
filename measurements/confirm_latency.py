#!/usr/bin/env python3
"""C04 measurement: how long one Confirm holds the SQLite write lock (ADR-04)
as the reservation history of a place grows.

Usage: PYTHONPATH=src python3 measurements/confirm_latency.py [--history 100 1000 10000] [--runs 200]

For every history size H the script creates a fresh SQLite file with H past
reservations on each of the 5 catalog places (states mixed like a real
history: CONFIRMED, CANCELLED, EXPIRED, never-confirmed DRAFT), then times
`ReservationService.confirm` on new DRAFTs of one place:

- solo (QC-1): one instance, `runs` confirms one after another;
- contended (QC-2): two instances, i.e. two separate processes with their own
  connection as in G4, confirm `runs` DRAFTs each at the same time. Lock
  errors are counted out of these 2 x `runs` concurrent confirms only.

Confirm runs entirely inside `transaction()` (BEGIN IMMEDIATE .. COMMIT) and
the notifier is a no-op, so the time of `confirm` is the time the write lock
is held plus, when contended, the time spent waiting for it.
"""
import argparse
import multiprocessing
import os
import platform
import queue
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

from parking.domain import Reservation, State, User
from parking.places import PLACES, PlaceCatalog
from parking.repository import ReservationRepository, StoreBusy
from parking.service import ReservationService

NOW = datetime(2026, 10, 1, 6, tzinfo=timezone.utc)
H = timedelta(hours=1)
PLACE = PLACES[0]
HISTORY_STATES = [State.CONFIRMED, State.CANCELLED, State.CONFIRMED, State.DRAFT, State.EXPIRED]


class NoNotifier:
    def reservation_changed(self, reservation):
        pass


def seed(path: str, per_place: int, index: bool) -> None:
    """Past reservations, one per hour slot, so none of them conflicts with
    the measured future DRAFTs but all of them are scanned by for_place."""
    repo = ReservationRepository(path)
    with repo.transaction():
        for place in PLACES:
            for i in range(per_place):
                start = NOW - (i + 1) * H
                repo.save(Reservation(place.id, "history", start, start + H,
                                      HISTORY_STATES[i % len(HISTORY_STATES)]))
    repo.close()
    if index:  # alternative under evaluation, not part of the application schema
        db = sqlite3.connect(path)
        db.execute("CREATE INDEX reservation_place ON reservation (place_id)")
        db.close()


def open_service(path: str) -> ReservationService:
    return ReservationService(ReservationRepository(path), PlaceCatalog(), NoNotifier(), clock=lambda: NOW)


def drafts(service: ReservationService, count: int, first_slot: int) -> list[str]:
    user = User("bench", "bench")
    return [service.create(PLACE.id, user, NOW + (2 + first_slot + i) * H, NOW + (3 + first_slot + i) * H).id
            for i in range(count)]


def timed_confirms(service: ReservationService, ids: list[str], out: list[float], errors: list[str]) -> None:
    for rid in ids:
        t0 = time.perf_counter()
        try:
            service.confirm(rid)
        except StoreBusy as e:  # write lock not obtained within LOCK_TIMEOUT (5 s)
            errors.append(f"{e} after {(time.perf_counter() - t0):.1f} s")
            continue
        out.append((time.perf_counter() - t0) * 1000)


def p95(values: list[float]) -> float:
    return statistics.quantiles(values, n=20, method="inclusive")[18]


BARRIER_TIMEOUT = 120.0  # seconds; creating `runs` DRAFTs takes far less


def instance(path: str, first_slot: int, runs: int, ready, results) -> None:
    """One application instance (G4): its own process and connection. Creates
    its DRAFTs, waits for the other instance, then confirms them all.

    Any failure (e.g. StoreBusy while creating DRAFTs) is reported to the
    parent and breaks the barrier, so the other instance stops waiting too
    instead of blocking the measurement forever."""
    try:
        service = open_service(path)
        try:
            ids = drafts(service, runs, first_slot)
            ready.wait(BARRIER_TIMEOUT)
            times, errors = [], []
            timed_confirms(service, ids, times, errors)
        finally:
            service.close()
    except BaseException as e:
        ready.abort()
        results.put(("failed", f"{type(e).__name__}: {e}"))
        return
    results.put(("ok", (times, errors)))


def contended(paths: list[str], runs: int) -> tuple[list[float], list[str]]:
    """QC-2: one instance (process) per database path confirms `runs` DRAFTs,
    all at the same time; the measurement passes the same file twice. Returns
    the confirm times and lock errors of all instances; raises RuntimeError if
    an instance fails or dies instead of reporting them."""
    # spawn, not fork: the same start method on Windows and Linux.
    ctx = multiprocessing.get_context("spawn")
    ready, results = ctx.Barrier(len(paths)), ctx.Queue()
    procs = [ctx.Process(target=instance, args=(path, (i + 1) * runs, runs, ready, results))
             for i, path in enumerate(paths)]
    for p in procs:
        p.start()
    times, errors, failures = [], [], []
    pending = len(procs)
    while pending:  # read before join, so a full queue cannot block a child
        try:
            status, payload = results.get(timeout=1)
        except queue.Empty:
            if any(p.is_alive() for p in procs):
                continue
            try:  # every instance has exited: take what they managed to send
                status, payload = results.get(timeout=1)
            except queue.Empty:
                failures.append(f"{pending} instance(s) exited without a result "
                                f"(exit codes {[p.exitcode for p in procs]})")
                break
        pending -= 1
        if status == "ok":
            times += payload[0]
            errors += payload[1]
        else:
            failures.append(payload)
    for p in procs:
        p.join()
    if failures:
        raise RuntimeError("contended measurement failed: " + "; ".join(failures))
    return times, errors


def measure(per_place: int, runs: int, index: bool = False) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "bench.db")
        seed(path, per_place, index)

        solo, solo_errors = [], []
        service = open_service(path)
        timed_confirms(service, drafts(service, runs, 0), solo, solo_errors)
        service.close()

        contended_times, contended_errors = contended([path, path], runs)

        check = ReservationRepository(path)
        confirmed = sum(r.state == State.CONFIRMED for r in check.for_place(PLACE.id) if r.user_id == "bench")
        check.close()

    return {
        "rows": per_place * len(PLACES), "per_place": per_place,
        "solo_p50": statistics.median(solo), "solo_p95": p95(solo), "solo_max": max(solo),
        "solo_errors": len(solo_errors),
        "cont_p95": p95(contended_times), "cont_max": max(contended_times),
        "errors": len(contended_errors), "concurrent": 2 * runs,
        "confirmed": confirmed, "expected": 3 * runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--history", type=int, nargs="+", default=[100, 1000, 10000],
                        help="past reservations per place")
    parser.add_argument("--runs", type=int, default=200, help="confirms solo and per instance when contended")
    parser.add_argument("--index", action="store_true", help="measure with an index on place_id (alternative)")
    args = parser.parse_args()

    print(f"Python {platform.python_version()}, SQLite {sqlite3.sqlite_version}, "
          f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPUs; runs={args.runs}, "
          f"index on place_id: {'yes' if args.index else 'no'}")
    print(f"solo = 1 instance x {args.runs} (QC-1); 2-proc = 2 processes x {args.runs} at the same time (QC-2); "
          "2-proc max = longest successful confirm incl. waiting for the lock")
    print("history/place  rows    solo p50   solo p95   solo max  2-proc p95  2-proc max  lock errors  confirmed")
    for per_place in args.history:
        m = measure(per_place, args.runs, args.index)
        errors = f"{m['errors']}/{m['concurrent']}"
        print(f"{m['per_place']:>13}  {m['rows']:>6}  {m['solo_p50']:>6.2f} ms {m['solo_p95']:>7.2f} ms "
              f"{m['solo_max']:>7.2f} ms  {m['cont_p95']:>7.2f} ms  {m['cont_max']:>7.2f} ms  "
              f"{errors:>11}  {m['confirmed']}/{m['expected']}")
        if m["solo_errors"]:
            print(f"{'':>13}  solo lock errors: {m['solo_errors']}")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

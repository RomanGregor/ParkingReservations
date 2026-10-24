#!/usr/bin/env python3
"""C04 measurement: how long one Confirm holds the SQLite write lock (ADR-04)
as the reservation history of a place grows.

Usage: PYTHONPATH=src python3 measurements/confirm_latency.py [--history 100 1000 10000] [--runs 200]

For every history size H the script creates a fresh SQLite file with H past
reservations on each of the 5 catalog places (states mixed like a real
history: CONFIRMED, CANCELLED, EXPIRED, never-confirmed DRAFT), then times
`ReservationService.confirm` on new DRAFTs of one place:

- solo: one instance, `runs` confirms one after another;
- contended: two instances (two connections, two threads) confirm `runs / 2`
  DRAFTs each at the same time, as in G4. Lock errors are counted.

Confirm runs entirely inside `transaction()` (BEGIN IMMEDIATE .. COMMIT) and
the notifier is a no-op, so the time of `confirm` is the time the write lock
is held plus, when contended, the time spent waiting for it.
"""
import argparse
import os
import platform
import sqlite3
import statistics
import sys
import tempfile
import threading
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


def measure(per_place: int, runs: int, index: bool = False) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "bench.db")
        seed(path, per_place, index)

        solo, solo_errors = [], []
        service = open_service(path)
        timed_confirms(service, drafts(service, runs, 0), solo, solo_errors)
        service.close()

        contended, contended_errors = [], []
        ready = threading.Barrier(2)

        def instance(first_slot: int) -> None:
            # A connection may only be used by the thread that opened it.
            s = open_service(path)
            ids = drafts(s, runs // 2, first_slot)
            ready.wait()
            timed_confirms(s, ids, contended, contended_errors)
            s.close()

        threads = [threading.Thread(target=instance, args=(slot,)) for slot in (runs, runs + runs // 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        check = ReservationRepository(path)
        confirmed = sum(r.state == State.CONFIRMED for r in check.for_place(PLACE.id) if r.user_id == "bench")
        check.close()

    return {
        "rows": per_place * len(PLACES), "per_place": per_place,
        "solo_p50": statistics.median(solo), "solo_p95": p95(solo), "solo_max": max(solo),
        "cont_p95": p95(contended), "cont_max": max(contended),
        "errors": len(solo_errors) + len(contended_errors),
        "confirmed": confirmed, "expected": runs + 2 * (runs // 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--history", type=int, nargs="+", default=[100, 1000, 10000],
                        help="past reservations per place")
    parser.add_argument("--runs", type=int, default=200, help="confirms per scenario")
    parser.add_argument("--index", action="store_true", help="measure with an index on place_id (alternative)")
    args = parser.parse_args()

    print(f"Python {platform.python_version()}, SQLite {sqlite3.sqlite_version}, "
          f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPUs; runs={args.runs}, "
          f"index on place_id: {'yes' if args.index else 'no'}")
    print("history/place  rows   solo p50  solo p95  solo max  2-inst p95  2-inst max  lock errors  confirmed")
    for per_place in args.history:
        m = measure(per_place, args.runs, args.index)
        print(f"{m['per_place']:>13}  {m['rows']:>5}  {m['solo_p50']:>6.2f} ms {m['solo_p95']:>6.2f} ms "
              f"{m['solo_max']:>6.2f} ms  {m['cont_p95']:>7.2f} ms  {m['cont_max']:>7.2f} ms  "
              f"{m['errors']:>11}  {m['confirmed']}/{m['expected']}")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

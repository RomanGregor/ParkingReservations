"""Reservation Management: the only entry point for Reservation state changes (ADR-04).

Every state change is one transaction: load the reservation and the other
reservations of its place, apply the domain rule, save. The SQLite write lock
taken by `transaction()` keeps another instance from slipping its own write
between our check and our write (REQ-04, REQ-06). `now` comes from the
service's clock, read inside the transaction. The Driver is notified only
after the commit; a failing notification does not undo the change.
"""
import logging
from datetime import datetime, timezone
from typing import Callable

from parking import domain
from parking.domain import ParkingPlace, Reservation, RuleViolation, State, User
from parking.notification import LogNotifier, Notifier
from parking.places import PlaceCatalog
from parking.repository import ReservationRepository

log = logging.getLogger("parking.service")

Rule = Callable[[Reservation, list[Reservation], datetime], object]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ReservationService:
    def __init__(self, repo: ReservationRepository, catalog: PlaceCatalog | None = None,
                 notifier: Notifier | None = None, clock: Callable[[], datetime] = utc_now):
        self._repo = repo
        self._catalog = catalog or PlaceCatalog()
        self._notifier = notifier or LogNotifier()
        self._clock = clock

    def close(self) -> None:
        self._repo.close()

    # --- queries -------------------------------------------------------
    def places(self) -> list[ParkingPlace]:
        return self._catalog.all()

    def reservations(self) -> list[Reservation]:
        return self._repo.all()

    def check(self, place_id: str, start: datetime, end: datetime) -> bool:
        """OP-02 Check Availability."""
        place = self._catalog.find(place_id)
        return domain.is_available(place.id, start, end, self._repo.for_place(place.id))

    # --- state changes -------------------------------------------------
    def create(self, place_id: str, user: User, start: datetime, end: datetime) -> Reservation:
        """OP-01 Create Reservation. A DRAFT blocks nothing, so no lock is needed."""
        r = domain.create_reservation(self._catalog.find(place_id), user, start, end, self._clock())
        self._repo.save(r)
        return r

    def confirm(self, reservation_id: str) -> Reservation:
        """OP-03: DRAFT -> CONFIRMED, or PENDING_APPROVAL on a place that requires approval."""
        return self._transition(reservation_id, lambda r, others, now: domain.confirm(
            r, others, self._catalog.find(r.place_id), now))

    def approve(self, reservation_id: str) -> Reservation:
        """OP-05 / REQ-06: PENDING_APPROVAL -> CONFIRMED."""
        return self._transition(reservation_id, domain.approve)

    def reject(self, reservation_id: str) -> Reservation:
        """OP-05 / REQ-07: PENDING_APPROVAL -> REJECTED."""
        return self._transition(reservation_id, lambda r, _, now: domain.reject(r, now))

    def cancel(self, reservation_id: str) -> Reservation:
        """OP-04 Cancel Reservation."""
        return self._transition(reservation_id, lambda r, _, now: domain.cancel(r, now))

    def expire_due(self) -> list[Reservation]:
        """REQ-08: expire every PENDING_APPROVAL whose start has passed.
        Returns the reservations that actually expired."""
        now = self._clock()
        due = [r for r in self._repo.all() if r.state == State.PENDING_APPROVAL and now >= r.start]
        expired = [self._transition(p.id, lambda r, _, now: domain.expire_if_due(r, now)) for p in due]
        # Another instance may have decided on a request in the meantime.
        return [r for r in expired if r.state == State.EXPIRED]

    def _transition(self, reservation_id: str, rule: Rule) -> Reservation:
        with self._repo.transaction():
            r = self._repo.get(reservation_id)
            if r is None:
                raise RuleViolation("reservation does not exist")
            before = r.state
            rule(r, self._repo.for_place(r.place_id), self._clock())
            self._repo.save(r)
        if r.state != before:
            self._notify(r)
        return r

    def _notify(self, r: Reservation) -> None:
        try:
            self._notifier.reservation_changed(r)
        except Exception:
            log.exception("notification for reservation %s failed; %s stays committed", r.id, r.state.value)


def open_service(db_path: str) -> ReservationService:
    return ReservationService(ReservationRepository(db_path))

"""Reservation domain: parking places, users, reservations and business rules."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from uuid import uuid4

MAX_DURATION = timedelta(hours=24)  # BR-04: domain-specific rule from C01
MIN_LEAD_TIME = timedelta(hours=1)  # BR-05: a reservation must start in the future


class State(str, Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


# BR-03: a Reservation may be withdrawn while it is still a candidate or an
# active allocation, but only before its interval starts.
CANCELLABLE_STATES = {State.DRAFT, State.PENDING_APPROVAL, State.CONFIRMED}


class RuleViolation(Exception):
    """A business rule would be broken by the requested operation."""


@dataclass(frozen=True)
class User:
    id: str
    name: str


@dataclass(frozen=True)
class ParkingPlace:
    id: str
    label: str
    requires_approval: bool = False  # OP-05: place-level approval requirement


@dataclass
class Reservation:
    place_id: str
    user_id: str
    start: datetime
    end: datetime
    state: State = State.DRAFT
    id: str = field(default_factory=lambda: uuid4().hex)

    def overlaps(self, other: "Reservation") -> bool:
        # BR-01: intervals use [start, end) semantics.
        return (
            self.place_id == other.place_id
            and self.start < other.end
            and other.start < self.end
        )


def create_reservation(place: ParkingPlace, user: User, start: datetime, end: datetime, now: datetime) -> Reservation:
    """OP-01 Create Reservation: records intent, does not allocate the place.

    BR-05: start must be at least MIN_LEAD_TIME in the future relative to
    `now` — a Driver cannot request a reservation that starts in the past
    or immediately, since nobody could act on it (confirm/approve) in time.
    """
    if end <= start:
        raise RuleViolation("end must be after start")
    if end - start > MAX_DURATION:
        raise RuleViolation("a parking place can be reserved for at most 24 hours")
    if start < now + MIN_LEAD_TIME:
        raise RuleViolation("reservation must start at least 1 hour from now")
    return Reservation(place.id, user.id, start, end)


def is_available(place_id: str, start: datetime, end: datetime, existing: list[Reservation]) -> bool:
    """OP-02 Check Availability. BR-02: only CONFIRMED reservations block a
    place; a PENDING_APPROVAL request is not yet a committed allocation and
    must not make the place look unavailable to other users."""
    if end <= start:
        raise RuleViolation("end must be after start")
    probe = Reservation(place_id, "", start, end)
    return not any(r.state == State.CONFIRMED and r.overlaps(probe) for r in existing)


def _require_not_started(reservation: Reservation, now: datetime, action: str) -> None:
    if now >= reservation.start:
        raise RuleViolation(f"cannot {action} a reservation that has already started")


def confirm(reservation: Reservation, existing: list[Reservation], place: ParkingPlace, now: datetime) -> None:
    """OP-03 Confirm Reservation, only before the reservation starts.

    v0.2: if the place requires approval, confirming a DRAFT only moves it to
    PENDING_APPROVAL (see OP-05 Approve); the overlap check is deferred to
    approval time, when the outcome is actually committed. Places that do not
    require approval keep the v0.1 behavior: DRAFT -> CONFIRMED immediately.
    """
    if reservation.state != State.DRAFT:
        raise RuleViolation(f"cannot confirm a {reservation.state.value} reservation")
    _require_not_started(reservation, now, "confirm")
    if place.requires_approval:
        reservation.state = State.PENDING_APPROVAL
        return
    others = [r for r in existing if r.id != reservation.id]
    if not is_available(reservation.place_id, reservation.start, reservation.end, others):
        raise RuleViolation("confirmed reservations for the same place must not overlap")
    reservation.state = State.CONFIRMED


def approve(reservation: Reservation, existing: list[Reservation], now: datetime) -> None:
    """OP-05 Approve Reservation: an authorized approver accepts a
    PENDING_APPROVAL request as the committed allocation of its place.
    After the reservation's start the request can only expire."""
    if reservation.state != State.PENDING_APPROVAL:
        raise RuleViolation(f"cannot approve a {reservation.state.value} reservation")
    _require_not_started(reservation, now, "approve")
    others = [r for r in existing if r.id != reservation.id]
    if not is_available(reservation.place_id, reservation.start, reservation.end, others):
        raise RuleViolation("confirmed reservations for the same place must not overlap")
    reservation.state = State.CONFIRMED


def reject(reservation: Reservation, now: datetime) -> None:
    """OP-05 Approve Reservation, negative outcome: the approver declines the request.

    Reject only applies to a PENDING_APPROVAL reservation (an approval
    request awaiting a Facility manager decision). A DRAFT reservation was
    never submitted for approval, so it cannot be rejected — the Driver
    withdraws it with cancel() instead (OP-04).
    """
    if reservation.state == State.DRAFT:
        raise RuleViolation("a DRAFT reservation cannot be rejected; use cancel instead")
    if reservation.state != State.PENDING_APPROVAL:
        raise RuleViolation(f"cannot reject a {reservation.state.value} reservation")
    _require_not_started(reservation, now, "reject")
    reservation.state = State.REJECTED


def expire_if_due(reservation: Reservation, now: datetime) -> bool:
    """A PENDING_APPROVAL request that nobody decided on before the
    reservation's own start time is no longer actionable and expires.
    Returns True if the reservation was changed."""
    if reservation.state == State.PENDING_APPROVAL and now >= reservation.start:
        reservation.state = State.EXPIRED
        return True
    return False


def cancel(reservation: Reservation, now: datetime) -> None:
    """OP-04 Cancel Reservation. BR-03: a DRAFT, PENDING_APPROVAL or CONFIRMED
    reservation can be withdrawn, but only strictly before its start time."""
    if reservation.state not in CANCELLABLE_STATES:
        raise RuleViolation(f"cannot cancel a {reservation.state.value} reservation")
    _require_not_started(reservation, now, "cancel")
    reservation.state = State.CANCELLED

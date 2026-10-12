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
        return self.place_id == other.place_id and self.overlaps_interval(other.start, other.end)

    def overlaps_interval(self, start: datetime, end: datetime) -> bool:
        # BR-01: intervals use [start, end) semantics; touching intervals do not overlap.
        return self.start < end and start < self.end


class Availability(str, Enum):
    """OP-02 outcome. AVAILABLE / UNAVAILABLE follow REQ-02; INVALID_INTERVAL
    is the "rejected, no answer" case (end <= start)."""
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_INTERVAL = "INVALID_INTERVAL"


@dataclass(frozen=True)
class AvailabilityResult:
    outcome: Availability
    conflicts: tuple[str, ...] = ()  # ids of the overlapping CONFIRMED reservations, in `existing` order


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


def evaluate_availability(place_id: str, start: datetime, end: datetime, existing: list[Reservation],
                          exclude_id: str | None = None) -> AvailabilityResult:
    """OP-02 decision shared by Check, Confirm and Approve (C05 DD-4).

    BR-02: only CONFIRMED reservations of the same place block it; a
    PENDING_APPROVAL request is not yet a committed allocation and must not
    make the place look unavailable to other users. `exclude_id` leaves the
    reservation being decided out, so `existing` may be all reservations of
    the place including that one. An invalid interval is an outcome, not an
    exception; the caller decides how to report it.
    """
    if end <= start:
        return AvailabilityResult(Availability.INVALID_INTERVAL)
    conflicts = tuple(
        r.id for r in existing
        if r.id != exclude_id and r.place_id == place_id
        and r.state == State.CONFIRMED and r.overlaps_interval(start, end)
    )
    return AvailabilityResult(Availability.UNAVAILABLE if conflicts else Availability.AVAILABLE, conflicts)


def is_available(place_id: str, start: datetime, end: datetime, existing: list[Reservation]) -> bool:
    """OP-02 Check Availability as the yes/no answer of REQ-02. An invalid
    interval gets no answer: it is rejected."""
    result = evaluate_availability(place_id, start, end, existing)
    if result.outcome == Availability.INVALID_INTERVAL:
        raise RuleViolation("end must be after start")
    return result.outcome == Availability.AVAILABLE


def _require_not_started(reservation: Reservation, now: datetime, action: str) -> None:
    if now >= reservation.start:
        raise RuleViolation(f"cannot {action} a reservation that has already started")


def _require_no_conflict(reservation: Reservation, existing: list[Reservation]) -> None:
    """BR-02 at the moment an allocation is committed (confirm, approve)."""
    result = evaluate_availability(reservation.place_id, reservation.start, reservation.end,
                                   existing, exclude_id=reservation.id)
    if result.outcome == Availability.INVALID_INTERVAL:
        raise RuleViolation("end must be after start")
    if result.outcome == Availability.UNAVAILABLE:
        raise RuleViolation("confirmed reservations for the same place must not overlap; "
                            f"conflicts with {', '.join(result.conflicts)}")


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
    _require_no_conflict(reservation, existing)
    reservation.state = State.CONFIRMED


def approve(reservation: Reservation, existing: list[Reservation], now: datetime) -> None:
    """OP-05 Approve Reservation: an authorized approver accepts a
    PENDING_APPROVAL request as the committed allocation of its place.
    After the reservation's start the request can only expire."""
    if reservation.state != State.PENDING_APPROVAL:
        raise RuleViolation(f"cannot approve a {reservation.state.value} reservation")
    _require_not_started(reservation, now, "approve")
    _require_no_conflict(reservation, existing)
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

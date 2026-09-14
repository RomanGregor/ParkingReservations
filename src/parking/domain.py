"""Reservation domain: parking places, users, reservations and business rules."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from uuid import uuid4

MAX_DURATION = timedelta(hours=24)


class State(str, Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


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


@dataclass
class Reservation:
    place_id: str
    user_id: str
    start: datetime
    end: datetime
    state: State = State.DRAFT
    id: str = field(default_factory=lambda: uuid4().hex)

    def overlaps(self, other: "Reservation") -> bool:
        return (
            self.place_id == other.place_id
            and self.start < other.end
            and other.start < self.end
        )


def create_reservation(place: ParkingPlace, user: User, start: datetime, end: datetime) -> Reservation:
    if end <= start:
        raise RuleViolation("end must be after start")
    if end - start > MAX_DURATION:  # domain-specific rule
        raise RuleViolation("a parking place can be reserved for at most 24 hours")
    return Reservation(place.id, user.id, start, end)


def is_available(place_id: str, start: datetime, end: datetime, existing: list[Reservation]) -> bool:
    probe = Reservation(place_id, "", start, end)
    return not any(r.state == State.CONFIRMED and r.overlaps(probe) for r in existing)


def confirm(reservation: Reservation, existing: list[Reservation]) -> None:
    if reservation.state != State.DRAFT:
        raise RuleViolation(f"cannot confirm a {reservation.state.value} reservation")
    others = [r for r in existing if r.id != reservation.id]
    if not is_available(reservation.place_id, reservation.start, reservation.end, others):
        raise RuleViolation("confirmed reservations for the same place must not overlap")
    reservation.state = State.CONFIRMED


def cancel(reservation: Reservation) -> None:
    if reservation.state == State.CANCELLED:
        raise RuleViolation("reservation is already cancelled")
    reservation.state = State.CANCELLED

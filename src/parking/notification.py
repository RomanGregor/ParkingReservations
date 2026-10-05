"""Notification Integration: tells the Driver that a reservation changed state.

The external Notification Service from C01 does not exist yet; `LogNotifier`
stands in for it. ReservationService calls the notifier only after a state
change is committed (ADR-04).
"""
import logging
from typing import Protocol

from parking.domain import Reservation

log = logging.getLogger("parking.notification")


class Notifier(Protocol):
    def reservation_changed(self, reservation: Reservation) -> None: ...


class LogNotifier:
    def reservation_changed(self, reservation: Reservation) -> None:
        log.info("notify %s: reservation %s is now %s",
                 reservation.user_id, reservation.id, reservation.state.value)

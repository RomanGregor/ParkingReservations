# Project Frame

## Reservation domain
Parking places in a company car park. A user reserves one concrete parking
place for a time interval (start–end).

## Purpose
Employees and visitors of a company can reserve a parking place ahead of
time instead of hunting for a free one. The system guarantees that a
confirmed place is not double-booked and keeps places from being blocked
for days.

## Users / Stakeholders
- **Driver** – creates, confirms and cancels their own reservations.
- **Facility manager** – manages parking places, can cancel any reservation.

## Core concepts
Reservation, Resource (= ParkingPlace), User.

## Core operations
- Create reservation
- Confirm / approve reservation
- Cancel reservation
- Check availability

## Persistent state
- **Reservation**: id, place_id, user_id, start, end, state.
- **ParkingPlace**: id, label (e.g. `A-12`).

## State-changing operation
`DRAFT → CONFIRMED` (confirm) and `DRAFT | CONFIRMED → CANCELLED` (cancel).

## Common business rule
Confirmed reservations for the same parking place must not overlap.

## Domain-specific business rule
A parking place reservation may not be longer than 24 hours – places are
for parking, not for long-term storage of a vehicle.

## External / system boundary
Notification Service – the driver is notified (e-mail) when a reservation is
confirmed or cancelled. Modelled as a dependency behind an interface; not
implemented in C01.

## Assumption
All times are handled in UTC; drivers do not need reservations that cross a
daylight-saving change to be displayed in local time.

## Unknown
How many places / reservations per day the car park really has, i.e.
whether SQLite is enough beyond CP1 or a client–server database is needed.

## Selected future pressure
Category: **C** (Changeability)

Concrete pressure: a new resource type – *EV charging place* – with its own
rule (max 4 hours, only for users with an electric vehicle).

Why it is relevant to our reservation system: the company is installing
chargers next year; the overlap rule and the persistence must work for both
place types without duplicating the reservation logic.

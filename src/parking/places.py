"""Place Catalog: the parking places and their properties (C03, ADR-04)."""
from parking.domain import ParkingPlace, RuleViolation

PLACES = [ParkingPlace(f"p{i}", f"A-{i:02d}") for i in range(1, 5)] + [
    ParkingPlace("p5", "VIP-01", requires_approval=True),
]


class PlaceCatalog:
    def __init__(self, places: list[ParkingPlace] = PLACES):
        self._places = {p.id: p for p in places}

    def all(self) -> list[ParkingPlace]:
        return list(self._places.values())

    def find(self, place_id: str) -> ParkingPlace:
        try:
            return self._places[place_id]
        except KeyError:
            raise RuleViolation("parking place does not exist") from None

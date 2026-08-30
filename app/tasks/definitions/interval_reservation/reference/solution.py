"""Trusted half-open reservation oracle."""


class ReservationBook:
    def __init__(self) -> None:
        self._by_id: dict[str, tuple[str, int, int]] = {}

    def reserve(self, reservation_id: str, resource: str, start: int, end: int) -> bool:
        if not isinstance(reservation_id, str) or not reservation_id:
            raise ValueError("reservation_id must be a nonempty string")
        if not isinstance(resource, str) or not resource:
            raise ValueError("resource must be a nonempty string")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start < 0
            or start >= end
        ):
            raise ValueError("interval must use nonnegative integers with start < end")
        if reservation_id in self._by_id:
            raise ValueError("reservation id already exists")
        for existing_resource, existing_start, existing_end in self._by_id.values():
            if existing_resource == resource and start < existing_end and existing_start < end:
                return False
        self._by_id[reservation_id] = (resource, start, end)
        return True

    def cancel(self, reservation_id: str) -> bool:
        return self._by_id.pop(reservation_id, None) is not None

    def reservations(self, resource: str) -> list[dict[str, object]]:
        result = [
            {"id": reservation_id, "start": start, "end": end}
            for reservation_id, (candidate_resource, start, end) in self._by_id.items()
            if candidate_resource == resource
        ]
        return sorted(result, key=lambda item: (item["start"], item["end"], item["id"]))

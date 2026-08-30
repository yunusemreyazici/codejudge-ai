import pytest
from solution import ReservationBook


@pytest.mark.parametrize(
    "arguments",
    [
        ("", "room", 0, 1),
        ("id", "", 0, 1),
        ("id", "room", -1, 1),
        ("id", "room", 1, 1),
        ("id", "room", 2, 1),
        ("id", "room", True, 2),
        ("id", "room", 0, False),
    ],
)
def test_invalid_reservations_are_rejected(arguments: tuple[object, ...]) -> None:
    with pytest.raises(ValueError):
        ReservationBook().reserve(*arguments)


def test_containment_and_exact_duplicates_overlap() -> None:
    book = ReservationBook()
    assert book.reserve("outer", "room", 5, 20) is True
    assert book.reserve("inner", "room", 10, 12) is False
    assert book.reserve("same-range", "room", 5, 20) is False


def test_returned_records_cannot_mutate_book() -> None:
    book = ReservationBook()
    book.reserve("a", "room", 0, 1)
    records = book.reservations("room")
    records[0]["start"] = 99
    assert book.reservations("room") == [{"id": "a", "start": 0, "end": 1}]

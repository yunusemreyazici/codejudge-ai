import pytest
from solution import ReservationBook


def test_reserve_overlap_adjacency_and_ordering() -> None:
    book = ReservationBook()
    assert book.reserve("late", "room", 10, 20) is True
    assert book.reserve("early", "room", 0, 10) is True
    assert book.reserve("overlap", "room", 9, 11) is False
    assert book.reservations("room") == [
        {"id": "early", "start": 0, "end": 10},
        {"id": "late", "start": 10, "end": 20},
    ]


def test_resources_are_independent_and_failed_id_is_not_consumed() -> None:
    book = ReservationBook()
    assert book.reserve("a", "one", 0, 5) is True
    assert book.reserve("retry", "one", 1, 2) is False
    assert book.reserve("retry", "two", 1, 2) is True


def test_cancel_is_idempotent_and_releases_interval() -> None:
    book = ReservationBook()
    assert book.reserve("a", "room", 1, 4) is True
    assert book.cancel("a") is True
    assert book.cancel("a") is False
    assert book.reserve("b", "room", 1, 4) is True


def test_accepted_ids_are_globally_unique() -> None:
    book = ReservationBook()
    assert book.reserve("same", "one", 0, 1) is True
    with pytest.raises(ValueError, match="already exists"):
        book.reserve("same", "two", 10, 11)

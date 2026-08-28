import pytest
from solution import retry_delay


def test_one_based_exponential_growth_and_default_multiplier() -> None:
    assert retry_delay(1, 0.5, 100) == 0.5
    assert retry_delay(2, 0.5, 100) == 1
    assert retry_delay(3, 0.5, 100) == 2
    assert retry_delay(6, 0.5, 100) == 16


def test_cap_is_inclusive_and_remains_stable() -> None:
    assert retry_delay(3, 2, 8) == 8
    assert retry_delay(4, 2, 8) == 8
    assert retry_delay(100, 2, 8) == 8
    assert retry_delay(10_000, 0.1, 5) == 5


def test_custom_multiplier_and_multiplier_one() -> None:
    assert retry_delay(1, 3, 100, 3) == 3
    assert retry_delay(2, 3, 100, 3) == 9
    assert retry_delay(4, 3, 100, 3) == 81
    assert retry_delay(50, 3, 100, 1) == 3


@pytest.mark.parametrize("attempt", [0, -1, True, 1.5, "2"])
def test_invalid_attempt_is_rejected(attempt: object) -> None:
    with pytest.raises(ValueError):
        retry_delay(attempt, 1, 10)


@pytest.mark.parametrize(
    ("base", "cap", "multiplier"),
    [(0, 10, 2), (-1, 10, 2), (2, 1, 2), (1, 10, 0.5), (True, 10, 2)],
)
def test_invalid_delay_configuration_is_rejected(
    base: object, cap: object, multiplier: object
) -> None:
    with pytest.raises(ValueError):
        retry_delay(1, base, cap, multiplier)


def test_calls_are_pure_and_repeatable() -> None:
    first = retry_delay(7, 0.25, 20, 2)
    assert first == 16
    assert retry_delay(7, 0.25, 20, 2) == first
    assert retry_delay(8, 0.25, 20, 2) == 20

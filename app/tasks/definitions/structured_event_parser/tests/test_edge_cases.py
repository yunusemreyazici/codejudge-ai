import pytest
from solution import parse_events


@pytest.mark.parametrize(
    "line",
    [
        "{",
        '{"timestamp":1,"kind":"created"}',
        '{"id":"","timestamp":1,"kind":"created"}',
        '{"id":"a","timestamp":true,"kind":"created"}',
        '{"id":"a","timestamp":-1,"kind":"created"}',
        '{"id":"a","timestamp":1,"kind":"other"}',
        '{"id":"a","timestamp":1,"kind":[]}',
        '{"id":"a","timestamp":1,"kind":"created","payload":[]}',
        '{"id":"a","timestamp":1,"kind":"created","extra":1}',
    ],
)
def test_malformed_or_invalid_events_are_rejected(line: str) -> None:
    with pytest.raises(ValueError):
        parse_events([line])


def test_duplicate_ids_and_decreasing_timestamps_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        parse_events(
            [
                '{"id":"a","timestamp":1,"kind":"created"}',
                '{"id":"a","timestamp":2,"kind":"updated"}',
            ]
        )
    with pytest.raises(ValueError, match="nondecreasing"):
        parse_events(
            [
                '{"id":"a","timestamp":2,"kind":"created"}',
                '{"id":"b","timestamp":1,"kind":"updated"}',
            ]
        )


def test_payload_result_is_new_mapping() -> None:
    first = parse_events(['{"id":"a","timestamp":0,"kind":"created","payload":{"x":1}}'])
    second = parse_events(['{"id":"a","timestamp":0,"kind":"created","payload":{"x":1}}'])
    first[0]["payload"]["x"] = 9
    assert second[0]["payload"] == {"x": 1}

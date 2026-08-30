import copy

import pytest
from solution import parse_events


def test_parses_normalizes_and_preserves_order() -> None:
    lines = [
        '{"id":"a","timestamp":1,"kind":"created","payload":{"x":2}}',
        "   ",
        '{"id":"b","timestamp":1,"kind":"updated"}',
        '{"id":"c","timestamp":3,"kind":"deleted","payload":{}}',
    ]
    original = copy.deepcopy(lines)
    assert parse_events(lines) == [
        {"id": "a", "timestamp": 1, "kind": "created", "payload": {"x": 2}},
        {"id": "b", "timestamp": 1, "kind": "updated", "payload": {}},
        {"id": "c", "timestamp": 3, "kind": "deleted", "payload": {}},
    ]
    assert lines == original


def test_empty_and_blank_input() -> None:
    assert parse_events([]) == []
    assert parse_events(["", "\t", "  "]) == []


@pytest.mark.parametrize("value", [None, (), "one line"])
def test_lines_must_be_a_list(value: object) -> None:
    with pytest.raises(TypeError):
        parse_events(value)


def test_items_and_decoded_values_have_documented_types() -> None:
    with pytest.raises(TypeError):
        parse_events([1])
    with pytest.raises(TypeError):
        parse_events(["[]"])

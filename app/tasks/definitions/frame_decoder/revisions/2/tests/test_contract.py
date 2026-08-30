import pytest
from solution import LengthPrefixedDecoder


def test_decodes_multiple_and_empty_frames() -> None:
    decoder = LengthPrefixedDecoder(20)
    assert decoder.feed("3:abc0:5:hello") == ["abc", "", "hello"]
    assert decoder.finish() is None


def test_prefix_and_payload_can_cross_arbitrary_chunks() -> None:
    decoder = LengthPrefixedDecoder(20)
    assert decoder.feed("1") == []
    assert decoder.feed("1:hello") == []
    assert decoder.feed(" world") == ["hello world"]


def test_incomplete_tail_is_retained_after_complete_frames() -> None:
    decoder = LengthPrefixedDecoder(10)
    assert decoder.feed("1:a3:x") == ["a"]
    assert decoder.feed("yz") == ["xyz"]


def test_empty_feed_is_a_noop() -> None:
    decoder = LengthPrefixedDecoder(4)
    assert decoder.feed("") == []
    assert decoder.feed("2:ok") == ["ok"]


@pytest.mark.parametrize("maximum", [0, -1, True, 1.5, 1_000_001])
def test_invalid_maximum_is_rejected(maximum: object) -> None:
    with pytest.raises(ValueError):
        LengthPrefixedDecoder(maximum)

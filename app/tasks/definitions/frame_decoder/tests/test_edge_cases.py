import pytest
from solution import LengthPrefixedDecoder


@pytest.mark.parametrize("encoded", [":", "01:a", "x:a", "\uff11\uff12:a"])
def test_malformed_prefix_resets_decoder(encoded: str) -> None:
    decoder = LengthPrefixedDecoder(10)
    with pytest.raises(ValueError):
        decoder.feed(encoded)
    assert decoder.feed("2:ok") == ["ok"]


def test_oversized_prefix_is_detected_before_payload_and_resets() -> None:
    decoder = LengthPrefixedDecoder(3)
    with pytest.raises(ValueError, match="maximum"):
        decoder.feed("4")
    assert decoder.feed("3:yes") == ["yes"]


def test_nonstring_chunk_preserves_buffer() -> None:
    decoder = LengthPrefixedDecoder(10)
    assert decoder.feed("3:a") == []
    with pytest.raises(TypeError):
        decoder.feed(1)
    assert decoder.feed("bc") == ["abc"]


def test_finish_rejects_truncation_and_resets() -> None:
    decoder = LengthPrefixedDecoder(10)
    decoder.feed("3:ab")
    with pytest.raises(ValueError, match="truncated"):
        decoder.finish()
    assert decoder.feed("1:x") == ["x"]


def test_payload_can_contain_prefix_characters_and_unicode() -> None:
    decoder = LengthPrefixedDecoder(10)
    assert decoder.feed("4:a:🙂b") == ["a:🙂b"]

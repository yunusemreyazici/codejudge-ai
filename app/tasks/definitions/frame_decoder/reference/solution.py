"""Trusted incremental frame decoder oracle."""


class LengthPrefixedDecoder:
    def __init__(self, max_frame_size: int) -> None:
        if (
            isinstance(max_frame_size, bool)
            or not isinstance(max_frame_size, int)
            or not 0 < max_frame_size <= 1_000_000
        ):
            raise ValueError("max_frame_size must be between 1 and 1,000,000")
        self._maximum = max_frame_size
        self._buffer = ""
        self._expected: int | None = None

    def feed(self, chunk: str) -> list[str]:
        if not isinstance(chunk, str):
            raise TypeError("chunk must be a string")
        self._buffer += chunk
        frames: list[str] = []
        try:
            while True:
                if self._expected is None:
                    separator = self._buffer.find(":")
                    if separator < 0:
                        self._validate_partial_prefix()
                        break
                    prefix = self._buffer[:separator]
                    if not _valid_prefix(prefix):
                        raise ValueError("malformed frame length")
                    length = int(prefix)
                    if length > self._maximum:
                        raise ValueError("frame exceeds maximum size")
                    self._buffer = self._buffer[separator + 1 :]
                    self._expected = length
                if len(self._buffer) < self._expected:
                    break
                frames.append(self._buffer[: self._expected])
                self._buffer = self._buffer[self._expected :]
                self._expected = None
            return frames
        except ValueError:
            self._reset()
            raise

    def finish(self) -> None:
        if self._buffer or self._expected is not None:
            self._reset()
            raise ValueError("truncated frame")

    def _validate_partial_prefix(self) -> None:
        if not self._buffer:
            return
        if not self._buffer.isascii() or not self._buffer.isdigit():
            raise ValueError("malformed frame length")
        if self._buffer.startswith("0") and len(self._buffer) > 1:
            raise ValueError("malformed frame length")
        if int(self._buffer) > self._maximum:
            raise ValueError("frame exceeds maximum size")

    def _reset(self) -> None:
        self._buffer = ""
        self._expected = None


def _valid_prefix(prefix: str) -> bool:
    return (
        bool(prefix)
        and prefix.isascii()
        and prefix.isdigit()
        and (prefix == "0" or not prefix.startswith("0"))
    )

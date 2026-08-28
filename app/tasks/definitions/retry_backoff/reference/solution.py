"""Trusted retry-backoff oracle used only for generated-test validation."""


def retry_delay(
    attempt: int,
    base_delay: float,
    max_delay: float,
    multiplier: float = 2.0,
) -> float:
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("attempt must be a positive integer")
    if isinstance(base_delay, bool) or base_delay <= 0:
        raise ValueError("base_delay must be positive")
    if isinstance(max_delay, bool) or max_delay < base_delay:
        raise ValueError("max_delay must be at least base_delay")
    if isinstance(multiplier, bool) or multiplier < 1:
        raise ValueError("multiplier must be at least one")
    delay = float(base_delay)
    cap = float(max_delay)
    factor = float(multiplier)
    for _ in range(attempt - 1):
        if delay >= cap or factor == 1:
            break
        delay = min(cap, delay * factor)
    return min(delay, cap)

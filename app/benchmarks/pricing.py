"""Explicit versioned pricing snapshots for generation cost provenance."""

from __future__ import annotations

from decimal import Decimal

from app.benchmarks.models import PricingSnapshot


class PricingCatalog:
    def __init__(self, entries: dict[tuple[str, str], PricingSnapshot] | None = None) -> None:
        self._entries = dict(entries or {})

    def get(self, provider_id: str, model: str) -> PricingSnapshot | None:
        return self._entries.get((provider_id, model))


def calculate_generation_cost(
    pricing: PricingSnapshot | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> Decimal | None:
    if pricing is None or input_tokens is None or output_tokens is None:
        return None
    million = Decimal(1_000_000)
    return (
        Decimal(input_tokens) * pricing.input_cost_per_million_tokens / million
        + Decimal(output_tokens) * pricing.output_cost_per_million_tokens / million
    ).quantize(Decimal("0.000000000001"))

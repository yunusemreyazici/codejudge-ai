"""Small provider protocol independent of judging and worker orchestration."""

from __future__ import annotations

from typing import Protocol

from app.ai.models import ProviderResponse, StructuredLLMRequest


class ProviderError(RuntimeError):
    def __init__(self, code: str, *, transient: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.transient = transient


class LLMProvider(Protocol):
    async def complete_structured(self, request: StructuredLLMRequest) -> ProviderResponse: ...

    async def complete_raw_source(self, request: StructuredLLMRequest) -> ProviderResponse: ...

    async def close(self) -> None: ...

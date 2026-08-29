"""Small provider protocol independent of judging and worker orchestration."""

from __future__ import annotations

from typing import Protocol

from app.ai.models import ProviderResponse, StructuredLLMRequest


class ProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        transient: bool = False,
        http_status: int | None = None,
        detail_code: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.transient = transient
        self.http_status = http_status
        self.detail_code = detail_code


class LLMProvider(Protocol):
    async def complete_structured(self, request: StructuredLLMRequest) -> ProviderResponse: ...

    async def complete_raw_source(self, request: StructuredLLMRequest) -> ProviderResponse: ...

    async def close(self) -> None: ...

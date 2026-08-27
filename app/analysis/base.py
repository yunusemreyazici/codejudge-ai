"""Small typed contracts shared by static analyzers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.evaluator.models import AnalysisTool, ComplexityMetrics, Finding, StaticAnalysisResult


@dataclass(frozen=True, slots=True)
class CandidateSource:
    """Immutable, exact candidate source passed to every analyzer."""

    code: str
    language: str


@dataclass(frozen=True, slots=True)
class AnalyzerReport:
    """One analyzer's normalized output."""

    findings: list[Finding] = field(default_factory=list)
    complexity: ComplexityMetrics | None = None


class SourceAnalyzer(Protocol):
    tool: AnalysisTool

    async def analyze(self, candidate: CandidateSource, source_path: Path) -> AnalyzerReport: ...


class StaticAnalysisProvider(Protocol):
    async def analyze(self, candidate: CandidateSource) -> StaticAnalysisResult: ...

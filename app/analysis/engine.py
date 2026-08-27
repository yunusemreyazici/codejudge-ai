"""Static-analysis orchestration independent of execution runners."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from app.analysis.analyzers import AnalyzerFailure
from app.analysis.base import CandidateSource, SourceAnalyzer
from app.evaluator.models import ComplexityMetrics, Finding, StaticAnalysisResult


class StaticAnalysisInfrastructureError(RuntimeError):
    """Static analysis could not produce a complete, trustworthy result."""


class StaticAnalysisEngine:
    """Analyze one immutable source file with a small ordered analyzer set."""

    def __init__(self, analyzers: tuple[SourceAnalyzer, ...]) -> None:
        if not analyzers:
            raise ValueError("At least one static analyzer is required")
        self._analyzers = analyzers

    async def analyze(self, candidate: CandidateSource) -> StaticAnalysisResult:
        findings: list[Finding] = []
        complexity: ComplexityMetrics | None = None
        with tempfile.TemporaryDirectory(prefix="codejudge-analysis-") as temporary_directory:
            source_path = Path(temporary_directory) / "solution.py"
            source_path.write_text(candidate.code, encoding="utf-8")
            for analyzer in self._analyzers:
                try:
                    report = await analyzer.analyze(candidate, source_path)
                except asyncio.CancelledError:
                    raise
                except AnalyzerFailure as error:
                    raise StaticAnalysisInfrastructureError(str(error)) from error
                except Exception as error:
                    raise StaticAnalysisInfrastructureError(
                        f"Static analyzer '{analyzer.tool}' failed."
                    ) from error
                findings.extend(report.findings)
                if report.complexity is not None:
                    if complexity is not None:
                        raise StaticAnalysisInfrastructureError(
                            "Multiple complexity analyzers returned metrics."
                        )
                    complexity = report.complexity

        if complexity is None:
            raise StaticAnalysisInfrastructureError(
                "Static analysis did not produce complexity metrics."
            )
        return StaticAnalysisResult(findings=findings, complexity=complexity)

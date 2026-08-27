import asyncio
from pathlib import Path

import pytest

from app.analysis.base import AnalyzerReport, CandidateSource
from app.analysis.engine import StaticAnalysisEngine, StaticAnalysisInfrastructureError
from app.evaluator.models import AnalysisTool, ComplexityMetrics


class InspectingAnalyzer:
    tool = AnalysisTool.RADON

    def __init__(self) -> None:
        self.source_path: Path | None = None
        self.code: str | None = None
        self.workspace_entries: list[str] = []

    async def analyze(self, candidate: CandidateSource, source_path: Path) -> AnalyzerReport:
        self.source_path = source_path
        self.code, self.workspace_entries = await asyncio.to_thread(_inspect_workspace, source_path)
        assert candidate.code == self.code
        return AnalyzerReport(complexity=ComplexityMetrics(maximum=1, average=1, blocks=1))


class CrashingAnalyzer:
    tool = AnalysisTool.RADON

    async def analyze(self, candidate: CandidateSource, source_path: Path) -> AnalyzerReport:
        del candidate, source_path
        raise ValueError("raw internal detail")


def _inspect_workspace(source_path: Path) -> tuple[str, list[str]]:
    return (
        source_path.read_text(encoding="utf-8"),
        sorted(path.name for path in source_path.parent.iterdir()),
    )


async def test_engine_writes_only_exact_source_and_cleans_workspace() -> None:
    analyzer = InspectingAnalyzer()
    source = "value = 'é'\n"

    result = await StaticAnalysisEngine((analyzer,)).analyze(
        CandidateSource(code=source, language="python")
    )

    assert analyzer.code == source
    assert analyzer.workspace_entries == ["solution.py"]
    assert analyzer.source_path is not None
    assert analyzer.source_path.exists() is False
    assert result.complexity.maximum == 1


async def test_engine_sanitizes_unexpected_analyzer_failure() -> None:
    with pytest.raises(
        StaticAnalysisInfrastructureError,
        match="Static analyzer 'radon' failed",
    ) as captured:
        await StaticAnalysisEngine((CrashingAnalyzer(),)).analyze(
            CandidateSource(code="pass\n", language="python")
        )

    assert "raw internal detail" not in str(captured.value)

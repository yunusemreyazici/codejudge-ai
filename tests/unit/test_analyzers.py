from pathlib import Path

import pytest

from app.analysis.analyzers import (
    AnalyzerFailure,
    BanditAnalyzer,
    MypyAnalyzer,
    RadonAnalyzer,
    RuffAnalyzer,
)
from app.analysis.base import CandidateSource
from app.analysis.command import AnalyzerCommandResult, AnalyzerCommandRunner
from app.analysis.engine import StaticAnalysisEngine
from app.evaluator.models import AnalysisTool, FindingCategory, FindingConfidence, FindingSeverity


def _runner() -> AnalyzerCommandRunner:
    return AnalyzerCommandRunner(timeout_seconds=5, output_limit_bytes=256 * 1024)


class TruncatedAnalyzerCommandRunner:
    async def run(self, arguments: object, *, working_directory: Path) -> AnalyzerCommandResult:
        del arguments, working_directory
        return AnalyzerCommandResult(
            exit_code=1,
            stdout='[{"code":"F401"',
            stderr="",
            duration_seconds=0.01,
            output_truncated=True,
        )


async def test_ruff_clean_source_has_no_findings(tmp_path: Path) -> None:
    source = "def double(value: int) -> int:\n    return value * 2\n"
    source_path = tmp_path / "solution.py"
    source_path.write_text(source, encoding="utf-8")

    report = await RuffAnalyzer(_runner()).analyze(CandidateSource(source, "python"), source_path)

    assert report.findings == []


async def test_ruff_maps_structured_issue_location_code_and_fixability(tmp_path: Path) -> None:
    source = "def calculate():\n    unused = 1\n    return 2\n"
    source_path = tmp_path / "solution.py"
    source_path.write_text(source, encoding="utf-8")

    report = await RuffAnalyzer(_runner()).analyze(CandidateSource(source, "python"), source_path)

    finding = report.findings[0]
    assert finding.tool is AnalysisTool.RUFF
    assert finding.category is FindingCategory.QUALITY
    assert finding.code == "F841"
    assert finding.line == 2
    assert finding.column == 5
    assert finding.fixable is True


async def test_ruff_truncated_json_remains_bounded_infrastructure_failure(tmp_path: Path) -> None:
    source_path = tmp_path / "solution.py"
    source_path.write_text("unused = 1\n", encoding="utf-8")
    analyzer = RuffAnalyzer(TruncatedAnalyzerCommandRunner())  # type: ignore[arg-type]

    with pytest.raises(AnalyzerFailure, match="exceeded its output limit") as captured:
        await analyzer.analyze(CandidateSource("unused = 1\n", "python"), source_path)

    assert "F401" not in str(captured.value)


async def test_mypy_accepts_clean_typed_source(tmp_path: Path) -> None:
    source = "def double(value: int) -> int:\n    return value * 2\n"
    source_path = tmp_path / "solution.py"
    source_path.write_text(source, encoding="utf-8")

    report = await MypyAnalyzer(_runner()).analyze(CandidateSource(source, "python"), source_path)

    assert report.findings == []


async def test_mypy_maps_definite_type_error(tmp_path: Path) -> None:
    source = 'answer: int = "wrong"\n'
    source_path = tmp_path / "solution.py"
    source_path.write_text(source, encoding="utf-8")

    report = await MypyAnalyzer(_runner()).analyze(CandidateSource(source, "python"), source_path)

    finding = report.findings[0]
    assert finding.tool is AnalysisTool.MYPY
    assert finding.category is FindingCategory.TYPE_SAFETY
    assert finding.severity is FindingSeverity.ERROR
    assert finding.code == "assignment"
    assert finding.line == 1
    assert finding.column == 14


async def test_mypy_ignores_repository_and_candidate_adjacent_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    poison_directory = tmp_path / "poison"
    poison_directory.mkdir()
    (poison_directory / "pyproject.toml").write_text(
        '[tool.mypy]\nplugins = ["definitely_missing_plugin"]\n', encoding="utf-8"
    )
    monkeypatch.chdir(poison_directory)
    engine = StaticAnalysisEngine((MypyAnalyzer(_runner()), RadonAnalyzer(_runner())))

    result = await engine.analyze(
        CandidateSource("def identity(value: int) -> int:\n    return value\n", "python")
    )

    assert [finding for finding in result.findings if finding.tool is AnalysisTool.MYPY] == []


async def test_bandit_clean_source_has_no_findings(tmp_path: Path) -> None:
    source = "def double(value: int) -> int:\n    return value * 2\n"
    source_path = tmp_path / "solution.py"
    source_path.write_text(source, encoding="utf-8")

    report = await BanditAnalyzer(_runner()).analyze(CandidateSource(source, "python"), source_path)

    assert report.findings == []


async def test_bandit_maps_security_smell_and_severity(tmp_path: Path) -> None:
    source = 'result = eval("1 + 1")\n'
    source_path = tmp_path / "solution.py"
    source_path.write_text(source, encoding="utf-8")

    report = await BanditAnalyzer(_runner()).analyze(CandidateSource(source, "python"), source_path)

    finding = next(item for item in report.findings if item.code == "B307")
    assert finding.category is FindingCategory.SECURITY
    assert finding.severity is FindingSeverity.WARNING
    assert finding.confidence is FindingConfidence.HIGH
    assert finding.line == 1


async def test_radon_reports_simple_complexity(tmp_path: Path) -> None:
    source = "def choose(value: bool) -> int:\n    if value:\n        return 1\n    return 0\n"
    source_path = tmp_path / "solution.py"
    source_path.write_text(source, encoding="utf-8")

    report = await RadonAnalyzer(_runner()).analyze(CandidateSource(source, "python"), source_path)

    assert report.complexity is not None
    assert report.complexity.maximum == 2
    assert report.complexity.average == 2
    assert report.complexity.blocks == 1


async def test_radon_reports_maximum_and_average_for_complex_source(tmp_path: Path) -> None:
    source = (
        "def complex_function(value: int) -> int:\n"
        + "".join(f"    if value == {index}:\n        return {index}\n" for index in range(11))
        + "    return -1\n"
    )
    source_path = tmp_path / "solution.py"
    source_path.write_text(source, encoding="utf-8")

    report = await RadonAnalyzer(_runner()).analyze(CandidateSource(source, "python"), source_path)

    assert report.complexity is not None
    assert report.complexity.maximum == 12
    assert report.complexity.average == 12

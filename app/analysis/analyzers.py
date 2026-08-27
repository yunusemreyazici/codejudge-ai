"""Ruff, mypy, Bandit, and Radon adapters with normalized typed output."""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from app.analysis.base import AnalyzerReport, CandidateSource
from app.analysis.command import AnalyzerCommandResult, AnalyzerCommandRunner
from app.evaluator.models import (
    AnalysisTool,
    ComplexityMetrics,
    Finding,
    FindingCategory,
    FindingConfidence,
    FindingSeverity,
)


class AnalyzerFailure(RuntimeError):
    """A trusted analyzer failed, timed out, or returned malformed output."""

    def __init__(self, tool: AnalysisTool, reason: str) -> None:
        super().__init__(f"Static analyzer '{tool}' {reason}.")
        self.tool = tool


class _Analyzer:
    tool: AnalysisTool

    def __init__(self, command_runner: AnalyzerCommandRunner) -> None:
        self._command_runner = command_runner

    async def _run(self, arguments: list[str], source_path: Path) -> AnalyzerCommandResult:
        result = await self._command_runner.run(
            arguments,
            working_directory=source_path.parent,
        )
        if result.exit_code is None:
            raise AnalyzerFailure(self.tool, "could not be started")
        if result.timed_out:
            raise AnalyzerFailure(self.tool, "timed out")
        if result.output_truncated:
            raise AnalyzerFailure(self.tool, "exceeded its output limit")
        return result


class RuffAnalyzer(_Analyzer):
    tool = AnalysisTool.RUFF

    async def analyze(self, candidate: CandidateSource, source_path: Path) -> AnalyzerReport:
        del candidate
        result = await self._run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                "--isolated",
                "--no-cache",
                "--ignore-noqa",
                "--select",
                "E,F,B,UP,SIM",
                "--output-format",
                "json",
                source_path.name,
            ],
            source_path,
        )
        if result.exit_code not in {0, 1}:
            raise AnalyzerFailure(self.tool, "failed")
        payload = _json_value(result.stdout, self.tool)
        if not isinstance(payload, list):
            raise AnalyzerFailure(self.tool, "returned invalid output")
        return AnalyzerReport(findings=[self._finding(item) for item in payload])

    def _finding(self, raw: object) -> Finding:
        item = _mapping(raw, self.tool)
        code = _string(item, "code", self.tool)
        location = _mapping(item.get("location"), self.tool)
        end_location = _mapping(item.get("end_location"), self.tool)
        prefix = code[:1]
        severity = (
            FindingSeverity.ERROR
            if prefix in {"E", "F"}
            else FindingSeverity.WARNING
            if prefix == "B"
            else FindingSeverity.INFO
        )
        return Finding(
            severity=severity,
            category=FindingCategory.QUALITY,
            tool=self.tool,
            code=code,
            message=_string(item, "message", self.tool),
            line=_integer(location, "row", self.tool),
            column=_integer(location, "column", self.tool),
            end_line=_integer(end_location, "row", self.tool),
            end_column=_integer(end_location, "column", self.tool),
            fixable=isinstance(item.get("fix"), dict),
        )


class MypyAnalyzer(_Analyzer):
    tool = AnalysisTool.MYPY
    _config_path = Path(__file__).with_name("mypy.ini")

    async def analyze(self, candidate: CandidateSource, source_path: Path) -> AnalyzerReport:
        try:
            ast.parse(candidate.code, filename=source_path.name)
        except SyntaxError as error:
            return AnalyzerReport(
                findings=[
                    Finding(
                        severity=FindingSeverity.ERROR,
                        category=FindingCategory.TYPE_SAFETY,
                        tool=self.tool,
                        code="syntax",
                        message=error.msg,
                        line=_positive_optional_integer(error.lineno),
                        column=_positive_optional_integer(error.offset),
                        end_line=_positive_optional_integer(error.end_lineno),
                        end_column=_positive_optional_integer(error.end_offset),
                    )
                ]
            )
        result = await self._run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--output",
                "json",
                "--config-file",
                str(self._config_path),
                "--no-incremental",
                "--cache-dir=/dev/null",
                "--no-site-packages",
                "--follow-imports=skip",
                source_path.name,
            ],
            source_path,
        )
        if result.exit_code not in {0, 1}:
            raise AnalyzerFailure(self.tool, "failed")
        findings: list[Finding] = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            raw = _json_value(line, self.tool)
            item = _mapping(raw, self.tool)
            severity_value = _string(item, "severity", self.tool)
            severity = {
                "error": FindingSeverity.ERROR,
                "warning": FindingSeverity.WARNING,
                "note": FindingSeverity.INFO,
            }.get(severity_value, FindingSeverity.INFO)
            findings.append(
                Finding(
                    severity=severity,
                    category=FindingCategory.TYPE_SAFETY,
                    tool=self.tool,
                    code=_optional_string(item.get("code")),
                    message=_string(item, "message", self.tool),
                    line=_positive_optional_integer(item.get("line")),
                    column=_positive_optional_integer(item.get("column")),
                    end_line=_positive_optional_integer(item.get("end_line")),
                    end_column=_positive_optional_integer(item.get("end_column")),
                )
            )
        return AnalyzerReport(findings=findings)


class BanditAnalyzer(_Analyzer):
    tool = AnalysisTool.BANDIT

    async def analyze(self, candidate: CandidateSource, source_path: Path) -> AnalyzerReport:
        del candidate
        result = await self._run(
            [
                sys.executable,
                "-m",
                "bandit",
                "--format",
                "json",
                "--quiet",
                "--ignore-nosec",
                source_path.name,
            ],
            source_path,
        )
        if result.exit_code not in {0, 1}:
            raise AnalyzerFailure(self.tool, "failed")
        payload = _mapping(_json_value(result.stdout, self.tool), self.tool)
        results = payload.get("results")
        errors = payload.get("errors")
        if not isinstance(results, list) or not isinstance(errors, list):
            raise AnalyzerFailure(self.tool, "returned invalid output")
        findings = [self._finding(item) for item in results]
        for error in errors:
            error_item = _mapping(error, self.tool)
            reason = _optional_string(error_item.get("reason")) or "Source could not be parsed"
            findings.append(
                Finding(
                    severity=FindingSeverity.ERROR,
                    category=FindingCategory.SECURITY,
                    tool=self.tool,
                    code="parse-error",
                    message=reason,
                    line=_positive_optional_integer(error_item.get("line")),
                )
            )
        return AnalyzerReport(findings=findings)

    def _finding(self, raw: object) -> Finding:
        item = _mapping(raw, self.tool)
        severity = {
            "LOW": FindingSeverity.INFO,
            "MEDIUM": FindingSeverity.WARNING,
            "HIGH": FindingSeverity.ERROR,
        }[_string(item, "issue_severity", self.tool)]
        confidence = FindingConfidence(_string(item, "issue_confidence", self.tool).lower())
        column = _integer(item, "col_offset", self.tool) + 1
        end_column = _integer(item, "end_col_offset", self.tool) + 1
        return Finding(
            severity=severity,
            category=FindingCategory.SECURITY,
            tool=self.tool,
            code=_string(item, "test_id", self.tool),
            message=_string(item, "issue_text", self.tool),
            line=_integer(item, "line_number", self.tool),
            column=column,
            end_column=end_column,
            confidence=confidence,
        )


class RadonAnalyzer(_Analyzer):
    tool = AnalysisTool.RADON

    async def analyze(self, candidate: CandidateSource, source_path: Path) -> AnalyzerReport:
        del candidate
        result = await self._run(
            [sys.executable, "-m", "radon", "cc", "--json", "--show-complexity", source_path.name],
            source_path,
        )
        if result.exit_code == 1 and _looks_like_candidate_parse_error(result.stderr):
            return AnalyzerReport(
                findings=[
                    Finding(
                        severity=FindingSeverity.ERROR,
                        category=FindingCategory.COMPLEXITY,
                        tool=self.tool,
                        code="parse-error",
                        message=(
                            "Cyclomatic complexity could not be measured because the source "
                            "is invalid."
                        ),
                    )
                ],
                complexity=ComplexityMetrics(
                    maximum=0,
                    average=0,
                    blocks=0,
                    analyzable=False,
                ),
            )
        if result.exit_code != 0:
            raise AnalyzerFailure(self.tool, "failed")
        payload = _mapping(_json_value(result.stdout, self.tool), self.tool)
        raw_blocks = payload.get(source_path.name)
        if isinstance(raw_blocks, dict) and isinstance(raw_blocks.get("error"), str):
            return AnalyzerReport(
                findings=[
                    Finding(
                        severity=FindingSeverity.ERROR,
                        category=FindingCategory.COMPLEXITY,
                        tool=self.tool,
                        code="parse-error",
                        message=(
                            "Cyclomatic complexity could not be measured because the source "
                            "is invalid."
                        ),
                    )
                ],
                complexity=ComplexityMetrics(
                    maximum=0,
                    average=0,
                    blocks=0,
                    analyzable=False,
                ),
            )
        if not isinstance(raw_blocks, list):
            raise AnalyzerFailure(self.tool, "returned invalid output")
        complexities = _complexities(raw_blocks, self.tool)
        maximum = max(complexities, default=0)
        average = 0.0 if not complexities else sum(complexities) / len(complexities)
        return AnalyzerReport(
            complexity=ComplexityMetrics(
                maximum=maximum,
                average=round(average, 2),
                blocks=len(complexities),
            )
        )


def _complexities(raw_blocks: list[object], tool: AnalysisTool) -> list[int]:
    complexities: list[int] = []
    for raw in raw_blocks:
        block = _mapping(raw, tool)
        complexities.append(_integer(block, "complexity", tool))
        methods = block.get("methods", [])
        closures = block.get("closures", [])
        if not isinstance(methods, list) or not isinstance(closures, list):
            raise AnalyzerFailure(tool, "returned invalid output")
        complexities.extend(_complexities(cast(list[object], methods), tool))
        complexities.extend(_complexities(cast(list[object], closures), tool))
    return complexities


def _looks_like_candidate_parse_error(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(
        marker in lowered
        for marker in ("syntaxerror", "invalid syntax", "unexpected indent", "expected an indented")
    )


def _json_value(value: str, tool: AnalysisTool) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise AnalyzerFailure(tool, "returned invalid output") from error


def _mapping(value: object, tool: AnalysisTool) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise AnalyzerFailure(tool, "returned invalid output")
    return cast(Mapping[str, object], value)


def _string(item: Mapping[str, object], key: str, tool: AnalysisTool) -> str:
    value = item.get(key)
    if not isinstance(value, str):
        raise AnalyzerFailure(tool, "returned invalid output")
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _integer(item: Mapping[str, object], key: str, tool: AnalysisTool) -> int:
    value = item.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise AnalyzerFailure(tool, "returned invalid output")
    return value


def _positive_optional_integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    return None

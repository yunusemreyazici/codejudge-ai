"""Construct the configured Python static-analysis pipeline."""

from app.analysis.analyzers import BanditAnalyzer, MypyAnalyzer, RadonAnalyzer, RuffAnalyzer
from app.analysis.command import AnalyzerCommandRunner
from app.analysis.engine import StaticAnalysisEngine
from app.core.config import Settings


def create_static_analysis_engine(settings: Settings) -> StaticAnalysisEngine:
    command_runner = AnalyzerCommandRunner(
        timeout_seconds=settings.static_analysis_timeout_seconds,
        output_limit_bytes=settings.static_analysis_output_limit_bytes,
    )
    return StaticAnalysisEngine(
        analyzers=(
            RuffAnalyzer(command_runner),
            MypyAnalyzer(command_runner),
            BanditAnalyzer(command_runner),
            RadonAnalyzer(command_runner),
        )
    )

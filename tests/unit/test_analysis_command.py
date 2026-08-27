import sys
from pathlib import Path

from app.analysis.command import AnalyzerCommandRunner


async def test_analyzer_command_enforces_timeout(tmp_path: Path) -> None:
    runner = AnalyzerCommandRunner(timeout_seconds=0.05, output_limit_bytes=1024)

    result = await runner.run(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        working_directory=tmp_path,
    )

    assert result.timed_out is True
    assert result.duration_seconds < 2


async def test_analyzer_command_bounds_combined_output(tmp_path: Path) -> None:
    runner = AnalyzerCommandRunner(timeout_seconds=2, output_limit_bytes=64)

    result = await runner.run(
        [sys.executable, "-c", 'import sys; print("x" * 100); print("y" * 100, file=sys.stderr)'],
        working_directory=tmp_path,
    )

    assert result.output_truncated is True
    assert len((result.stdout + result.stderr).encode()) == 64

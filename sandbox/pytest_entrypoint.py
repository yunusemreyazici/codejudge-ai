"""Legacy generated-test entrypoint used only by Phase 6 adversarial validation."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest


class StructuredReporter:
    def __init__(self) -> None:
        self._started_at = time.monotonic()

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: pytest.ExitCode) -> None:
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        stats = reporter.stats if reporter is not None else {}
        passed = len(stats.get("passed", []))
        reported_failed = len(stats.get("failed", [])) + len(stats.get("error", []))
        collected = int(session.testscollected)
        total = max(collected, passed + reported_failed)
        failure_details = "\n".join(
            str(report.longrepr)
            for category in ("error", "failed")
            for report in stats.get(category, [])
        )
        payload = {
            "passed": passed,
            "failed": total - passed,
            "total": total,
            "duration_seconds": time.monotonic() - self._started_at,
            "syntax_error": "SyntaxError" in failure_details,
            "import_error": (
                "ImportError" in failure_details or "ModuleNotFoundError" in failure_details
            ),
        }
        Path(os.environ["CODEJUDGE_REPORT_PATH"]).write_text(json.dumps(payload), encoding="utf-8")


def main() -> int:
    return int(
        pytest.main(
            [
                "-q",
                "/workspace/task_tests",
                "--tb=short",
                "--disable-warnings",
                "--capture=no",
                "-p",
                "no:cacheprovider",
            ],
            plugins=[StructuredReporter()],
        )
    )

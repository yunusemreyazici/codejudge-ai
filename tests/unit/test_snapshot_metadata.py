from collections.abc import Sequence

from app.core.config import ExecutionBackend, Settings
from app.runners.docker_cli import CommandResult
from app.snapshots.metadata import ExecutionMetadataCollector, analyzer_versions


class FakeDockerClient:
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.calls: list[Sequence[str]] = []

    async def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
    ) -> CommandResult:
        del timeout_seconds, output_limit_bytes
        self.calls.append(arguments)
        return self.result


async def test_execution_metadata_collects_and_caches_docker_image_id() -> None:
    client = FakeDockerClient(
        CommandResult(
            exit_code=0,
            stdout="sha256:trusted-image\n",
            stderr="",
            duration_seconds=0.01,
        )
    )
    collector = ExecutionMetadataCollector(
        Settings(execution_backend=ExecutionBackend.DOCKER),
        docker_client=client,
    )

    first = await collector.snapshot()
    second = await collector.snapshot()

    assert first == second
    assert first.sandbox_image_id == "sha256:trusted-image"
    assert len(client.calls) == 1


async def test_execution_metadata_tolerates_unavailable_image_identity() -> None:
    collector = ExecutionMetadataCollector(
        Settings(execution_backend=ExecutionBackend.DOCKER),
        docker_client=FakeDockerClient(
            CommandResult(
                exit_code=1,
                stdout="",
                stderr="unavailable",
                duration_seconds=0.01,
            )
        ),
    )

    assert (await collector.snapshot()).sandbox_image_id is None


def test_analyzer_versions_come_from_installed_package_metadata() -> None:
    versions = analyzer_versions()

    assert set(versions) == {"ruff", "mypy", "bandit", "radon"}
    assert "unavailable" not in versions.values()

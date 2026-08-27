"""Trusted, cached runtime metadata used in reproducibility snapshots."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from typing import Protocol

from app.core.config import ExecutionBackend, Settings
from app.runners.docker_cli import CommandResult, DockerCli
from app.snapshots.models import ExecutionEnvironmentSnapshot

_ANALYZER_PACKAGES = ("ruff", "mypy", "bandit", "radon")


@lru_cache(maxsize=1)
def analyzer_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in _ANALYZER_PACKAGES:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "unavailable"
    return versions


class DockerMetadataClient(Protocol):
    async def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
    ) -> CommandResult: ...


class ExecutionMetadataProvider(Protocol):
    async def snapshot(self) -> ExecutionEnvironmentSnapshot: ...


class ExecutionMetadataCollector:
    """Collect an allowlisted backend/image identity once per application process."""

    def __init__(
        self,
        settings: Settings,
        docker_client: DockerMetadataClient | None = None,
    ) -> None:
        self._settings = settings
        self._docker_client = docker_client or DockerCli()
        self._cached: ExecutionEnvironmentSnapshot | None = None
        self._lock = asyncio.Lock()

    async def snapshot(self) -> ExecutionEnvironmentSnapshot:
        if self._cached is not None:
            return self._cached
        async with self._lock:
            if self._cached is None:
                self._cached = await self._collect()
        return self._cached

    async def _collect(self) -> ExecutionEnvironmentSnapshot:
        if self._settings.execution_backend is ExecutionBackend.LOCAL:
            return ExecutionEnvironmentSnapshot(backend=ExecutionBackend.LOCAL)

        result = await self._docker_client.run(
            ["image", "inspect", "--format", "{{.Id}}", self._settings.sandbox_image],
            timeout_seconds=5,
            output_limit_bytes=4096,
        )
        image_id = result.stdout.strip() if result.exit_code == 0 and not result.timed_out else None
        if image_id is not None and not image_id.startswith("sha256:"):
            image_id = None
        return ExecutionEnvironmentSnapshot(
            backend=ExecutionBackend.DOCKER,
            sandbox_image=self._settings.sandbox_image,
            sandbox_image_id=image_id,
        )


def canonical_analyzer_versions(versions: Mapping[str, str] | None = None) -> dict[str, str]:
    return dict(sorted((versions or analyzer_versions()).items()))

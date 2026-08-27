"""Build the configured Python execution backend."""

from app.core.config import ExecutionBackend, Settings
from app.runners.base import CodeRunner
from app.runners.docker_runner import DockerPythonRunner, DockerSandboxConfig
from app.runners.python_runner import PythonRunner


def create_python_runner(settings: Settings) -> CodeRunner:
    if settings.execution_backend is ExecutionBackend.LOCAL:
        return PythonRunner()
    return DockerPythonRunner(
        DockerSandboxConfig(
            image=settings.sandbox_image,
            memory_mb=settings.sandbox_memory_mb,
            cpus=settings.sandbox_cpus,
            pids_limit=settings.sandbox_pids_limit,
            timeout_seconds=settings.sandbox_timeout_seconds,
            output_limit_bytes=settings.sandbox_output_limit_bytes,
        )
    )

from app.core.config import ExecutionBackend, Settings
from app.runners.docker_runner import DockerPythonRunner
from app.runners.factory import create_python_runner
from app.runners.python_runner import PythonRunner


def test_factory_selects_docker_by_default() -> None:
    assert isinstance(create_python_runner(Settings()), DockerPythonRunner)


def test_factory_selects_local_only_when_explicit() -> None:
    settings = Settings(execution_backend=ExecutionBackend.LOCAL)

    assert isinstance(create_python_runner(settings), PythonRunner)

import json
from collections.abc import Sequence
from pathlib import Path

from app.runners.docker_cli import CommandResult
from app.runners.docker_runner import DockerPythonRunner, DockerSandboxConfig
from app.tasks.registry import TaskRegistry


def _result(
    exit_code: int = 0,
    *,
    stdout: str = "",
    timed_out: bool = False,
    output_truncated: bool = False,
) -> CommandResult:
    return CommandResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr="",
        duration_seconds=0.01,
        timed_out=timed_out,
        output_truncated=output_truncated,
    )


class FakeDockerClient:
    def __init__(
        self,
        *,
        start_result: CommandResult | None = None,
        state: dict[str, object] | None = None,
        capability_available: bool = True,
        write_report: bool = True,
        create_result: CommandResult | None = None,
    ) -> None:
        self.start_result = start_result or _result()
        self.state = state or {"ExitCode": 0, "OOMKilled": False}
        self.capability_available = capability_available
        self.write_report = write_report
        self.create_result = create_result or _result(stdout="container-id\n")
        self.commands: list[list[str]] = []
        self.report_path: Path | None = None

    async def run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        output_limit_bytes: int,
    ) -> CommandResult:
        command = list(arguments)
        self.commands.append(command)
        if command[:2] == ["info", "--format"]:
            return _result() if self.capability_available else _result(1)
        if command[:2] == ["image", "inspect"]:
            return _result()
        if command[0] == "create":
            results_mount = next(
                command[index + 1]
                for index, value in enumerate(command)
                if value == "--mount" and "target=/results/report.json" in command[index + 1]
            )
            source = results_mount.split("source=", 1)[1].split(",target=", 1)[0]
            self.report_path = Path(source)
            return self.create_result
        if command[:2] == ["start", "--attach"]:
            if self.write_report and not self.start_result.timed_out:
                if self.report_path is None:
                    raise AssertionError("create command was not observed")
                report = {"passed": 8, "failed": 0, "total": 8}
                self.report_path.write_text(json.dumps(report))
            return self.start_result
        if command[0] == "inspect":
            return _result(stdout=json.dumps(self.state))
        if command[0] in {"kill", "rm"}:
            return _result()
        raise AssertionError(f"Unexpected Docker command: {command}")


def _runner(client: FakeDockerClient) -> DockerPythonRunner:
    return DockerPythonRunner(
        DockerSandboxConfig(
            image="codejudge-python-sandbox:phase2",
            memory_mb=256,
            cpus=0.5,
            pids_limit=64,
            timeout_seconds=2,
            output_limit_bytes=1024,
        ),
        cli=client,
    )


async def test_runner_builds_restricted_container_and_cleans_up(correct_lru: str) -> None:
    client = FakeDockerClient()

    result = await _runner(client).evaluate(TaskRegistry.default().get("lru-cache"), correct_lru)

    assert result.passed == 8
    create = next(command for command in client.commands if command[0] == "create")
    assert ["--network", "none"] == create[create.index("--network") :][:2]
    assert ["--cap-drop", "ALL"] == create[create.index("--cap-drop") :][:2]
    assert "--read-only" in create
    assert "--privileged" not in create
    assert "/var/run/docker.sock" not in " ".join(create)
    assert ["--user", "10001:10001"] == create[create.index("--user") :][:2]
    assert ["--pids-limit", "64"] == create[create.index("--pids-limit") :][:2]
    assert ["--ulimit", "fsize=65536:65536"] == create[create.index("--ulimit") :][:2]
    assert ["--memory", "256m"] == create[create.index("--memory") :][:2]
    assert ["--cpus", "0.5"] == create[create.index("--cpus") :][:2]
    assert ["--memory-swap", "256m"] == create[create.index("--memory-swap") :][:2]
    assert ["--log-driver", "local"] == create[create.index("--log-driver") :][:2]
    log_options = [create[index + 1] for index, value in enumerate(create) if value == "--log-opt"]
    assert log_options == ["max-size=1k", "max-file=1"]
    assert ["--security-opt", "no-new-privileges=true"] == create[create.index("--security-opt") :][
        :2
    ]
    environment = [create[index + 1] for index, value in enumerate(create) if value == "--env"]
    assert environment == [
        "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONUNBUFFERED=1",
        "PYTHONPATH=/workspace",
        "CODEJUDGE_REPORT_PATH=/results/report.json",
    ]
    assert client.commands[-1][0:2] == ["rm", "--force"]


async def test_runner_kills_and_removes_timed_out_container(correct_lru: str) -> None:
    client = FakeDockerClient(start_result=_result(timed_out=True))

    result = await _runner(client).evaluate(TaskRegistry.default().get("lru-cache"), correct_lru)

    assert result.timed_out is True
    assert result.enforced_timeout_seconds == 2
    assert any(command[0] == "kill" for command in client.commands)
    assert client.commands[-1][0:2] == ["rm", "--force"]


async def test_runner_uses_inspect_metadata_for_oom(correct_lru: str) -> None:
    client = FakeDockerClient(state={"ExitCode": 137, "OOMKilled": True})

    result = await _runner(client).evaluate(TaskRegistry.default().get("lru-cache"), correct_lru)

    assert result.oom_killed is True
    assert result.exit_code == 137
    assert client.commands[-1][0:2] == ["rm", "--force"]


async def test_runner_reports_unavailable_daemon_without_local_fallback(
    correct_lru: str,
) -> None:
    client = FakeDockerClient(capability_available=False)

    result = await _runner(client).evaluate(TaskRegistry.default().get("lru-cache"), correct_lru)

    assert result.infrastructure_error == "Docker daemon is unavailable."
    assert not any(command[0] == "create" for command in client.commands)


async def test_runner_cleans_up_when_report_is_invalid(correct_lru: str) -> None:
    client = FakeDockerClient(write_report=False)

    result = await _runner(client).evaluate(TaskRegistry.default().get("lru-cache"), correct_lru)

    assert result.sandbox_error == "Sandbox exited without a valid structured test report."
    assert client.commands[-1][0:2] == ["rm", "--force"]


async def test_runner_propagates_bounded_output_state(correct_lru: str) -> None:
    client = FakeDockerClient(start_result=_result(output_truncated=True))

    result = await _runner(client).evaluate(TaskRegistry.default().get("lru-cache"), correct_lru)

    assert result.output_truncated is True


async def test_runner_attempts_cleanup_when_create_command_fails(correct_lru: str) -> None:
    client = FakeDockerClient(create_result=_result(1))

    result = await _runner(client).evaluate(TaskRegistry.default().get("lru-cache"), correct_lru)

    assert result.infrastructure_error == "Docker could not create the evaluation container."
    assert client.commands[-1][0:2] == ["rm", "--force"]


async def test_capability_reports_missing_cli() -> None:
    runner = DockerPythonRunner(
        DockerSandboxConfig(
            image="codejudge-python-sandbox:phase2",
            memory_mb=256,
            cpus=0.5,
            pids_limit=64,
            timeout_seconds=5,
            output_limit_bytes=1024,
        ),
        docker_binary="codejudge-docker-command-that-does-not-exist",
    )

    capability = await runner.check_capability()

    assert capability.available is False
    assert capability.detail == "Docker CLI is unavailable."

import json
from collections.abc import Sequence
from pathlib import Path

from app.runners.docker_cli import CommandResult
from app.runners.docker_runner import DockerPythonRunner, DockerSandboxConfig
from app.tasks.registry import TaskRegistry


def _result(
    exit_code: int | None = 0,
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
        capability_results: Sequence[CommandResult] | None = None,
        image_result: CommandResult | None = None,
        oom_event: dict[str, object] | None = None,
    ) -> None:
        self.start_result = start_result or _result()
        self.state = state or {"ExitCode": 0, "OOMKilled": False}
        self.capability_available = capability_available
        self.write_report = write_report
        self.create_result = create_result or _result(stdout="container-id\n")
        self.capability_results = list(capability_results or [])
        self.image_result = image_result
        self.oom_event = oom_event
        self.commands: list[list[str]] = []
        self.report_path: Path | None = None
        self.container_name: str | None = None

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
            if self.capability_results:
                return self.capability_results.pop(0)
            return _result(stdout="29.0.0\n") if self.capability_available else _result(1)
        if command[:2] == ["image", "inspect"]:
            return self.image_result or _result(stdout="sha256:sandbox-image\n")
        if command[0] == "create":
            self.container_name = command[command.index("--name") + 1]
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
        if command[0] == "events":
            stdout = "" if self.oom_event is None else json.dumps(self.oom_event) + "\n"
            return _result(stdout=stdout)
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
        capability_retry_delay_seconds=0,
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
    assert log_options == ["max-size=1k", "max-file=1", "compress=false"]
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


async def test_runner_uses_exact_container_oom_event_when_inspect_is_false(
    correct_lru: str,
) -> None:
    client = FakeDockerClient(
        start_result=_result(137),
        state={"ExitCode": 137, "OOMKilled": False},
        write_report=False,
        oom_event={
            "Action": "oom",
            "Actor": {
                "ID": "container-id",
                "Attributes": {"name": "ignored-because-id-matches"},
            },
        },
    )

    result = await _runner(client).evaluate(TaskRegistry.default().get("lru-cache"), correct_lru)

    assert result.oom_killed is True
    assert result.exit_code == 137
    events = next(command for command in client.commands if command[0] == "events")
    assert "container=container-id" in events
    assert "event=oom" in events
    assert "--since" in events
    assert "--until" in events
    assert client.commands[-1][0:2] == ["rm", "--force"]


async def test_runner_does_not_treat_exit_137_without_oom_evidence_as_oom(
    correct_lru: str,
) -> None:
    client = FakeDockerClient(
        start_result=_result(137),
        state={"ExitCode": 137, "OOMKilled": False},
        write_report=False,
    )

    result = await _runner(client).evaluate(TaskRegistry.default().get("lru-cache"), correct_lru)

    assert result.exit_code == 137
    assert result.oom_killed is False
    assert result.sandbox_error == "Sandbox exited without a valid structured test report."
    assert any(command[0] == "events" for command in client.commands)
    assert client.commands[-1][0:2] == ["rm", "--force"]


async def test_runner_ignores_unrelated_oom_event(correct_lru: str) -> None:
    client = FakeDockerClient(
        start_result=_result(137),
        state={"ExitCode": 137, "OOMKilled": False},
        write_report=False,
        oom_event={
            "Action": "oom",
            "Actor": {
                "ID": "another-container-id",
                "Attributes": {"name": "another-container"},
            },
        },
    )

    result = await _runner(client).evaluate(TaskRegistry.default().get("lru-cache"), correct_lru)

    assert result.oom_killed is False
    assert result.sandbox_error == "Sandbox exited without a valid structured test report."


async def test_runner_reports_unavailable_daemon_without_local_fallback(
    correct_lru: str,
) -> None:
    client = FakeDockerClient(capability_available=False)

    result = await _runner(client).evaluate(TaskRegistry.default().get("lru-cache"), correct_lru)

    assert result.infrastructure_error == "Docker daemon is unavailable."
    assert not any(command[0] == "create" for command in client.commands)
    assert sum(command[:2] == ["info", "--format"] for command in client.commands) == 3


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
    assert capability.reason == "docker_cli_missing"


async def test_capability_retries_a_transient_daemon_failure() -> None:
    client = FakeDockerClient(capability_results=[_result(1), _result(stdout="29.0.0\n")])

    capability = await _runner(client).check_capability()

    assert capability.available is True
    assert sum(command[:2] == ["info", "--format"] for command in client.commands) == 2


async def test_capability_reports_timeout_after_bounded_retries() -> None:
    client = FakeDockerClient(capability_results=[_result(None, timed_out=True) for _ in range(3)])

    capability = await _runner(client).check_capability()

    assert capability.available is False
    assert capability.reason == "docker_probe_timeout"
    assert capability.detail == "Docker capability probe timed out."
    assert sum(command[:2] == ["info", "--format"] for command in client.commands) == 3


async def test_capability_reports_failed_probe_with_empty_success_output() -> None:
    client = FakeDockerClient(capability_results=[_result() for _ in range(3)])

    capability = await _runner(client).check_capability()

    assert capability.available is False
    assert capability.reason == "docker_probe_failed"
    assert sum(command[:2] == ["info", "--format"] for command in client.commands) == 3


async def test_capability_reports_missing_image_without_retrying_nontransient_failure() -> None:
    client = FakeDockerClient(image_result=_result(1))

    capability = await _runner(client).check_capability()

    assert capability.available is False
    assert capability.reason == "sandbox_image_missing"
    assert capability.detail == "Sandbox image 'codejudge-python-sandbox:phase2' is unavailable."
    assert sum(command[:2] == ["image", "inspect"] for command in client.commands) == 1
    assert sum(command[:2] == ["info", "--format"] for command in client.commands) == 2


async def test_capability_does_not_misreport_daemon_failure_as_missing_image() -> None:
    client = FakeDockerClient(
        capability_results=[
            _result(stdout="29.0.0\n"),
            _result(1),
            _result(1),
            _result(1),
        ],
        image_result=_result(1),
    )

    capability = await _runner(client).check_capability()

    assert capability.available is False
    assert capability.reason == "docker_daemon_unavailable"
    assert capability.detail == "Docker daemon is unavailable."
    assert sum(command[:2] == ["image", "inspect"] for command in client.commands) == 1
    assert sum(command[:2] == ["info", "--format"] for command in client.commands) == 4

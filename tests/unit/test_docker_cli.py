import sys

from app.runners.docker_cli import DockerCli


async def test_cli_capture_is_bounded_across_stdout_and_stderr() -> None:
    result = await DockerCli(sys.executable).run(
        ["-c", "import sys; sys.stdout.write('a' * 100); sys.stderr.write('b' * 100)"],
        timeout_seconds=1,
        output_limit_bytes=64,
    )

    assert result.exit_code == 0
    assert len(result.stdout.encode()) + len(result.stderr.encode()) == 64
    assert result.output_truncated is True


async def test_cli_enforces_timeout() -> None:
    result = await DockerCli(sys.executable).run(
        ["-c", "while True: pass"],
        timeout_seconds=0.1,
        output_limit_bytes=64,
    )

    assert result.timed_out is True
    assert result.exit_code is not None

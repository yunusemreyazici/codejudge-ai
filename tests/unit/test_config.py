import pytest

from app.core.config import ExecutionBackend, Settings


def test_settings_default_to_docker_backend() -> None:
    assert Settings().execution_backend is ExecutionBackend.DOCKER


def test_settings_load_typed_sandbox_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_BACKEND", "local")
    monkeypatch.setenv("SANDBOX_IMAGE", "example/sandbox:test")
    monkeypatch.setenv("SANDBOX_MEMORY_MB", "128")
    monkeypatch.setenv("SANDBOX_CPUS", "0.25")
    monkeypatch.setenv("SANDBOX_PIDS_LIMIT", "32")
    monkeypatch.setenv("SANDBOX_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("SANDBOX_OUTPUT_LIMIT_BYTES", "4096")

    settings = Settings.from_env()

    assert settings.execution_backend is ExecutionBackend.LOCAL
    assert settings.sandbox_image == "example/sandbox:test"
    assert settings.sandbox_memory_mb == 128
    assert settings.sandbox_cpus == 0.25
    assert settings.sandbox_pids_limit == 32
    assert settings.sandbox_timeout_seconds == 2.5
    assert settings.sandbox_output_limit_bytes == 4096


def test_settings_reject_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_BACKEND", "automatic")

    with pytest.raises(ValueError, match="automatic"):
        Settings.from_env()

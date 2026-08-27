import pytest

from app.core.config import EvaluationMode, ExecutionBackend, Settings


def test_settings_default_to_docker_backend() -> None:
    assert Settings().execution_backend is ExecutionBackend.DOCKER
    assert Settings().static_analysis_enabled is True
    assert Settings().persistence_enabled is False
    assert Settings().evaluation_mode is EvaluationMode.SYNC


def test_settings_load_typed_sandbox_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_BACKEND", "local")
    monkeypatch.setenv("SANDBOX_IMAGE", "example/sandbox:test")
    monkeypatch.setenv("SANDBOX_MEMORY_MB", "128")
    monkeypatch.setenv("SANDBOX_CPUS", "0.25")
    monkeypatch.setenv("SANDBOX_PIDS_LIMIT", "32")
    monkeypatch.setenv("SANDBOX_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("SANDBOX_OUTPUT_LIMIT_BYTES", "4096")
    monkeypatch.setenv("STATIC_ANALYSIS_ENABLED", "false")
    monkeypatch.setenv("STATIC_ANALYSIS_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("STATIC_ANALYSIS_OUTPUT_LIMIT_BYTES", "8192")
    monkeypatch.setenv("PERSISTENCE_ENABLED", "true")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://codejudge:secret@localhost:5432/codejudge_test",
    )

    settings = Settings.from_env()

    assert settings.execution_backend is ExecutionBackend.LOCAL
    assert settings.sandbox_image == "example/sandbox:test"
    assert settings.sandbox_memory_mb == 128
    assert settings.sandbox_cpus == 0.25
    assert settings.sandbox_pids_limit == 32
    assert settings.sandbox_timeout_seconds == 2.5
    assert settings.sandbox_output_limit_bytes == 4096
    assert settings.static_analysis_enabled is False
    assert settings.static_analysis_timeout_seconds == 3.5
    assert settings.static_analysis_output_limit_bytes == 8192
    assert settings.persistence_enabled is True
    assert settings.database_url is not None


def test_settings_reject_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXECUTION_BACKEND", "automatic")

    with pytest.raises(ValueError, match="automatic"):
        Settings.from_env()


def test_settings_reject_invalid_static_analysis_boolean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STATIC_ANALYSIS_ENABLED", "sometimes")

    with pytest.raises(ValueError, match="STATIC_ANALYSIS_ENABLED"):
        Settings.from_env()


def test_settings_require_postgresql_url_when_persistence_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSISTENCE_ENABLED", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="DATABASE_URL is required"):
        Settings.from_env()


def test_settings_reject_sqlite_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///codejudge.db")

    with pytest.raises(ValueError, match=r"postgresql\+asyncpg"):
        Settings.from_env()


def test_async_settings_load_worker_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PERSISTENCE_ENABLED", "true")
    monkeypatch.setenv("EVALUATION_MODE", "async")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://codejudge:secret@localhost/codejudge_test"
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/3")
    monkeypatch.setenv("WORKER_CONCURRENCY", "2")
    monkeypatch.setenv("WORKER_LEASE_SECONDS", "90")
    monkeypatch.setenv("WORKER_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("OUTBOX_POLL_INTERVAL_SECONDS", "0.25")
    monkeypatch.setenv("RETRY_BASE_DELAY_SECONDS", "2")

    settings = Settings.from_env()

    assert settings.evaluation_mode is EvaluationMode.ASYNC
    assert settings.redis_url == "redis://localhost:6379/3"
    assert settings.worker_concurrency == 2
    assert settings.worker_lease_seconds == 90
    assert settings.worker_max_attempts == 4
    assert settings.outbox_poll_interval_seconds == 0.25
    assert settings.retry_base_delay_seconds == 2


def test_async_mode_requires_persistence_and_redis() -> None:
    with pytest.raises(ValueError, match="PERSISTENCE_ENABLED"):
        Settings(evaluation_mode=EvaluationMode.ASYNC, redis_url="redis://localhost")

    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings(
            evaluation_mode=EvaluationMode.ASYNC,
            persistence_enabled=True,
            database_url="postgresql+asyncpg://codejudge:secret@localhost/codejudge_test",
        )

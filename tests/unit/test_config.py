import pytest

from app.core.config import EvaluationMode, ExecutionBackend, Settings


def test_settings_default_to_docker_backend() -> None:
    assert Settings().execution_backend is ExecutionBackend.DOCKER
    assert Settings().static_analysis_enabled is True
    assert Settings().persistence_enabled is False
    assert Settings().evaluation_mode is EvaluationMode.SYNC
    assert Settings().llm_enabled is False
    assert Settings().benchmark_enabled is False
    assert Settings().max_benchmark_models == 20
    assert Settings().max_benchmark_total_generations == 500


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
    with pytest.raises(ValueError, match="BENCHMARK_CONFIG"):
        Settings(
            benchmark_enabled=True,
            persistence_enabled=True,
            database_url="postgresql+asyncpg://codejudge:secret@localhost/codejudge_test",
            redis_url="redis://localhost:6379/3",
        )


def test_llm_settings_are_typed_and_credentials_are_not_part_of_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("PERSISTENCE_ENABLED", "true")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://codejudge:secret@localhost/codejudge_test"
    )
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.invalid/v1/")
    monkeypatch.setenv("LLM_API_KEY", "secret")
    monkeypatch.setenv("LLM_PROVIDER_ID", "gateway")
    monkeypatch.setenv("LLM_JUDGE_MODELS", "judge-a, judge-b")
    monkeypatch.setenv("LLM_ADVERSARIAL_MODEL", "generator-a")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "500")
    monkeypatch.setenv("LLM_MAX_INPUT_BYTES", "9000")
    monkeypatch.setenv("LLM_MAX_ADVERSARIAL_TESTS", "3")

    settings = Settings.from_env()
    assert settings.llm_enabled
    assert settings.llm_base_url == "https://provider.invalid/v1"
    assert settings.llm_judge_models == ("judge-a", "judge-b")
    assert settings.llm_timeout_seconds == 12
    assert settings.llm_max_input_bytes == 9000
    assert settings.llm_max_adversarial_tests == 3


def test_llm_enabled_requires_provider_configuration() -> None:
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        Settings(
            persistence_enabled=True,
            database_url="postgresql+asyncpg://codejudge:secret@localhost/codejudge_test",
            llm_enabled=True,
        )


def test_llm_enabled_requires_persistence() -> None:
    with pytest.raises(ValueError, match="PERSISTENCE_ENABLED"):
        Settings(
            llm_enabled=True,
            llm_base_url="https://provider.invalid/v1",
            llm_api_key="secret",
            llm_judge_models=("judge-a",),
            llm_adversarial_model="generator-a",
        )


def test_benchmark_settings_use_separate_provider_and_bounded_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BENCHMARK_ENABLED", "true")
    monkeypatch.setenv("PERSISTENCE_ENABLED", "true")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://codejudge:secret@localhost/codejudge_test"
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/3")
    monkeypatch.setenv("BENCHMARK_BASE_URL", "https://coding.invalid/v1/")
    monkeypatch.setenv("BENCHMARK_API_KEY", "generation-secret")
    monkeypatch.setenv("BENCHMARK_PROVIDER_ID", "coding-gateway")
    monkeypatch.setenv("BENCHMARK_GENERATION_CONCURRENCY", "2")
    monkeypatch.setenv("MAX_BENCHMARK_TOTAL_GENERATIONS", "40")

    settings = Settings.from_env()

    assert settings.benchmark_enabled is True
    assert settings.benchmark_base_url == "https://coding.invalid/v1"
    assert settings.benchmark_provider_id == "coding-gateway"
    assert settings.benchmark_generation_concurrency == 2
    assert settings.max_benchmark_total_generations == 40
    assert settings.llm_enabled is False


def test_benchmark_enabled_requires_database_redis_and_provider() -> None:
    with pytest.raises(ValueError, match="PostgreSQL"):
        Settings(benchmark_enabled=True)
    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings(
            benchmark_enabled=True,
            persistence_enabled=True,
            database_url="postgresql+asyncpg://codejudge:secret@localhost/codejudge_test",
        )


def test_benchmark_config_replaces_legacy_single_provider_settings() -> None:
    settings = Settings(
        benchmark_enabled=True,
        benchmark_config_path="benchmark-configs/real-smoke.yaml",
        persistence_enabled=True,
        database_url="postgresql+asyncpg://codejudge:secret@localhost/codejudge_test",
        redis_url="redis://localhost:6379/3",
    )

    assert settings.benchmark_config_path == "benchmark-configs/real-smoke.yaml"
    assert settings.benchmark_base_url is None
    assert settings.benchmark_api_key is None

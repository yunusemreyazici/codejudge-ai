from decimal import Decimal
from pathlib import Path

import pytest

from app.benchmarks.run_config import (
    BenchmarkConfigError,
    BenchmarkRunConfig,
    build_plan,
    load_benchmark_config,
    resolved_provider_values,
    validate_run_preflight,
)

EXAMPLE = Path("benchmark-configs/real-smoke.example.yaml")
REPEATED_EXAMPLE = Path("benchmark-configs/repeated-example.yaml")


def test_example_config_plans_fourteen_generations_without_provider_calls() -> None:
    config = load_benchmark_config(EXAMPLE)

    plan = build_plan(config, environment={})

    assert plan.dataset_id == "codejudge-core"
    assert plan.dataset_version == "2"
    assert plan.task_count == 7
    assert plan.model_count == 2
    assert plan.planned_generations == 14
    assert plan.ai_evaluation_enabled is False
    assert plan.estimated_maximum_costs == {"USD": Decimal("0.250292000000")}
    assert plan.unknown_pricing == ()
    assert "estimate:" in plan.estimate_basis
    assert len(plan.warnings) == 4
    assert {model.output_mode.value for model in plan.models} == {"structured_json"}
    assert {model.request_timeout_seconds for model in plan.models} == {30}
    assert {model.max_concurrent_requests for model in plan.models} == {None}


def test_unknown_pricing_is_not_zero_and_prevents_budget_enforcement() -> None:
    config = load_benchmark_config(EXAMPLE)
    config = config.model_copy(update={"pricing": {}})

    with pytest.raises(BenchmarkConfigError, match="pricing is unknown"):
        build_plan(config, environment={})

    without_budget = config.model_copy(update={"max_generation_cost": None})
    plan = build_plan(without_budget, environment={})
    assert plan.estimated_maximum_costs == {}
    assert plan.unknown_pricing == ("provider-a/model-a", "provider-b/model-b")


def test_known_estimate_over_budget_refuses_before_run() -> None:
    config = load_benchmark_config(EXAMPLE)
    assert config.max_generation_cost is not None
    budget = config.max_generation_cost.model_copy(update={"amount": Decimal("0.01")})

    with pytest.raises(BenchmarkConfigError, match="exceeds budget"):
        build_plan(config.model_copy(update={"max_generation_cost": budget}), environment={})


def test_three_repeated_samples_plan_42_generations_and_multiply_budget_estimates() -> None:
    single = load_benchmark_config(EXAMPLE)
    repeated = single.model_copy(update={"samples_per_task": 3})
    single_plan = build_plan(single, environment={})
    repeated_plan = build_plan(repeated, environment={})

    assert repeated_plan.model_count == 2
    assert repeated_plan.task_count == 7
    assert repeated_plan.samples_per_task == 3
    assert repeated_plan.planned_generations == 42
    assert [model.planned_generations for model in repeated_plan.models] == [21, 21]
    assert repeated_plan.estimated_maximum_costs == {
        currency: amount * 3 for currency, amount in single_plan.estimated_maximum_costs.items()
    }
    for repeated_model, single_model in zip(repeated_plan.models, single_plan.models, strict=True):
        assert repeated_model.estimated_maximum_cost == single_model.estimated_maximum_cost * 3  # type: ignore[operator]

    assert repeated.max_generation_cost is not None
    refused_budget = repeated.max_generation_cost.model_copy(
        update={"amount": single_plan.estimated_maximum_costs["USD"]}
    )
    with pytest.raises(BenchmarkConfigError, match="exceeds budget"):
        build_plan(
            repeated.model_copy(update={"max_generation_cost": refused_budget}), environment={}
        )


def test_repeated_example_is_provider_free_and_plans_42_generations() -> None:
    config = load_benchmark_config(REPEATED_EXAMPLE)

    plan = build_plan(config, environment={})

    assert config.samples_per_task == 3
    assert plan.planned_generations == 42
    assert all(not model.credential_configured for model in plan.models)
    assert all(not model.endpoint_configured for model in plan.models)


def test_run_preflight_requires_credentials_and_matching_ai_policy() -> None:
    config = load_benchmark_config(EXAMPLE)
    missing_plan = build_plan(config, environment={})
    with pytest.raises(BenchmarkConfigError, match="endpoint or credential is missing"):
        validate_run_preflight(config, missing_plan, ai_enabled=False)

    environment = {
        "CODEJUDGE_PROVIDER_A_BASE_URL": "https://a.invalid/v1",
        "CODEJUDGE_PROVIDER_A_API_KEY": "secret-a",
        "CODEJUDGE_PROVIDER_B_BASE_URL": "https://b.invalid/v1",
        "CODEJUDGE_PROVIDER_B_API_KEY": "secret-b",
    }
    plan = build_plan(config, environment=environment)
    validate_run_preflight(config, plan, ai_enabled=False)
    with pytest.raises(BenchmarkConfigError, match="runtime settings differ"):
        validate_run_preflight(config, plan, ai_enabled=True)

    assert resolved_provider_values(config, environment) == {
        "provider-a": ("https://a.invalid/v1", "secret-a", 30, None),
        "provider-b": ("https://b.invalid/v1", "secret-b", 30, None),
    }


def test_provider_transport_capability_resolves_into_model_identity() -> None:
    payload = load_benchmark_config(EXAMPLE).model_dump(mode="python")
    payload["providers"]["provider-a"]["output_mode"] = "raw_source"
    payload["providers"]["provider-a"]["request_timeout_seconds"] = 120
    payload["providers"]["provider-a"]["max_concurrent_requests"] = 1
    config = BenchmarkRunConfig.model_validate(payload)

    models = config.resolved_models()

    assert models[0].output_mode.value == "raw_source"
    assert models[0].request_timeout_seconds == 120
    assert models[0].max_concurrent_requests == 1
    assert models[1].output_mode.value == "structured_json"
    assert "Mixed generation output modes" in " ".join(build_plan(config, environment={}).warnings)


def test_config_rejects_unregistered_and_duplicate_model_identities() -> None:
    payload = load_benchmark_config(EXAMPLE).model_dump(mode="python")
    payload["models"][0]["provider_id"] = "missing"
    with pytest.raises(ValueError, match="unregistered providers"):
        BenchmarkRunConfig.model_validate(payload)

    payload = load_benchmark_config(EXAMPLE).model_dump(mode="python")
    payload["models"] = (payload["models"][0], payload["models"][0])
    with pytest.raises(ValueError, match="duplicate model"):
        BenchmarkRunConfig.model_validate(payload)


def test_config_loader_rejects_secrets_and_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.yaml"
    path.write_text(
        EXAMPLE.read_text(encoding="utf-8") + "\napi_key: should-never-be-accepted\n",
        encoding="utf-8",
    )

    with pytest.raises(BenchmarkConfigError, match="Extra inputs are not permitted"):
        load_benchmark_config(path)


@pytest.mark.parametrize("timeout", [0, 601])
def test_provider_request_timeout_is_bounded(timeout: int) -> None:
    payload = load_benchmark_config(EXAMPLE).model_dump(mode="python")
    payload["providers"]["provider-a"]["request_timeout_seconds"] = timeout

    with pytest.raises(ValueError, match="request_timeout_seconds"):
        BenchmarkRunConfig.model_validate(payload)


@pytest.mark.parametrize("concurrency", [0, 101])
def test_provider_request_concurrency_is_bounded(concurrency: int) -> None:
    payload = load_benchmark_config(EXAMPLE).model_dump(mode="python")
    payload["providers"]["provider-a"]["max_concurrent_requests"] = concurrency

    with pytest.raises(ValueError, match="max_concurrent_requests"):
        BenchmarkRunConfig.model_validate(payload)

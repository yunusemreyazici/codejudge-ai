from typing import Any

from httpx import ASGITransport, AsyncClient

from app.core.config import ExecutionBackend, Settings
from app.evaluator.models import RunnerCapability, RunnerResult
from app.main import create_app
from app.tasks.registry import RegisteredTask


class UnavailableRunner:
    async def evaluate(self, task: RegisteredTask, code: str) -> RunnerResult:
        return RunnerResult(
            exit_code=None,
            stdout="",
            stderr="",
            duration_seconds=0.01,
            passed=0,
            failed=0,
            total=0,
            infrastructure_error="Docker daemon is unavailable.",
        )

    async def check_capability(self) -> RunnerCapability:
        return RunnerCapability(
            backend="docker",
            available=False,
            detail="Docker daemon is unavailable.",
        )


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_sandbox_health_reports_configured_backend(client: AsyncClient) -> None:
    response = await client.get("/health/sandbox")

    assert response.status_code == 200
    assert response.json() == {
        "backend": "local",
        "available": True,
        "detail": "Local execution is available but is not isolated.",
    }


async def test_list_tasks_does_not_expose_tests(client: AsyncClient) -> None:
    response = await client.get("/api/v1/tasks")

    assert response.status_code == 200
    body = response.json()
    assert {task["id"] for task in body} == {
        "async-batch-processor",
        "circuit-breaker",
        "dependency-resolver",
        "lru-cache",
        "rate-limiter",
        "retry-backoff",
        "ttl-cache",
    }
    for task in body:
        assert "tests" not in task
        assert "tests_path" not in task
        assert "reference" not in task


async def test_get_task(client: AsyncClient) -> None:
    response = await client.get("/api/v1/tasks/lru-cache")

    assert response.status_code == 200
    assert response.json()["entrypoint"] == "solution:LRUCache"


async def test_successful_lru_evaluation(client: AsyncClient, correct_lru: str) -> None:
    response = await client.post(
        "/api/v1/evaluations",
        json={"task_id": "lru-cache", "language": "python", "code": correct_lru},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["score"] == 100.0
    assert body["tests"]["passed"] == 8
    assert body["score_breakdown"] == {
        "correctness": 100.0,
        "code_quality": 100.0,
        "type_safety": 100.0,
        "security": 100.0,
        "complexity": 100.0,
    }
    assert body["analysis"]["findings"] == []
    assert body["analysis"]["complexity"]["maximum"] <= 5
    assert body["findings"] == []


async def test_failing_lru_evaluation(client: AsyncClient, incorrect_lru: str) -> None:
    response = await client.post(
        "/api/v1/evaluations",
        json={"task_id": "lru-cache", "language": "python", "code": incorrect_lru},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert 0 < body["score"] < 100
    assert body["tests"]["failed"] > 0
    assert body["findings"][0]["category"] == "testing"


async def test_lru_static_analysis_variants_are_deterministic(
    client: AsyncClient,
    incorrect_lru: str,
    poor_quality_lru: str,
    security_smelly_lru: str,
    type_incorrect_lru: str,
    high_complexity_lru: str,
) -> None:
    sources = {
        "incorrect": incorrect_lru,
        "quality": poor_quality_lru,
        "security": security_smelly_lru,
        "typing": type_incorrect_lru,
        "complexity": high_complexity_lru,
    }
    results: dict[str, Any] = {}
    for name, source in sources.items():
        response = await client.post(
            "/api/v1/evaluations",
            json={"task_id": "lru-cache", "language": "python", "code": source},
        )
        assert response.status_code == 200
        results[name] = response.json()

    assert results["incorrect"]["score_breakdown"]["correctness"] < 100
    for name in ("quality", "security", "typing", "complexity"):
        assert results[name]["score_breakdown"]["correctness"] == 100
        assert results[name]["score"] > results["incorrect"]["score"]
    assert results["quality"]["score_breakdown"]["code_quality"] < 100
    assert results["security"]["score_breakdown"]["security"] < 100
    assert results["typing"]["score_breakdown"]["type_safety"] < 100
    assert results["complexity"]["score_breakdown"]["complexity"] == 70


async def test_unknown_task(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/evaluations",
        json={"task_id": "unknown", "language": "python", "code": "pass"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Unknown task: unknown"}


async def test_unknown_task_metadata(client: AsyncClient) -> None:
    response = await client.get("/api/v1/tasks/unknown")

    assert response.status_code == 404


async def test_unsupported_language(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/evaluations",
        json={"task_id": "lru-cache", "language": "javascript", "code": "class X {}"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Unsupported language: javascript"}


async def test_invalid_input(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/evaluations",
        json={"task_id": "lru-cache", "language": "python", "code": "   "},
    )

    assert response.status_code == 422


async def test_syntax_error_is_a_structured_evaluation(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/evaluations",
        json={"task_id": "lru-cache", "language": "python", "code": "class LRUCache(\n"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["score_breakdown"]["correctness"] == 0
    assert body["score"] < 40
    assert body["findings"][0] == {
        "severity": "error",
        "category": "execution",
        "message": "Candidate code contains a syntax error.",
    }


async def test_oversized_source_returns_useful_validation_error(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/evaluations",
        json={"task_id": "lru-cache", "language": "python", "code": "x" * (100 * 1024 + 1)},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "Code is 102401 bytes; maximum is 102400 bytes"}


async def test_unavailable_execution_backend_returns_503() -> None:
    settings = Settings(
        log_level="CRITICAL",
        execution_backend=ExecutionBackend.DOCKER,
    )
    application = create_app(settings=settings, python_runner=UnavailableRunner())
    transport = ASGITransport(app=application)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/evaluations",
            json={"task_id": "lru-cache", "language": "python", "code": "pass"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Docker daemon is unavailable."}

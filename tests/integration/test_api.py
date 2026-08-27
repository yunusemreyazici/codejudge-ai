from httpx import AsyncClient


async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_list_tasks_does_not_expose_tests(client: AsyncClient) -> None:
    response = await client.get("/api/v1/tasks")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == "lru-cache"
    assert "tests" not in body[0]
    assert "tests_path" not in body[0]


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
    assert body["score"] == 0
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

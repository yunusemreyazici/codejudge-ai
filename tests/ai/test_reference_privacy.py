from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.core.config import ExecutionBackend, Settings
from app.main import create_app
from app.tasks.registry import TaskRegistry


async def test_reference_solution_is_packaged_but_absent_from_public_task_api() -> None:
    registry = TaskRegistry.default()
    task = registry.get("lru-cache")
    assert task.reference_path is not None and task.reference_path.is_file()
    reference_source = task.reference_path.read_text(encoding="utf-8")

    application = create_app(
        settings=Settings(execution_backend=ExecutionBackend.LOCAL),
        registry=registry,
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/tasks/lru-cache")
    assert response.status_code == 200
    assert "reference" not in response.json()
    assert reference_source not in response.text

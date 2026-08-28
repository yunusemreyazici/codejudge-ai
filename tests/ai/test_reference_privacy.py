from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.core.config import ExecutionBackend, Settings
from app.main import create_app
from app.tasks.registry import TaskRegistry


async def test_all_reference_solutions_are_absent_from_public_task_api() -> None:
    registry = TaskRegistry.default()
    application = create_app(
        settings=Settings(execution_backend=ExecutionBackend.LOCAL),
        registry=registry,
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        listing = await client.get("/api/v1/tasks")
        assert listing.status_code == 200
        for task in registry:
            assert task.reference_path is not None and task.reference_path.is_file()
            reference_source = task.reference_path.read_text(encoding="utf-8")
            response = await client.get(f"/api/v1/tasks/{task.specification.id}")
            assert response.status_code == 200
            assert "reference" not in response.json()
            assert "tests" not in response.json()
            assert reference_source not in response.text
            assert reference_source not in listing.text

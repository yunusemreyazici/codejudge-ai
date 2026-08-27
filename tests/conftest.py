from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import ExecutionBackend, Settings
from app.main import create_app

CORRECT_LRU = """
from collections import OrderedDict


class LRUCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.values = OrderedDict()

    def get(self, key: int) -> int:
        if key not in self.values:
            return -1
        self.values.move_to_end(key)
        return self.values[key]

    def put(self, key: int, value: int) -> None:
        if key in self.values:
            self.values.move_to_end(key)
        self.values[key] = value
        if len(self.values) > self.capacity:
            self.values.popitem(last=False)
""".lstrip()

INCORRECT_LRU = """
class LRUCache:
    def __init__(self, capacity: int):
        self.values = {}

    def get(self, key: int) -> int:
        return self.values.get(key, -1)

    def put(self, key: int, value: int) -> None:
        self.values[key] = value
""".lstrip()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    settings = Settings(
        log_level="CRITICAL",
        max_code_size=100 * 1024,
        execution_backend=ExecutionBackend.LOCAL,
    )
    transport = ASGITransport(app=create_app(settings=settings))
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.fixture
def correct_lru() -> str:
    return CORRECT_LRU


@pytest.fixture
def incorrect_lru() -> str:
    return INCORRECT_LRU

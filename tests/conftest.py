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
        self.values: OrderedDict[int, int] = OrderedDict()

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

POOR_QUALITY_LRU = (
    CORRECT_LRU
    + """

def _quality_smell():
    unused = 1
    return 2
"""
)

SECURITY_SMELLY_LRU = (
    CORRECT_LRU
    + """

def _unsafe(expression: str) -> object:
    return eval(expression)
"""
)

TYPE_INCORRECT_LRU = (
    CORRECT_LRU
    + """

def _type_error() -> int:
    value: int = "wrong"
    return value
"""
)

HIGH_COMPLEXITY_LRU = (
    CORRECT_LRU
    + "\ndef _complex(value: int) -> int:\n"
    + "".join(f"    if value == {index}:\n        return {index}\n" for index in range(11))
    + "    return -1\n"
)


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


@pytest.fixture
def poor_quality_lru() -> str:
    return POOR_QUALITY_LRU


@pytest.fixture
def security_smelly_lru() -> str:
    return SECURITY_SMELLY_LRU


@pytest.fixture
def type_incorrect_lru() -> str:
    return TYPE_INCORRECT_LRU


@pytest.fixture
def high_complexity_lru() -> str:
    return HIGH_COMPLEXITY_LRU

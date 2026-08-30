import pytest
from solution import normalize_path


def test_absolute_path_normalization() -> None:
    assert normalize_path("//api/./v1/../v2//") == "/api/v2"
    assert normalize_path("/") == "/"


def test_relative_path_uses_normalized_cwd() -> None:
    assert normalize_path("../logs/./today", "/srv/app/") == "/srv/logs/today"
    assert normalize_path("child", "/a//b/../c") == "/a/c/child"


def test_absolute_path_ignores_cwd_components() -> None:
    assert normalize_path("/safe", "/ignored/../cwd") == "/safe"


@pytest.mark.parametrize("path,cwd", [("", "/"), ("a", ""), ("a", "relative")])
def test_empty_or_relative_contract_values_are_rejected(path: str, cwd: str) -> None:
    with pytest.raises(ValueError):
        normalize_path(path, cwd)

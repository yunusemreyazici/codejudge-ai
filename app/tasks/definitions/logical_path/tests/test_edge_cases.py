import pytest
from solution import normalize_path


def test_exact_root_boundary_is_allowed() -> None:
    assert normalize_path("..", "/a") == "/"
    assert normalize_path("a/..", "/") == "/"


def test_traversal_above_root_is_rejected_for_path_and_cwd() -> None:
    with pytest.raises(ValueError, match="above root"):
        normalize_path("../x", "/")
    with pytest.raises(ValueError, match="above root"):
        normalize_path("x", "/../bad")


def test_backslash_is_an_ordinary_character() -> None:
    assert normalize_path(r"a\b/../c", "/root") == "/root/c"
    assert normalize_path(r"a\b", "/root") == r"/root/a\b"


def test_absolute_path_replaces_cwd_even_when_it_contains_parent_tokens() -> None:
    assert normalize_path("/x/../y", "/base") == "/y"

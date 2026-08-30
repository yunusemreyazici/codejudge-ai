import pytest
from solution import merge_config_layers


@pytest.mark.parametrize("layers", [None, {}, ()])
def test_outer_value_must_be_a_list(layers: object) -> None:
    with pytest.raises(TypeError):
        merge_config_layers(layers)


def test_each_layer_must_be_a_mapping() -> None:
    with pytest.raises(TypeError):
        merge_config_layers([{"valid": 1}, []])


@pytest.mark.parametrize(
    "layers",
    [[{1: "bad"}], [{"nested": {2: "bad"}}], [{"items": [{3: "bad"}]}]],
)
def test_string_keys_are_required_at_every_depth(layers: object) -> None:
    with pytest.raises(TypeError):
        merge_config_layers(layers)


def test_nested_deletion_does_not_remove_parent() -> None:
    assert merge_config_layers([{"a": {"x": 1, "y": 2}}, {"a": {"x": None}}]) == {"a": {"y": 2}}

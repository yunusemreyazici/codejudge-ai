import copy

from solution import merge_config_layers


def test_recursive_merge_replacement_and_deletion() -> None:
    layers = [
        {"service": {"host": "a", "port": 80}, "debug": False, "obsolete": 1},
        {"service": {"port": 443, "tls": True}, "debug": True, "obsolete": None},
    ]
    assert merge_config_layers(layers) == {
        "service": {"host": "a", "port": 443, "tls": True},
        "debug": True,
    }


def test_empty_layers_and_missing_deletion() -> None:
    assert merge_config_layers([]) == {}
    assert merge_config_layers([{"missing": None}]) == {}


def test_mapping_and_scalar_replace_each_other() -> None:
    assert merge_config_layers([{"a": 1}, {"a": {"b": 2}}]) == {"a": {"b": 2}}
    assert merge_config_layers([{"a": {"b": 2}}, {"a": [3]}]) == {"a": [3]}


def test_inputs_and_nested_mutables_are_not_shared() -> None:
    layers = [{"a": {"items": [1, {"x": 2}]}}]
    original = copy.deepcopy(layers)
    result = merge_config_layers(layers)
    result["a"]["items"].append(3)
    result["a"]["items"][1]["x"] = 9
    assert layers == original

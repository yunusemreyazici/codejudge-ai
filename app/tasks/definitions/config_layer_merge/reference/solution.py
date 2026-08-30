"""Trusted recursive configuration merge oracle."""

from copy import deepcopy


def merge_config_layers(layers: list[dict[str, object]]) -> dict[str, object]:
    if not isinstance(layers, list):
        raise TypeError("layers must be a list")
    for layer in layers:
        if not isinstance(layer, dict):
            raise TypeError("every layer must be a dictionary")
        _validate_keys(layer)
    result: dict[str, object] = {}
    for layer in layers:
        _merge(result, layer)
    return result


def _validate_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise TypeError("configuration keys must be strings")
            _validate_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _validate_keys(nested)


def _merge(target: dict[str, object], layer: dict[str, object]) -> None:
    for key, value in layer.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict):
            existing = target.get(key)
            nested = deepcopy(existing) if isinstance(existing, dict) else {}
            _merge(nested, value)
            target[key] = nested
        else:
            target[key] = deepcopy(value)

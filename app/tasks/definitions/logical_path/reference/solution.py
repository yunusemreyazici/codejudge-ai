"""Trusted lexical POSIX path normalizer oracle."""


def normalize_path(path: str, cwd: str = "/") -> str:
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a nonempty string")
    if not isinstance(cwd, str) or not cwd or not cwd.startswith("/"):
        raise ValueError("cwd must be a nonempty absolute string")
    base = [] if path.startswith("/") else _components(cwd, [])
    resolved = _components(path, base)
    return "/" + "/".join(resolved)


def _components(value: str, initial: list[str]) -> list[str]:
    result = initial.copy()
    for component in value.split("/"):
        if not component or component == ".":
            continue
        if component == "..":
            if not result:
                raise ValueError("path traverses above root")
            result.pop()
        else:
            result.append(component)
    return result

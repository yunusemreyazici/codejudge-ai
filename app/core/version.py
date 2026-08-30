"""Central application version metadata."""

from importlib.metadata import PackageNotFoundError, version

_FALLBACK_VERSION = "0.7.11"


def codejudge_version() -> str:
    try:
        return version("codejudge-ai")
    except PackageNotFoundError:
        return _FALLBACK_VERSION

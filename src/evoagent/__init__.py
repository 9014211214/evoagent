from importlib.metadata import PackageNotFoundError, version

from .domain.models import *

try:
    __version__ = version("auto-evolving-agent")
except PackageNotFoundError:  # pragma: no cover - only when imported outside an install
    __version__ = "0+unknown"

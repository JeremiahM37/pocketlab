"""pocketlab — a mobile-first PWA dashboard for self-hosted servers."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth is pyproject.toml's [project].version.
    __version__ = version("pocketlab")
except PackageNotFoundError:  # source checkout that was never pip-installed
    __version__ = "0.0.0+unknown"

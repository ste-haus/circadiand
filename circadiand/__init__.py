"""circadiand — REST service for powering hosts on/off."""

from importlib import metadata
from pathlib import Path

PACKAGE_NAME = "circadiand"


def _read_version() -> str:
    """Resolve the version from installed metadata, falling back to the VERSION
    file at the repo root (for running from a source checkout)."""
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        version_file = Path(__file__).resolve().parent.parent / "VERSION"
        if version_file.is_file():
            return version_file.read_text().strip()
        return "0.0.0"


__version__ = _read_version()

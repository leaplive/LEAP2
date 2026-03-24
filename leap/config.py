"""Project root resolution and configuration constants."""

from __future__ import annotations

import os
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, _LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


_PROJECT_ROOT_TYPES = ("lab",)


def parse_frontmatter_text(text: str, defaults: dict | None = None) -> dict:
    """Parse YAML frontmatter from a text string. Returns defaults if no valid frontmatter."""
    result = dict(defaults) if defaults else {}
    if not text.startswith("---"):
        return result
    end = text.find("---", 3)
    if end == -1:
        return result
    try:
        fm = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return result
    result.update(fm)
    return result


def _is_lab_root(path: Path) -> bool:
    """Check if a directory is a LEAP project root (has README.md with type: lab in frontmatter)."""
    readme = path / "README.md"
    if not readme.is_file():
        return False
    try:
        text = readme.read_text(encoding="utf-8")
        fm = parse_frontmatter_text(text)
        return fm.get("type") in _PROJECT_ROOT_TYPES
    except Exception:
        return False


def get_root() -> Path:
    """Resolve project root: LEAP_ROOT env > cwd (lab README or experiments/) > parent of leap package.

    Also sets get_root.reason to explain how the root was resolved.
    """
    if env := os.environ.get("LEAP_ROOT"):
        get_root.reason = "LEAP_ROOT environment variable"
        return Path(env).resolve()
    cwd = Path.cwd()
    if _is_lab_root(cwd):
        get_root.reason = "cwd has README.md with type: lab"
        return cwd
    if (cwd / "experiments").is_dir():
        get_root.reason = "cwd has experiments/ directory"
        return cwd
    get_root.reason = "fallback to cwd (no lab detected)"
    return cwd

get_root.reason = ""


def experiments_dir(root: Path | None = None) -> Path:
    return (root or get_root()) / "experiments"


def config_dir(root: Path | None = None) -> Path:
    return (root or get_root()) / "config"


def credentials_path(root: Path | None = None) -> Path:
    return config_dir(root) / "admin_credentials.json"


def package_ui_dir() -> Path:
    """Return the path to UI files bundled with the leap package."""
    return Path(__file__).resolve().parent / "ui"


def ui_dir(root: Path | None = None) -> Path:
    project_ui = (root or get_root()) / "ui"
    if project_ui.is_dir():
        return project_ui
    return package_ui_dir()


SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "")
DEFAULT_EXPERIMENT = os.environ.get("DEFAULT_EXPERIMENT", "")
ADMIN_PASSWORD_ENV = os.environ.get("ADMIN_PASSWORD", "")

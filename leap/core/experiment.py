"""Experiment discovery, README frontmatter parsing, and function loading."""

from __future__ import annotations

import importlib.util
import inspect
import logging
import re
import sys
import types
from pathlib import Path
from typing import Any

import yaml

from leap import __version__
from leap.config import experiments_dir

logger = logging.getLogger(__name__)

VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Sentinel entry_point meaning "open the experiment README page" (/static/readme.html?exp=...)
ENTRY_POINT_README = "readme"

DEFAULT_FRONTMATTER = {
    "display_name": "",
    "description": "",
    "version": "",
    "entry_point": ENTRY_POINT_README,
    "require_registration": True,
}


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string like '1.0.0' into a tuple of ints."""
    return tuple(int(x) for x in v.split(".") if x.isdigit())


def check_leap_version(required: str) -> tuple[bool, str]:
    """Check if current LEAP2 version satisfies a requirement like '>=1.0'.

    Returns (ok, message).
    """
    req = required.strip()
    if not req:
        return True, ""

    if req.startswith(">="):
        min_ver = _parse_version(req[2:])
        cur_ver = _parse_version(__version__)
        if cur_ver >= min_ver:
            return True, f"LEAP2 {__version__} >= {req[2:]}"
        return False, f"LEAP2 {__version__} < {req[2:]} (required: {req})"
    elif req.startswith(">"):
        min_ver = _parse_version(req[1:])
        cur_ver = _parse_version(__version__)
        if cur_ver > min_ver:
            return True, f"LEAP2 {__version__} > {req[1:]}"
        return False, f"LEAP2 {__version__} <= {req[1:]} (required: {req})"
    elif req.startswith("=="):
        req_ver = _parse_version(req[2:])
        cur_ver = _parse_version(__version__)
        if cur_ver == req_ver:
            return True, f"LEAP2 {__version__} == {req[2:]}"
        return False, f"LEAP2 {__version__} != {req[2:]} (required: {req})"
    else:
        # Treat bare version as >=
        min_ver = _parse_version(req)
        cur_ver = _parse_version(__version__)
        if cur_ver >= min_ver:
            return True, f"LEAP2 {__version__} >= {req}"
        return False, f"LEAP2 {__version__} < {req} (required: >={req})"


def validate_experiment_name(name: str) -> bool:
    return bool(VALID_NAME_RE.match(name))


def parse_frontmatter(readme_path: Path) -> dict:
    """Parse YAML frontmatter from a README.md file."""
    if not readme_path.exists():
        return dict(DEFAULT_FRONTMATTER)

    text = readme_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return dict(DEFAULT_FRONTMATTER)

    end = text.find("---", 3)
    if end == -1:
        return dict(DEFAULT_FRONTMATTER)

    try:
        fm = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError as e:
        logger.warning("Bad YAML frontmatter in %s: %s", readme_path, e)
        return dict(DEFAULT_FRONTMATTER)

    result = dict(DEFAULT_FRONTMATTER)
    result.update(fm)
    return result


def load_functions(funcs_dir: Path) -> dict[str, callable]:
    """Load all public callables from *.py files in funcs_dir."""
    functions: dict[str, callable] = {}
    if not funcs_dir.is_dir():
        return functions

    parent_str = str(funcs_dir)
    if parent_str not in sys.path:
        sys.path.insert(0, parent_str)

    for py_file in sorted(funcs_dir.glob("*.py")):
        module_name = f"_leap_funcs_{funcs_dir.parent.name}_{py_file.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception:
            logger.exception("Failed to load %s", py_file)
            continue

        exported = getattr(module, "__all__", None)
        names = exported if exported is not None else dir(module)
        for attr_name in names:
            if attr_name.startswith("_"):
                continue
            obj = getattr(module, attr_name)
            if callable(obj) and not isinstance(obj, type) and not isinstance(obj, types.ModuleType):
                if attr_name in functions:
                    logger.warning(
                        "Duplicate function '%s' in %s (already loaded); overwriting",
                        attr_name, py_file,
                    )
                functions[attr_name] = obj

    logger.info("Loaded %d functions from %s", len(functions), funcs_dir)
    return functions


def get_function_info(func: callable) -> dict[str, str]:
    """Return signature, docstring, and decorator flags for a callable."""
    try:
        sig = str(inspect.signature(func))
    except (ValueError, TypeError):
        sig = "(...)"
    return {
        "signature": sig,
        "doc": inspect.getdoc(func) or "",
        "nolog": getattr(func, "_leap_nolog", False),
        "noregcheck": getattr(func, "_leap_noregcheck", False),
        "adminonly": getattr(func, "_leap_adminonly", False),
        "ratelimit": getattr(func, "_leap_ratelimit", "default"),
    }


class ExperimentInfo:
    """Holds loaded experiment state."""

    def __init__(self, name: str, path: Path):
        self.name = name
        self.path = path
        self.readme_path = path / "README.md"
        self.funcs_dir = path / "funcs"
        self.ui_dir = path / "ui"
        self.db_path = path / "db" / "experiment.db"

        self.frontmatter = parse_frontmatter(self.readme_path)
        self.display_name = self.frontmatter.get("display_name") or name
        self.description = self.frontmatter.get("description", "")
        self.version = self.frontmatter.get("version", "")
        self.entry_point = self.frontmatter.get("entry_point", ENTRY_POINT_README)
        self.require_registration = self.frontmatter.get("require_registration", True)
        self.leap_version = self.frontmatter.get("leap_version", "")
        self.pages = self.frontmatter.get("pages", [])

        # Check leap_version requirement
        self.version_ok, self.version_message = check_leap_version(self.leap_version)
        if self.leap_version and not self.version_ok:
            logger.warning(
                "Experiment '%s' requires %s — %s", name, self.leap_version, self.version_message
            )

        self.functions: dict[str, callable] = {}
        self.reload_functions()

    def reload_metadata(self) -> dict:
        """Re-parse README frontmatter from disk."""
        self.frontmatter = parse_frontmatter(self.readme_path)
        self.display_name = self.frontmatter.get("display_name") or self.name
        self.description = self.frontmatter.get("description", "")
        self.version = self.frontmatter.get("version", "")
        self.entry_point = self.frontmatter.get("entry_point", ENTRY_POINT_README)
        self.require_registration = self.frontmatter.get("require_registration", True)
        self.leap_version = self.frontmatter.get("leap_version", "")
        self.pages = self.frontmatter.get("pages", [])
        self.version_ok, self.version_message = check_leap_version(self.leap_version)
        return self.frontmatter

    def reload_functions(self) -> int:
        self.functions = load_functions(self.funcs_dir)
        return len(self.functions)

    def get_functions_info(self) -> dict[str, dict]:
        return {name: get_function_info(fn) for name, fn in self.functions.items()}

    def to_metadata(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "version": self.version,
            "entry_point": self.entry_point,
            "function_count": len(self.functions),
            "require_registration": self.require_registration,
            "leap_version": self.leap_version,
            "leap_version_ok": self.version_ok,
            "pages": self.pages,
        }


def discover_experiments(root: Path | None = None) -> dict[str, ExperimentInfo]:
    """Scan experiments/ directory and return loaded experiments."""
    exp_dir = experiments_dir(root)
    experiments: dict[str, ExperimentInfo] = {}

    if not exp_dir.is_dir():
        logger.warning("Experiments directory not found: %s", exp_dir)
        return experiments

    for child in sorted(exp_dir.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if not validate_experiment_name(name):
            suggested = name.lower().replace(" ", "-")
            if validate_experiment_name(suggested):
                logger.warning(
                    "Skipping '%s' — experiment names must be lowercase "
                    "(matching [a-z0-9][a-z0-9_-]*). Try renaming to '%s'.",
                    name, suggested,
                )
            else:
                logger.warning(
                    "Skipping '%s' — experiment names must match [a-z0-9][a-z0-9_-]* "
                    "(lowercase letters, digits, hyphens, underscores).",
                    name,
                )
            continue
        try:
            experiments[name] = ExperimentInfo(name, child)
            logger.info("Discovered experiment: %s", name)
        except Exception:
            logger.exception("Failed to load experiment '%s'", name)

    return experiments

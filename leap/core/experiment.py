"""Experiment discovery, README frontmatter parsing, and function loading."""

from __future__ import annotations

import importlib.util
import inspect
import logging
import operator as _operator
import re
import sys
import types
from pathlib import Path
from typing import Any

import yaml

from leap import __version__
from leap.config import experiments_dir, parse_frontmatter_text

logger = logging.getLogger(__name__)

VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Sentinel entry_point meaning "open the experiment README page" (/static/readme.html?exp=...)
ENTRY_POINT_README = "readme"

DEFAULT_FRONTMATTER = {
    "type": "experiment",
    "display_name": "",
    "description": "",
    "version": "",
    "entry_point": ENTRY_POINT_README,
    "require_registration": True,
    "author": "",
    "organization": "",
    "tags": [],
    "repository": "",
}


def _parse_version(v: str) -> tuple[int, ...]:
    """Parse a version string like '1.0.0' into a tuple of ints."""
    return tuple(int(x) for x in v.split(".") if x.isdigit())


_VERSION_OPS: list[tuple[str, _operator.attrgetter | callable, str, str]] = [
    (">=", _operator.ge, "{cur} >= {ver}", "{cur} < {ver} (required: {req})"),
    (">",  _operator.gt, "{cur} > {ver}",  "{cur} <= {ver} (required: {req})"),
    ("==", _operator.eq, "{cur} == {ver}", "{cur} != {ver} (required: {req})"),
]


def check_leap_version(required: str) -> tuple[bool, str]:
    """Check if current LEAP2 version satisfies a requirement like '>=1.0'.

    Returns (ok, message).
    """
    req = required.strip()
    if not req:
        return True, ""

    cur_ver = _parse_version(__version__)
    cur = f"LEAP2 {__version__}"

    for prefix, op, ok_fmt, fail_fmt in _VERSION_OPS:
        if req.startswith(prefix):
            ver_str = req[len(prefix):]
            if op(cur_ver, _parse_version(ver_str)):
                return True, ok_fmt.format(cur=cur, ver=ver_str)
            return False, fail_fmt.format(cur=cur, ver=ver_str, req=req)

    # Bare version treated as >=
    if _operator.ge(cur_ver, _parse_version(req)):
        return True, f"{cur} >= {req}"
    return False, f"{cur} < {req} (required: >={req})"


def validate_experiment_name(name: str) -> bool:
    return bool(VALID_NAME_RE.match(name))


def parse_frontmatter(readme_path: Path) -> dict:
    """Parse YAML frontmatter from a README.md file."""
    try:
        text = readme_path.read_text(encoding="utf-8")
    except OSError:
        return dict(DEFAULT_FRONTMATTER)
    return parse_frontmatter_text(text, DEFAULT_FRONTMATTER)


def update_frontmatter_field(readme_path: Path, field: str, value: Any) -> bool:
    """Update or add a field in README YAML frontmatter. Returns True if written."""
    try:
        text = readme_path.read_text(encoding="utf-8")
    except OSError:
        return False

    if not text.startswith("---"):
        return False

    end = text.find("---", 3)
    if end == -1:
        return False

    try:
        parsed = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return False

    parsed[field] = value
    new_yaml = yaml.dump(parsed, default_flow_style=False, sort_keys=False)
    rebuilt = "---\n" + new_yaml + "---" + text[end + 3:]
    readme_path.write_text(rebuilt, encoding="utf-8")
    return True


def get_experiment_list(readme_path: Path) -> list[dict]:
    """Read the 'experiments' list from lab README frontmatter."""
    fm = parse_frontmatter(readme_path)
    entries = fm.get("experiments", [])
    return entries if isinstance(entries, list) else []


def add_experiment_entry(readme_path: Path, name: str, source: str = "") -> bool:
    """Add or update an experiment entry in the lab README's experiments list.

    Idempotent: if name already exists, updates its source. Returns True if written.
    source should be a URL for remote experiments, or empty/"" for local ones.
    """
    entries = get_experiment_list(readme_path)
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name") == name:
            old_source = entry.get("source", "")
            if old_source == source or (not old_source and not source):
                return False
            if source:
                entry["source"] = source
            else:
                entry.pop("source", None)
            return update_frontmatter_field(readme_path, "experiments", entries)
    new_entry: dict = {"name": name}
    if source:
        new_entry["source"] = source
    entries.append(new_entry)
    return update_frontmatter_field(readme_path, "experiments", entries)


def remove_experiment_entry(readme_path: Path, name: str) -> bool:
    """Remove an experiment entry from the lab README's experiments list."""
    entries = get_experiment_list(readme_path)
    new_entries = [e for e in entries if not (isinstance(e, dict) and e.get("name") == name)]
    if len(new_entries) == len(entries):
        return False
    return update_frontmatter_field(readme_path, "experiments", new_entries)


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
                # Skip imports — only export functions defined in this module
                if exported is None and getattr(obj, "__module__", None) != module_name:
                    continue
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
        self._apply_frontmatter()

        if self.leap_version and not self.version_ok:
            logger.warning(
                "Experiment '%s' requires %s — %s", name, self.leap_version, self.version_message
            )

        self.functions: dict[str, callable] = {}
        self.reload_functions()

    def _apply_frontmatter(self):
        """Sync instance attributes from self.frontmatter."""
        fm = self.frontmatter
        self.display_name = fm.get("display_name") or self.name
        self.description = fm.get("description", "")
        self.version = fm.get("version", "")
        self.entry_point = fm.get("entry_point", ENTRY_POINT_README)
        self.require_registration = fm.get("require_registration", True)
        self.leap_version = fm.get("leap_version", "")
        self.pages = fm.get("pages", [])
        self.author = fm.get("author", "")
        self.organization = fm.get("organization", "")
        self.tags = fm.get("tags", [])
        self.repository = fm.get("repository", "")
        self.version_ok, self.version_message = check_leap_version(self.leap_version)

    def reload_metadata(self) -> dict:
        """Re-parse README frontmatter from disk."""
        self.frontmatter = parse_frontmatter(self.readme_path)
        self._apply_frontmatter()
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
            "author": self.author,
            "organization": self.organization,
            "tags": self.tags,
            "repository": self.repository,
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

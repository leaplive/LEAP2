"""Typer CLI for LEAP2. Shared functions used by both CLI and web API."""

from __future__ import annotations

import contextlib
import csv
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
import typer
import yaml

from leap import __version__
from leap.config import get_root, is_lab_root

logger = logging.getLogger(__name__)

app = typer.Typer(help="LEAP2 — Live Experiments for Active Pedagogy")

REGISTRY_URL = "https://raw.githubusercontent.com/leaplive/registry/main/registry.yaml"
REGISTRY_REPO = "leaplive/registry"

# pip→import name mappings for dependency checking
_IMPORT_MAP = {"pyyaml": "yaml", "pillow": "PIL", "scikit_learn": "sklearn"}


def _slugify_dir(name: str) -> str:
    """Derive a slug from a directory name."""
    return re.sub(r"[^a-z0-9_-]+", "", re.sub(r"[\s.]+", "-", name.lower().strip())).strip("-") or "my-lab"


def _display_name_from_slug(name: str) -> str:
    """Convert a slug to a human-readable display name."""
    return name.replace("-", " ").replace("_", " ").title()


def _parse_tags(raw: str) -> list[str]:
    """Parse a comma-separated tags string into a list."""
    return [t.strip() for t in raw.split(",") if t.strip()] if raw else []


def _yaml_str_or_list(val: list[str] | str) -> str:
    """Format a value as YAML inline: single string or [a, b] list."""
    if isinstance(val, list):
        if len(val) == 1:
            return val[0]
        return "[" + ", ".join(val) + "]"
    return str(val)


def _shorten_repo_url(url: str) -> str:
    """Strip scheme prefix and .git suffix for display."""
    for prefix in ("https://", "git@"):
        if url.startswith(prefix):
            return url[len(prefix):].removesuffix(".git").replace(":", "/")
    return url


def _print_validation_results(results: list[dict]) -> bool:
    """Print validation results with icons. Returns True if there were issues."""
    has_issues = False
    for r in results:
        if r["status"] == "ok":
            icon = "✓"
        elif r["status"] == "warning":
            icon = "!"
            has_issues = True
        else:
            icon = "✗"
            has_issues = True
        typer.echo(f"  {icon} {r['check']}: {r['message']}")
    return has_issues


def _validate_and_report(exp_name: str, root: Path | None = None):
    """Validate an experiment and print results with restart reminder."""
    results = validate_experiment_fn(exp_name, root)
    if _print_validation_results(results):
        typer.echo("Some checks had warnings — review above.")
    else:
        typer.echo("Validation passed.")
    typer.echo("Restart the server to load the new experiment.")


class LabDetectedError(Exception):
    """Raised when a cloned repo is a lab, not an experiment."""

    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url


def _resolve_root(root: Path | None) -> Path:
    return root or get_root()


@contextlib.contextmanager
def _experiment_session(experiment: str, root: Path | None = None):
    """Open a DB session for an experiment, closing it on exit."""
    from leap.core.experiment import ExperimentInfo
    from leap.core import storage

    resolved = _resolve_root(root)
    exp_path = resolved / "experiments" / experiment
    if not exp_path.is_dir():
        raise typer.BadParameter(f"Experiment '{experiment}' not found at {exp_path}")
    exp_info = ExperimentInfo(experiment, exp_path)
    session = storage.get_session(experiment, exp_info.db_path)
    try:
        yield exp_info, session
    finally:
        session.close()


def _get_git_remote(path: Path) -> str:
    """Return the git remote 'origin' URL for a path, or empty string."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _ensure_gitignore_entries(root: Path, entries: list[str]) -> list[str]:
    """Add entries to .gitignore if not already present. Returns list of added entries."""
    gitignore = root / ".gitignore"
    if gitignore.is_file():
        content = gitignore.read_text(encoding="utf-8")
        lines = content.splitlines()
    else:
        content = ""
        lines = []
    added = [e for e in entries if e not in lines]
    if not added:
        return []
    if content and not content.endswith("\n"):
        content += "\n"
    content += "\n".join(added) + "\n"
    gitignore.write_text(content, encoding="utf-8")
    return added


def _add_gitignore_entry(root: Path, name: str) -> None:
    """Add experiments/<name>/ to .gitignore if not already present."""
    _ensure_gitignore_entries(root, [f"experiments/{name}/"])


def _remove_gitignore_entry(root: Path, name: str) -> None:
    """Remove experiments/<name>/ from .gitignore if present."""
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return
    entry = f"experiments/{name}/"
    lines = gitignore.read_text(encoding="utf-8").splitlines()
    filtered = [line for line in lines if line != entry]
    if len(filtered) != len(lines):
        gitignore.write_text("\n".join(filtered) + "\n" if filtered else "", encoding="utf-8")


def set_password_fn(root: Path | None = None) -> None:
    """Set or update the admin password."""
    import getpass
    from leap.core.auth import hash_password, save_credentials

    pw = getpass.getpass("New admin password: ")
    if not pw:
        raise typer.BadParameter("Password cannot be empty")
    pw2 = getpass.getpass("Confirm admin password: ")
    if pw != pw2:
        raise typer.BadParameter("Passwords do not match")

    cred = hash_password(pw)
    save_credentials(cred, _resolve_root(root))
    typer.echo("Admin password updated.")


def add_student_fn(
    experiment: str,
    student_id: str,
    name: str | None = None,
    root: Path | None = None,
) -> dict:
    """Add a student to an experiment. Returns student dict."""
    from leap.core import storage

    with _experiment_session(experiment, root) as (_exp_info, session):
        storage.add_student(session, student_id, name or student_id)
        return {"student_id": student_id, "name": name or student_id}


def import_students_fn(
    experiment: str,
    csv_file: Path,
    root: Path | None = None,
) -> dict:
    """Import students from a CSV file. Returns result dict with added/skipped/errors."""
    from leap.core import storage

    if not csv_file.is_file():
        raise typer.BadParameter(f"CSV file not found: {csv_file}")

    with open(csv_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "student_id" not in reader.fieldnames:
            raise typer.BadParameter("CSV must have a 'student_id' column header")
        rows = list(reader)

    with _experiment_session(experiment, root) as (_exp_info, session):
        return storage.bulk_add_students(session, rows)


def list_students_fn(experiment: str, root: Path | None = None) -> list[dict]:
    """List students in an experiment."""
    from leap.core import storage

    with _experiment_session(experiment, root) as (_exp_info, session):
        return storage.list_students(session)


def init_project_fn(root: Path | None = None) -> dict[str, str]:
    """Bootstrap LEAP2 project structure. Returns {path: status} for each dir/file."""
    resolved = _resolve_root(root)
    results: dict[str, str] = {}

    dirs = [
        resolved / "experiments",
        resolved / "config",
    ]
    for d in dirs:
        rel = str(d.relative_to(resolved))
        if d.is_dir():
            results[rel] = "exists"
        else:
            d.mkdir(parents=True, exist_ok=True)
            results[rel] = "created"

    essential = [
        "config/admin_credentials.json",
        "experiments/*/db/",
        "__pycache__/",
        "*.pyc",
        ".env",
    ]
    added = _ensure_gitignore_entries(resolved, essential)
    if added:
        gitignore = resolved / ".gitignore"
        # Check if .gitignore existed before (has more lines than what we added)
        lines = gitignore.read_text(encoding="utf-8").splitlines()
        results[".gitignore"] = "updated" if len(lines) > len(added) else "created"
    else:
        results[".gitignore"] = "exists"

    return results


def _prompt_lab_metadata(slug: str) -> dict[str, str | list[str]]:
    """Prompt interactively for lab metadata. Returns dict of field values."""
    typer.echo()
    typer.echo("Configure your lab (you can change these later in README.md):")
    typer.echo()

    typer.echo("  A unique identifier for this lab (lowercase, hyphens).")
    name = typer.prompt("  Name", default=slug).strip()
    typer.echo()

    typer.echo("  A human-readable name shown on the landing page.")
    display_name = typer.prompt("  Display name (optional)", default="").strip()
    typer.echo()

    typer.echo("  A short description of what this lab contains.")
    description = typer.prompt("  Description", default="").strip()
    typer.echo()

    typer.echo("  Who created this lab — shown on the landing page.")
    authors_raw = typer.prompt("  Author(s) (comma-separated, optional)", default="").strip()
    authors = [a.strip() for a in authors_raw.split(",") if a.strip()] if authors_raw else []
    typer.echo()

    typer.echo("  Your university, company, or group.")
    orgs_raw = typer.prompt("  Organization(s) (comma-separated, optional)", default="").strip()
    organizations = [o.strip() for o in orgs_raw.split(",") if o.strip()] if orgs_raw else []
    typer.echo()

    typer.echo("  Keywords to help others discover this lab, e.g. algorithms, intro-cs.")
    tags = _parse_tags(typer.prompt("  Tags (comma-separated, optional)", default="").strip())

    return {
        "name": name or slug,
        "display_name": display_name,
        "description": description,
        "authors": authors,
        "organizations": organizations,
        "tags": tags,
    }


def _ensure_lab_root_readme(cwd: Path, meta: dict | None = None) -> str:
    """Ensure root README has ``type: lab``. Returns a short status: created|updated|skipped."""
    from leap.core.experiment import parse_frontmatter, validate_experiment_name, update_frontmatter_field

    readme = cwd / "README.md"
    slug = _slugify_dir(cwd.name)
    if not validate_experiment_name(slug):
        slug = "my-lab"

    if not readme.is_file():
        m = meta or {"name": slug, "display_name": "", "description": "", "authors": [], "organizations": [], "tags": []}
        tags_yaml = f" [{', '.join(m['tags'])}]" if m.get("tags") else " []"
        authors_line = f"authors: {_yaml_str_or_list(m['authors'])}\n" if m.get("authors") else ""
        orgs_line = f"organizations: {_yaml_str_or_list(m['organizations'])}\n" if m.get("organizations") else ""
        readme.write_text(
            f"---\nname: {m['name']}\ntype: lab\n"
            f"display_name: \"{m['display_name']}\"\n"
            f"description: \"{m['description']}\"\n"
            f"{authors_line}"
            f"{orgs_line}"
            f"tags:{tags_yaml}\n"
            f"experiments: []\n---\n\n"
            f"# {m.get('display_name') or cwd.name}\n\n"
            "LEAP project root. Add experiments under `experiments/`.\n",
            encoding="utf-8",
        )
        return "created"

    fm = parse_frontmatter(readme)
    rt = str(fm.get("type", "") or "")
    if rt == "lab":
        return "skipped"

    text = readme.read_text(encoding="utf-8")
    if text.startswith("---"):
        if update_frontmatter_field(readme, "type", "lab"):
            if not fm.get("name"):
                update_frontmatter_field(readme, "name", slug)
            return "updated"
        end = text.find("---", 3)
        body = text[end + 3 :].lstrip("\n") if end != -1 else text
        readme.write_text(
            f"---\nname: {slug}\ntype: lab\ndisplay_name: \"\"\ndescription: \"\"\n---\n\n{body}",
            encoding="utf-8",
        )
        return "updated"

    readme.write_text(
        f"---\nname: {slug}\ntype: lab\ndisplay_name: \"\"\ndescription: \"\"\n---\n\n{text}",
        encoding="utf-8",
    )
    return "updated"


def _install_experiment_deps(root: Path) -> list[str]:
    """Install requirements.txt for all experiments that have one. Returns list of experiment names."""
    exp_dir = root / "experiments"
    if not exp_dir.is_dir():
        return []

    installed = []
    for child in sorted(exp_dir.iterdir()):
        if not child.is_dir():
            continue
        req_file = child / "requirements.txt"
        if req_file.is_file():
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                installed.append(child.name)
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                logger.warning("pip install failed for %s: %s", child.name, e)
    return installed


def _reinstall_missing_remote_experiments(root: Path) -> list[str]:
    """Reinstall remote experiments listed in README but missing from disk. Returns names reinstalled."""
    from leap.core.experiment import get_experiment_list, validate_experiment_name

    readme = root / "README.md"
    exp_dir = root / "experiments"
    if not readme.is_file() or not exp_dir.is_dir():
        return []

    listed_entries = get_experiment_list(readme)
    on_disk = {c.name for c in exp_dir.iterdir() if c.is_dir() and validate_experiment_name(c.name)}
    entries_by_name = {e["name"]: e for e in listed_entries if isinstance(e, dict) and "name" in e}

    reinstalled = []
    for name, entry in sorted(entries_by_name.items()):
        if name in on_disk:
            continue
        source = entry.get("source", "")
        if not source:
            continue
        if typer.confirm(f"Experiment '{name}' missing (from {source}). Reinstall?", default=True):
            try:
                install_experiment_fn(source, name=name, root=root)
                reinstalled.append(name)
            except Exception as e:
                logger.warning("Failed to reinstall %s: %s", name, e)
    return reinstalled


def init_fn(
    *,
    force_password: bool = False,
    skip_password: bool = False,
    interactive: bool = True,
) -> dict[str, str]:
    """Initialize or set up the current directory as a LEAP lab root.

    Idempotent — safe to run on both new and cloned labs. Creates directories
    and README if missing, installs experiment dependencies, reinstalls missing
    remote experiments, and sets admin password.
    """
    from leap.config import credentials_path
    from leap.core.experiment import validate_experiment_name

    cwd = Path.cwd().resolve()

    if cwd.parent.name == "experiments" and validate_experiment_name(cwd.name):
        raise typer.BadParameter(
            "Run `leap init` from the project root, not from inside `experiments/<name>`."
        )

    results: dict[str, str] = dict(init_project_fn(cwd))

    # Prompt for lab metadata if README doesn't exist yet
    readme = cwd / "README.md"
    meta = None
    if not readme.is_file() and interactive and sys.stdin.isatty():
        meta = _prompt_lab_metadata(_slugify_dir(cwd.name))

    readme_status = _ensure_lab_root_readme(cwd, meta=meta)
    results["readme"] = readme_status

    from leap.core.experiment import parse_frontmatter, update_frontmatter_field
    if readme.is_file():
        fm = parse_frontmatter(readme)

        # Populate missing optional fields interactively
        if interactive and sys.stdin.isatty() and meta is None:
            optional_fields = {
                "display_name": ("Display name", "A human-readable name shown on the landing page."),
                "description": ("Description", "A short description of what this lab contains."),
                "authors": ("Author(s)", "Who created this lab — shown on the landing page."),
                "organizations": ("Organization(s)", "Your university, company, or group."),
                "tags": ("Tags (comma-separated)", "Keywords to help others discover this lab."),
            }
            updated = False
            for field, (label, hint) in optional_fields.items():
                if not fm.get(field):
                    typer.echo(f"\n  {hint}")
                    if field == "tags":
                        value = _parse_tags(typer.prompt(f"  {label} (optional)", default="").strip())
                    else:
                        value = typer.prompt(f"  {label} (optional)", default="").strip()
                    if value:
                        update_frontmatter_field(readme, field, value)
                        updated = True
            if updated:
                fm = parse_frontmatter(readme)
                results["readme"] = "updated"

        # Populate repository field from git remote if missing
        if not fm.get("repository", ""):
            remote = _get_git_remote(cwd)
            if remote:
                update_frontmatter_field(readme, "repository", remote)
                results["repository"] = remote

    synced = _sync_experiments_list(cwd)
    if synced:
        results["experiments_synced"] = str(synced)

    deps_installed = _install_experiment_deps(cwd)
    if deps_installed:
        results["deps_installed"] = ", ".join(deps_installed)

    reinstalled = _reinstall_missing_remote_experiments(cwd)
    if reinstalled:
        results["experiments_reinstalled"] = ", ".join(reinstalled)

    cred_path = credentials_path(cwd)
    if skip_password:
        results["password"] = "skipped"
    elif cred_path.is_file() and not force_password:
        results["password"] = "exists"
    else:
        set_password_fn(cwd)
        results["password"] = "set"

    return results


def _sync_experiments_list(root: Path) -> int:
    """Scan experiments/ and populate the README experiments list. Returns count added."""
    from leap.core.experiment import add_experiment_entry, validate_experiment_name

    readme = root / "README.md"
    if not readme.is_file():
        return 0

    exp_dir = root / "experiments"
    if not exp_dir.is_dir():
        return 0

    count = 0
    for child in sorted(exp_dir.iterdir()):
        if not child.is_dir() or not validate_experiment_name(child.name):
            continue
        source = _get_git_remote(child) if (child / ".git").is_dir() else ""
        if add_experiment_entry(readme, child.name, source):
            count += 1
    return count


def _prompt_experiment_metadata(name: str, interactive: bool = True) -> dict:
    """Prompt for experiment metadata. Returns a dict of frontmatter fields."""
    default_display = _display_name_from_slug(name)

    if not interactive:
        return {
            "display_name": default_display,
            "description": "",
            "authors": [],
            "organizations": [],
            "tags": [],
            "require_registration": True,
            "entry_point": "dashboard.html",
        }

    typer.echo()
    typer.echo("Configure your experiment (you can change these later in README.md):")
    typer.echo()

    typer.echo("  The display name is shown on the landing page and experiment navbar.")
    display_name = typer.prompt("  Display name", default=default_display).strip()
    typer.echo()

    typer.echo("  A short description of what students will do in this experiment.")
    description = typer.prompt("  Description").strip()
    typer.echo()

    typer.echo("  Who created this experiment — shown on the landing page.")
    authors_raw = typer.prompt("  Author(s) (comma-separated)").strip()
    authors = [a.strip() for a in authors_raw.split(",") if a.strip()] if authors_raw else []
    typer.echo()

    typer.echo("  Your university, company, or group (optional).")
    orgs_raw = typer.prompt("  Organization(s) (comma-separated)", default="").strip()
    organizations = [o.strip() for o in orgs_raw.split(",") if o.strip()] if orgs_raw else []
    typer.echo()

    typer.echo("  Keywords to help others find this experiment, e.g. algorithms, graphs, BFS.")
    tags = _parse_tags(typer.prompt("  Tags (comma-separated)", default="").strip())
    typer.echo()

    typer.echo("  If yes, students must register with an ID before calling functions.")
    typer.echo("  Set to no for open experiments where anyone can participate.")
    require_reg = typer.confirm("  Require student registration?", default=True)
    typer.echo()

    typer.echo("  The HTML file loaded when a student opens this experiment.")
    typer.echo("  Use 'readme' to show the README as the default page instead.")
    entry_point = typer.prompt("  Entry point", default="dashboard.html").strip()

    meta = {
        "display_name": display_name,
        "description": description,
        "authors": authors,
        "organizations": organizations,
        "tags": tags,
        "require_registration": require_reg,
        "entry_point": entry_point,
    }

    typer.echo()
    typer.echo("  Experiment metadata:")
    typer.echo(f"    Name:          {name}")
    typer.echo(f"    Display name:  {meta['display_name']}")
    typer.echo(f"    Description:   {meta['description']}")
    typer.echo(f"    Authors:       {', '.join(meta['authors']) if meta['authors'] else '(none)'}")
    typer.echo(f"    Organizations: {', '.join(meta['organizations']) if meta['organizations'] else '(none)'}")
    typer.echo(f"    Tags:          {', '.join(meta['tags']) if meta['tags'] else '(none)'}")
    typer.echo(f"    Registration:  {'required' if meta['require_registration'] else 'not required'}")
    typer.echo(f"    Entry point:   {meta['entry_point']}")
    typer.echo()
    typer.echo("  You can change any of these later in the experiment's README.md.")

    return meta


def new_experiment_fn(name: str, root: Path | None = None, interactive: bool = True) -> Path:
    """Scaffold a new experiment. Returns the experiment path."""
    from leap.core.experiment import validate_experiment_name

    resolved = _resolve_root(root)
    if not validate_experiment_name(name):
        raise typer.BadParameter(
            f"Invalid experiment name '{name}'. "
            "Must match [a-z0-9][a-z0-9_-]* (lowercase, digits, hyphens, underscores)."
        )

    exp_path = resolved / "experiments" / name
    if exp_path.exists():
        raise typer.BadParameter(f"Experiment '{name}' already exists at {exp_path}")

    meta = _prompt_experiment_metadata(name, interactive=interactive)
    remote_url = _get_git_remote(resolved)

    exp_path.mkdir(parents=True)
    (exp_path / "funcs").mkdir()
    (exp_path / "ui").mkdir()
    (exp_path / "db").mkdir()

    tags_yaml = f" [{', '.join(meta['tags'])}]" if meta["tags"] else " []"
    readme = exp_path / "README.md"
    readme.write_text(
        f"---\nname: {name}\ntype: experiment\ndisplay_name: {meta['display_name']}\n"
        f"description: \"{meta['description']}\"\n"
        f"authors: {_yaml_str_or_list(meta['authors'])}\n"
        f"organizations: {_yaml_str_or_list(meta['organizations'])}\n"
        f"tags:{tags_yaml}\n"
        f"repository: \"{remote_url}\"\n"
        f"entry_point: {meta['entry_point']}\n"
        f"require_registration: {'true' if meta['require_registration'] else 'false'}\n"
        f"---\n\n"
        f"# {meta['display_name']}\n\nExperiment instructions go here.\n",
        encoding="utf-8",
    )

    (exp_path / "requirements.txt").write_text(
        "# Add experiment dependencies here, one per line.\n",
        encoding="utf-8",
    )

    stub_func = exp_path / "funcs" / "functions.py"
    stub_func.write_text(
        '"""Experiment functions. Public callables are auto-discovered as RPC endpoints."""\n\n\n'
        "def hello(name: str = \"world\") -> str:\n"
        '    """Greet someone."""\n'
        '    return f"Hello, {name}!"\n',
        encoding="utf-8",
    )

    stub_ui = exp_path / "ui" / "dashboard.html"
    display = _display_name_from_slug(name)
    stub_ui.write_text(
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
        "  <meta charset=\"UTF-8\">\n  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
        f"  <title>{display} — LEAP2</title>\n"
        "  <script>(function(){var t=localStorage.getItem(\"leap-theme\");"
        "if(t===\"dark\"||(!t&&matchMedia(\"(prefers-color-scheme:dark)\").matches))"
        "document.documentElement.classList.add(\"dark\")})()</script>\n"
        "  <link rel=\"stylesheet\" href=\"/static/theme.css\">\n"
        "  <script src=\"/static/theme-toggle.js\" defer></script>\n"
        "</head>\n<body data-page=\"dashboard\">\n"
        "  <a class=\"skip-to-content\" href=\"#main\">Skip to content</a>\n"
        "  <script src=\"/static/navbar.js\"></script>\n"
        "  <main class=\"container\" id=\"main\">\n"
        f"    <h1>{display}</h1>\n"
        "    <p>Edit this page or add visualizations.</p>\n"
        "  </main>\n"
        "  <script src=\"/static/footer.js\"></script>\n"
        "</body>\n</html>\n",
        encoding="utf-8",
    )

    # Track in root README
    root_readme = resolved / "README.md"
    if root_readme.is_file():
        from leap.core.experiment import add_experiment_entry
        add_experiment_entry(root_readme, name, source="")

    return exp_path


def list_experiments_fn(root: Path | None = None) -> list[dict]:
    """Discover and return experiment metadata."""
    from leap.core.experiment import discover_experiments

    resolved = _resolve_root(root)
    exps = discover_experiments(resolved)
    return [
        {
            "name": info.name,
            "display_name": info.display_name,
            "description": info.description,
            "functions": len(info.functions),
            "require_registration": info.require_registration,
        }
        for info in exps.values()
    ]


def validate_experiment_fn(name: str, root: Path | None = None) -> list[dict]:
    """Validate an experiment. Returns list of {check, status, message}."""
    from leap.core.experiment import (
        validate_experiment_name,
        parse_frontmatter,
        load_functions,
        check_leap_version,
        ENTRY_POINT_README,
    )

    resolved = _resolve_root(root)
    results: list[dict] = []

    if not validate_experiment_name(name):
        results.append({"check": "name", "status": "error", "message": f"Invalid name '{name}'"})
        return results
    results.append({"check": "name", "status": "ok", "message": "Valid"})

    exp_path = resolved / "experiments" / name
    if not exp_path.is_dir():
        results.append({"check": "directory", "status": "error", "message": f"Not found: {exp_path}"})
        return results
    results.append({"check": "directory", "status": "ok", "message": str(exp_path)})

    readme_path = exp_path / "README.md"
    fm = parse_frontmatter(readme_path) if readme_path.is_file() else {}
    if not readme_path.is_file():
        results.append({"check": "readme", "status": "warning", "message": "README.md missing"})
    else:
        results.append({"check": "readme", "status": "ok", "message": f"Frontmatter parsed: {list(fm.keys())}"})

    funcs_dir = exp_path / "funcs"
    if not funcs_dir.is_dir():
        results.append({"check": "funcs", "status": "warning", "message": "funcs/ directory missing"})
    else:
        funcs = load_functions(funcs_dir)
        if funcs:
            results.append({"check": "funcs", "status": "ok", "message": f"{len(funcs)} function(s) loaded"})
        else:
            results.append({"check": "funcs", "status": "warning", "message": "No functions found"})
    leap_ver = fm.get("leap_version", "")
    if leap_ver:
        ok, msg = check_leap_version(leap_ver)
        if ok:
            results.append({"check": "leap_version", "status": "ok", "message": msg})
        else:
            results.append({"check": "leap_version", "status": "error", "message": msg})

    ui_dir = exp_path / "ui"
    entry = fm.get("entry_point", ENTRY_POINT_README)
    if entry == ENTRY_POINT_README:
        results.append({"check": "entry_point", "status": "ok", "message": "README page (default)"})
    else:
        entry_path = ui_dir / entry
        if not entry_path.is_file():
            results.append({"check": "entry_point", "status": "warning", "message": f"{entry} not found in ui/"})
        else:
            results.append({"check": "entry_point", "status": "ok", "message": f"{entry} exists"})

    return results


def show_config_fn(root: Path | None = None) -> dict:
    """Return resolved configuration."""
    from leap.config import (
        experiments_dir,
        config_dir,
        credentials_path,
        ui_dir as _ui_dir,
        SESSION_SECRET_KEY,
        DEFAULT_EXPERIMENT,
        ADMIN_PASSWORD_ENV,
    )

    resolved = _resolve_root(root)
    exp_dir = experiments_dir(resolved)
    exp_count = 0
    if exp_dir.is_dir():
        exp_count = sum(1 for c in exp_dir.iterdir() if c.is_dir())

    return {
        "root": str(resolved),
        "experiments_dir": str(exp_dir),
        "experiment_count": exp_count,
        "config_dir": str(config_dir(resolved)),
        "credentials_path": str(credentials_path(resolved)),
        "credentials_exist": credentials_path(resolved).is_file(),
        "ui_dir": str(_ui_dir(resolved)),
        "default_experiment": DEFAULT_EXPERIMENT or "(not set)",
        "session_secret_set": bool(SESSION_SECRET_KEY),
        "admin_password_env_set": bool(ADMIN_PASSWORD_ENV),
    }


def _doctor_hint(check: str, status: str) -> str:
    """Actionable fix text for doctor rows; empty when status is ok."""
    if status == "ok":
        return ""
    if check == "python":
        return "Install Python 3.10+, then reinstall LEAP2 (`pip install -e .`)."
    if check == "root":
        return "cd into the lab directory or set LEAP_ROOT to the project root."
    if check == "root_readme":
        return (
            "Edit root README.md frontmatter (`type: lab`), or run "
            "`leap init` in the project root."
        )
    if check == "experiments_dir":
        return "Run `leap run` once (bootstraps dirs) or create `experiments/` manually."
    if check == "experiments":
        return "`leap add <name>`."
    if check.startswith("experiment:"):
        exp_name = check.split(":", 1)[1]
        return (
            f"Edit `experiments/{exp_name}/README.md` — set `type: experiment` in frontmatter."
        )
    if check == "experiments_list":
        return "Run `leap doctor` to interactively resolve experiment list mismatches."
    if check == "credentials":
        return "`leap set-password` (or set `ADMIN_PASSWORD` for non-interactive setup)."
    if check.startswith("deps:"):
        exp_name = check.split(":", 1)[1]
        return f"`pip install -r experiments/{exp_name}/requirements.txt` or `leap init`."
    if check.startswith("package:"):
        pkg = check.split(":", 1)[1]
        return f"`pip install -e .` in the lab root, or `pip install {pkg}`."
    return ""


def _doctor_row(check: str, status: str, message: str) -> dict:
    return {
        "check": check,
        "status": status,
        "message": message,
        "hint": _doctor_hint(check, status),
    }


def doctor_fn(root: Path | None = None) -> list[dict]:
    """Validate overall LEAP2 setup.

    Returns list of dicts with keys: check, status, message, hint.
    ``hint`` is empty when status is ok; otherwise suggests commands or edits.
    """
    from leap.config import experiments_dir, credentials_path
    from leap.core.experiment import parse_frontmatter, validate_experiment_name, get_experiment_list

    resolved = _resolve_root(root)
    results: list[dict] = []

    v = sys.version_info
    if v >= (3, 10):
        results.append(_doctor_row("python", "ok", f"Python {v.major}.{v.minor}.{v.micro}"))
    else:
        results.append(_doctor_row("python", "error", f"Python {v.major}.{v.minor} < 3.10"))

    reason = getattr(get_root, "reason", "")
    if resolved.is_dir():
        results.append(_doctor_row("root", "ok", f"{resolved} ({reason})"))
    else:
        results.append(_doctor_row("root", "error", f"Not found: {resolved}"))

    # Root README and type detection
    root_readme = resolved / "README.md"
    if root_readme.is_file():
        fm = parse_frontmatter(root_readme)
        root_type = fm.get("type", "")
        root_name = fm.get("name", "")
        if root_type == "lab":
            results.append(
                _doctor_row("root_readme", "ok", f"type: lab, name: {root_name or '(unnamed)'}")
            )
        elif root_type == "experiment":
            results.append(
                _doctor_row(
                    "root_readme",
                    "warning",
                    "Root README has type: experiment — use type: lab for a project root",
                )
            )
        elif root_type:
            results.append(
                _doctor_row(
                    "root_readme",
                    "warning",
                    f"Unknown type: {root_type} — expected 'lab' for a project root",
                )
            )
        else:
            has_frontmatter = root_readme.read_text(encoding="utf-8").startswith("---")
            if has_frontmatter:
                results.append(
                    _doctor_row(
                        "root_readme",
                        "warning",
                        "Has frontmatter but missing 'type' field — add type: lab or run `leap init`",
                    )
                )
            else:
                results.append(
                    _doctor_row(
                        "root_readme",
                        "warning",
                        "No YAML frontmatter found — add type: lab or run `leap init`",
                    )
                )
    else:
        results.append(
            _doctor_row(
                "root_readme",
                "warning",
                "No README.md — run `leap init` or add README.md with `type: lab`",
            )
        )

    if root_readme.is_file():
        repo = fm.get("repository", "")
        if repo:
            results.append(_doctor_row("repository", "ok", repo))
        else:
            remote = _get_git_remote(resolved)
            if remote:
                results.append(_doctor_row(
                    "repository", "warning",
                    f"Not set in README (git remote: {remote}) — run `leap init` to populate",
                ))
            else:
                results.append(_doctor_row(
                    "repository", "warning",
                    "No repository in README and no git remote found",
                ))

    exp_dir = experiments_dir(resolved)
    if exp_dir.is_dir():
        results.append(_doctor_row("experiments_dir", "ok", str(exp_dir)))
    else:
        results.append(_doctor_row("experiments_dir", "error", "experiments/ not found"))

    if exp_dir.is_dir():
        exp_names = [c.name for c in sorted(exp_dir.iterdir()) if c.is_dir()]
        if exp_names:
            results.append(
                _doctor_row("experiments", "ok", f"{len(exp_names)}: {', '.join(exp_names)}")
            )

            # Check each experiment's README and type
            for exp_name in exp_names:
                exp_readme = exp_dir / exp_name / "README.md"
                if exp_readme.is_file():
                    efm = parse_frontmatter(exp_readme)
                    etype = efm.get("type", "")
                    if etype == "experiment":
                        pass  # expected, don't clutter output
                    elif etype == "lab":
                        results.append(
                            _doctor_row(
                                f"experiment:{exp_name}",
                                "warning",
                                "Has type: lab — should be type: experiment",
                            )
                        )
                    elif not etype:
                        results.append(
                            _doctor_row(
                                f"experiment:{exp_name}",
                                "warning",
                                "Missing 'type' field in README frontmatter",
                            )
                        )
                else:
                    results.append(
                        _doctor_row(f"experiment:{exp_name}", "warning", "No README.md")
                    )

                # Check experiment dependencies
                req_file = exp_dir / exp_name / "requirements.txt"
                if req_file.is_file():
                    import importlib.util
                    missing_pkgs = []
                    for line in req_file.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or line.startswith("-"):
                            continue
                        pkg = line.split(">=")[0].split("<=")[0].split("==")[0].split("~=")[0].split("!=")[0].split("[")[0].strip()
                        import_name = pkg.replace("-", "_").lower()
                        import_name = _IMPORT_MAP.get(import_name, import_name)
                        if importlib.util.find_spec(import_name) is None:
                            missing_pkgs.append(pkg)
                    if missing_pkgs:
                        results.append(_doctor_row(
                            f"deps:{exp_name}", "warning",
                            f"Missing: {', '.join(missing_pkgs)}",
                        ))
                    else:
                        results.append(_doctor_row(
                            f"deps:{exp_name}", "ok",
                            "All dependencies installed",
                        ))
        else:
            results.append(_doctor_row("experiments", "warning", "No experiments found"))

    # Check experiments list in root README vs filesystem
    if root_readme.is_file() and exp_dir.is_dir():
        listed_entries = get_experiment_list(root_readme)
        listed = {e["name"] for e in listed_entries if isinstance(e, dict) and "name" in e}
        on_disk = {c.name for c in exp_dir.iterdir() if c.is_dir() and validate_experiment_name(c.name)}
        entries_by_name = {e["name"]: e for e in listed_entries if isinstance(e, dict)}

        unlisted = sorted(on_disk - listed)
        missing = sorted(listed - on_disk)

        if not unlisted and not missing:
            results.append(_doctor_row(
                "experiments_list", "ok",
                f"README tracks {len(listed)} experiment(s), all match filesystem",
            ))
        else:
            if unlisted:
                results.append(_doctor_row(
                    "experiments_list", "warning",
                    f"On disk but not in README: {', '.join(unlisted)}",
                ))
            if missing:
                for m in missing:
                    entry = entries_by_name.get(m, {})
                    source = entry.get("source", "")
                    if source:
                        results.append(_doctor_row(
                            "experiments_list", "warning",
                            f"'{m}' in README (from {source}) but not on disk — reinstall or remove from README",
                        ))
                    else:
                        results.append(_doctor_row(
                            "experiments_list", "warning",
                            f"'{m}' in README but not on disk — recreate or remove from README",
                        ))

        # Check source consistency: URL source but no .git dir means it's local
        for name_on_disk in sorted(on_disk & listed):
                entry = entries_by_name.get(name_on_disk, {})
                source = entry.get("source", "")
                has_own_git = (exp_dir / name_on_disk / ".git").is_dir()
                if source and not has_own_git:
                    results.append(_doctor_row(
                        "experiment_source", "warning",
                        f"'{name_on_disk}' has source '{source}' but no .git dir — should have no source",
                    ))

    cred_path = credentials_path(resolved)
    # Read at check time (not leap.config snapshot) so `ADMIN_PASSWORD=… leap doctor` matches server behavior.
    admin_pw_env = os.environ.get("ADMIN_PASSWORD", "").strip()
    if cred_path.is_file():
        results.append(_doctor_row("credentials", "ok", str(cred_path)))
    elif admin_pw_env:
        results.append(
            _doctor_row(
                "credentials",
                "ok",
                "admin_credentials.json missing — ADMIN_PASSWORD is set (file will be created on `leap run`)",
            )
        )
    else:
        results.append(
            _doctor_row("credentials", "warning", "admin_credentials.json missing")
        )

    for pkg_name in ("fastapi", "uvicorn", "sqlalchemy", "duckdb", "typer"):
        try:
            __import__(pkg_name)
            results.append(_doctor_row(f"package:{pkg_name}", "ok", "importable"))
        except ImportError:
            results.append(_doctor_row(f"package:{pkg_name}", "error", "not installed"))

    return results


def export_logs_fn(
    experiment: str,
    fmt: str = "jsonlines",
    output: Path | None = None,
    root: Path | None = None,
) -> int:
    """Export all logs for an experiment. Returns number of rows exported."""
    from leap.core import storage

    with _experiment_session(experiment, root) as (_exp_info, session):
        all_logs = storage.query_all_logs(session)

    if not all_logs:
        return 0

    if output:
        fh = open(output, "w", encoding="utf-8", newline="")
    else:
        fh = sys.stdout

    try:
        if fmt == "csv":
            columns = ["id", "ts", "student_id", "experiment", "trial", "func_name", "args", "result", "error"]
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for log in all_logs:
                row = dict(log)
                row["args"] = json.dumps(row.get("args"))
                row["result"] = json.dumps(row.get("result"))
                writer.writerow(row)
        else:
            for log in all_logs:
                fh.write(json.dumps(log, default=str) + "\n")
    finally:
        if output and fh is not sys.stdout:
            fh.close()

    return len(all_logs)


def remove_experiment_fn(
    name: str,
    root: Path | None = None,
) -> Path:
    """Remove an experiment directory and its README tracking entry. Returns the removed path."""
    from leap.core.experiment import validate_experiment_name, remove_experiment_entry

    resolved = _resolve_root(root)
    if not validate_experiment_name(name):
        raise typer.BadParameter(f"Invalid experiment name '{name}'.")

    exp_path = resolved / "experiments" / name
    if not exp_path.is_dir():
        raise typer.BadParameter(f"Experiment '{name}' not found at {exp_path}")

    shutil.rmtree(exp_path)

    _remove_gitignore_entry(resolved, name)

    root_readme = resolved / "README.md"
    if root_readme.is_file():
        remove_experiment_entry(root_readme, name)

    return exp_path


def install_experiment_fn(
    url: str,
    name: str | None = None,
    root: Path | None = None,
) -> tuple[str, Path, bool]:
    """Clone or update an experiment from a Git URL into experiments/.

    Returns (experiment_name, experiment_path, updated).
    """
    from leap.core.experiment import validate_experiment_name

    resolved = _resolve_root(root)
    exp_base = resolved / "experiments"
    exp_base_existed = exp_base.exists()
    exp_base.mkdir(parents=True, exist_ok=True)

    if not name:
        parsed = urlparse(url)
        repo_name = parsed.path.rstrip("/").split("/")[-1]
        repo_name = re.sub(r"\.git$", "", repo_name)
        name = repo_name.lower().replace(" ", "-")

    if not validate_experiment_name(name):
        raise typer.BadParameter(
            f"Derived experiment name '{name}' is invalid. "
            "Use --name to specify a valid name ([a-z0-9][a-z0-9_-]*)."
        )

    dest = exp_base / name
    updating = False

    if dest.exists():
        if not (dest / ".git").is_dir():
            raise typer.BadParameter(
                f"Experiment '{name}' exists but was not installed from a remote. "
                "Cannot update."
            )
        if not typer.confirm(
            f"Experiment '{name}' already exists. Update from remote?",
            default=True,
        ):
            raise typer.Abort()
        try:
            subprocess.run(
                ["git", "pull"],
                cwd=str(dest),
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            raise typer.BadParameter("git is not installed or not on PATH")
        except subprocess.CalledProcessError as e:
            raise typer.BadParameter(f"git pull failed: {e.stderr.strip()}")
        updating = True
    else:
        parsed_url = urlparse(url)
        if parsed_url.scheme not in ("https", "http", "git", "ssh", ""):
            raise typer.BadParameter(f"Unsupported URL scheme: '{parsed_url.scheme}'")

        try:
            subprocess.run(
                ["git", "clone", url, str(dest)],
                check=True,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            raise typer.BadParameter("git is not installed or not on PATH")
        except subprocess.CalledProcessError as e:
            raise typer.BadParameter(f"git clone failed: {e.stderr.strip()}")

        # Detect if cloned repo is a lab rather than an experiment
        from leap.core.experiment import parse_frontmatter
        cloned_readme = dest / "README.md"
        if cloned_readme.is_file():
            cloned_fm = parse_frontmatter(cloned_readme)
            if cloned_fm.get("type") == "lab":
                shutil.rmtree(dest)
                # Clean up experiments/ dir if we created it
                if not exp_base_existed and exp_base.exists() and not any(exp_base.iterdir()):
                    exp_base.rmdir()
                raise LabDetectedError(name, url)

        # Experiment URL cloned outside a lab — clean up and error
        if not is_lab_root(resolved):
            shutil.rmtree(dest)
            if not exp_base_existed and exp_base.exists() and not any(exp_base.iterdir()):
                exp_base.rmdir()
            raise typer.BadParameter(
                "This directory is not an initialized LEAP lab. "
                "Run 'leap init' first, then retry."
            )

    req_file = dest / "requirements.txt"
    if req_file.is_file():
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info("Installed dependencies from %s", req_file)
        except subprocess.CalledProcessError as e:
            logger.warning("pip install failed for %s: %s", name, e.stderr.strip())

    # Add to .gitignore so the lab's git doesn't track nested repos
    _add_gitignore_entry(resolved, name)

    # Track in root README
    root_readme = resolved / "README.md"
    if root_readme.is_file():
        from leap.core.experiment import add_experiment_entry
        add_experiment_entry(root_readme, name, source=url)

    return name, dest, updating


def discover_registry_fn(
    tag: str | None = None,
    entry_type: str | None = None,
    author: str | None = None,
    organization: str | None = None,
) -> list[dict]:
    """Fetch the leaplive registry and return entries, optionally filtered."""
    try:
        response = requests.get(REGISTRY_URL, timeout=10)
        response.raise_for_status()
    except Exception as exc:
        raise typer.BadParameter(f"Failed to fetch registry: {exc}")

    entries = yaml.safe_load(response.text)
    if not entries:
        return []

    if tag:
        tag_lower = tag.lower()
        entries = [
            e for e in entries
            if any(t.lower() == tag_lower for t in e.get("tags", []))
        ]

    if entry_type:
        entries = [e for e in entries if e.get("type", "") == entry_type]

    if author:
        author_lower = author.lower()
        def _match_author(e):
            a = e.get("authors", e.get("author", []))
            vals = a if isinstance(a, list) else [a] if a else []
            return any(author_lower in v.lower() for v in vals)
        entries = [e for e in entries if _match_author(e)]

    if organization:
        org_lower = organization.lower()
        def _match_org(e):
            o = e.get("organizations", e.get("organization", []))
            vals = o if isinstance(o, list) else [o] if o else []
            return any(org_lower in v.lower() for v in vals)
        entries = [e for e in entries if _match_org(e)]

    return entries


def publish_fn(
    experiment: str | None = None,
    root: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Publish an experiment or lab to the leaplive registry. Returns status dict."""
    from leap.core.experiment import parse_frontmatter, update_frontmatter_field

    if experiment:
        # Publishing a single experiment within a lab
        resolved = _resolve_root(root)
        exp_path = resolved / "experiments" / experiment
        if not exp_path.is_dir():
            raise typer.BadParameter(f"Experiment '{experiment}' not found at {exp_path}")
        readme_path = exp_path / "README.md"
        publish_dir = exp_path
    else:
        # Publishing from current directory (or root override)
        publish_dir = Path(root) if root else Path.cwd()
        readme_path = publish_dir / "README.md"

    fm = parse_frontmatter(readme_path)

    # Resolve repository: frontmatter → git remote
    repository = fm.get("repository", "")
    if not repository:
        repository = _get_git_remote(publish_dir)

    # Validate required fields
    missing = []
    name = fm.get("name", "") or experiment or ""
    entry_type = fm.get("type", "experiment")
    description = fm.get("description", "")
    if not description:
        missing.append("description")
    if not repository:
        missing.append("repository")
    if missing:
        raise typer.BadParameter(f"Missing required fields: {', '.join(missing)}")

    # Verify repository is reachable
    try:
        proc = subprocess.run(
            ["git", "ls-remote", repository],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            raise typer.BadParameter(
                f"Repository '{repository}' is not reachable. "
                "Push your code first, then try again."
            )
    except FileNotFoundError:
        raise typer.BadParameter("git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise typer.BadParameter(
            f"Repository '{repository}' timed out. Check the URL and try again."
        )

    # Check local repo is clean and pushed
    if (publish_dir / ".git").is_dir():
        git_dir = publish_dir
    elif experiment:
        git_dir = _resolve_root(root)
    else:
        git_dir = publish_dir
    try:
        status = subprocess.run(
            ["git", "-C", str(git_dir), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        if status.returncode == 0 and status.stdout.strip():
            raise typer.BadParameter(
                "You have uncommitted changes. Commit and push before publishing."
            )
        unpushed = subprocess.run(
            ["git", "-C", str(git_dir), "log", "--oneline", "@{u}..HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if unpushed.returncode == 0 and unpushed.stdout.strip():
            raise typer.BadParameter(
                "You have unpushed commits. Push to remote before publishing."
            )
    except FileNotFoundError:
        pass  # git not found — already caught above

    # Write repository back to frontmatter if it was missing
    if not fm.get("repository", "") and repository:
        if update_frontmatter_field(readme_path, "repository", repository):
            try:
                subprocess.run(
                    ["git", "-C", str(git_dir), "add", str(readme_path)],
                    capture_output=True, text=True, timeout=5,
                )
                subprocess.run(
                    ["git", "-C", str(git_dir), "commit", "-m",
                     f"chore: add repository URL to {name} frontmatter"],
                    capture_output=True, text=True, timeout=5,
                )
            except Exception:
                pass  # non-fatal — user can commit manually

    # Check if already in the registry
    try:
        resp = requests.get(REGISTRY_URL, timeout=10)
        resp.raise_for_status()
        registry = yaml.safe_load(resp.text) or []
        existing = [e for e in registry if e.get("name", "").lower() == name.lower()]
        if existing:
            raise typer.BadParameter(
                f"'{name}' is already in the registry. "
                "Use 'leap publish --update' or edit the registry entry directly."
            )
    except requests.RequestException:
        pass  # registry unreachable — continue, the issue review will catch duplicates

    # Check for an open issue already requesting this entry
    if shutil.which("gh"):
        try:
            proc = subprocess.run(
                ["gh", "issue", "list",
                 "--repo", REGISTRY_REPO,
                 "--search", f"Add {entry_type}: {name} in:title",
                 "--state", "open",
                 "--json", "url,title",
                 "--limit", "5"],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode == 0:
                import json as _json
                issues = _json.loads(proc.stdout) if proc.stdout.strip() else []
                # Exact title match
                exact = [i for i in issues if i.get("title") == f"Add {entry_type}: {name}"]
                if exact:
                    raise typer.BadParameter(
                        f"An open publish request already exists: {exact[0]['url']}"
                    )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass  # non-fatal — proceed with submission

    result = {
        "name": name,
        "repository": repository,
        "issue_url": None,
        "status": "pending",
    }

    if dry_run:
        result["status"] = "dry_run"
        return result

    manual_url = f"https://github.com/{REGISTRY_REPO}/issues/new"

    if shutil.which("gh") is None:
        result["status"] = "no_gh"
        result["manual_url"] = manual_url
        return result

    entry = {
        "name": name,
        "type": entry_type,
        "display_name": fm.get("display_name", "") or name,
        "description": description,
        "version": fm.get("version", ""),
        "authors": fm.get("authors", fm.get("author", [])),
        "organizations": fm.get("organizations", fm.get("organization", [])),
        "repository": repository,
        "tags": fm.get("tags", []),
    }
    body = "```yaml\n" + yaml.dump([entry], default_flow_style=False, sort_keys=False) + "```"

    try:
        proc = subprocess.run(
            ["gh", "issue", "create",
             "--repo", REGISTRY_REPO,
             "--title", f"Add {entry_type}: {name}",
             "--body", body],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            issue_url = proc.stdout.strip()
            result["issue_url"] = issue_url
            result["status"] = "submitted"
        else:
            result["status"] = "gh_error"
            result["error"] = proc.stderr.strip()
            result["manual_url"] = manual_url
    except Exception as exc:
        result["status"] = "gh_error"
        result["error"] = str(exc)
        result["manual_url"] = manual_url

    return result


publish_experiment_fn = publish_fn


@app.command()
def set_password(
    root: Optional[Path] = typer.Option(None, help="Project root override"),
):
    """Set or update the admin password."""
    set_password_fn(root)


@app.command("init")
def init_command(
    password: bool = typer.Option(
        False,
        "--password",
        help="Set password even if admin credentials already exist.",
    ),
    skip_password: bool = typer.Option(
        False,
        "--skip-password",
        help="Do not prompt for password (set ADMIN_PASSWORD or run leap set-password later).",
    ),
):
    """Initialize the current directory as a LEAP lab root."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    console = Console()

    try:
        results = init_fn(force_password=password, skip_password=skip_password)
    except typer.BadParameter as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    console.print()

    def _icon(status: str) -> Text:
        if status == "created":
            return Text("\u2713 created", style="green")
        if status == "updated":
            return Text("\u2713 updated", style="yellow")
        if status == "set":
            return Text("\u2713 set", style="green")
        if status == "skipped":
            return Text("- skipped", style="yellow")
        return Text("\u2022 exists", style="dim")

    # ── Structure items ──
    items = []
    for key in ["experiments", "config", ".gitignore"]:
        status = results.get(key, "")
        if status:
            items.append((key, status))

    readme_status = results.get("readme", "")
    if readme_status:
        items.append(("README.md", "exists" if readme_status == "skipped" else readme_status))

    if results.get("repository"):
        items.append(("repository", "created"))

    if results.get("experiments_synced"):
        items.append((f"experiments synced ({results['experiments_synced']})", "updated"))

    if results.get("deps_installed"):
        items.append((f"dependencies ({results['deps_installed']})", "created"))

    if results.get("experiments_reinstalled"):
        items.append((f"reinstalled ({results['experiments_reinstalled']})", "created"))

    pw = results.get("password", "")
    if pw:
        items.append(("password", pw))

    # ── Print as a checklist ──
    lines = []
    for label, status in items:
        icon = _icon(status)
        lines.append(f"  {icon}  [cyan]{label}[/cyan]" if status in ("created", "set") else f"  {icon}  {label}")

    # Use direct prints for the checklist
    for line in lines:
        console.print(line)

    console.print()

    if pw == "skipped":
        console.print("  [yellow]Set ADMIN_PASSWORD or run `leap set-password` before serving.[/yellow]")
        console.print()

    console.print(Panel(
        "[bold green]Ready![/bold green]  Run [cyan]leap run[/cyan] to start the server.",
        border_style="green",
        padding=(0, 2),
    ))


@app.command()
def run(
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(9000, help="Port"),
    root: Optional[Path] = typer.Option(None, help="Project root override"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable request access logs"),
):
    """Start the LEAP2 server."""
    import uvicorn
    from leap.main import create_app

    resolved = _resolve_root(root)

    # Verify project is initialized
    if not is_lab_root(resolved):
        typer.echo(
            "Error: This directory is not an initialized LEAP lab.\n"
            "Run 'leap init' first to set up the project.",
            err=True,
        )
        raise typer.Exit(1)
    if not (resolved / "experiments").is_dir():
        typer.echo(
            "Error: No experiments/ directory found.\n"
            "Run 'leap init' to set up the project structure.",
            err=True,
        )
        raise typer.Exit(1)

    import logging as _logging
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from leap.core.experiment import discover_experiments, parse_frontmatter

    console = Console()

    # ── Banner ──
    console.print()
    console.print(Panel(
        f"[bold]LEAP2[/bold]  [dim]v{__version__}[/dim]",
        subtitle=f"[dim]{resolved}[/dim]",
        border_style="bright_blue",
        padding=(0, 2),
    ))

    # ── Lab metadata ──
    root_readme = resolved / "README.md"
    if root_readme.is_file():
        fm = parse_frontmatter(root_readme)
        lab_name = fm.get("display_name") or fm.get("name") or resolved.name
        if lab_name:
            from leap.core.experiment import _as_list

            lab_lines = []
            lab_desc = fm.get("description", "")
            if lab_desc:
                lab_lines.append(f"[dim]Description:[/dim]    {lab_desc}")

            authors = _as_list(fm.get("authors", fm.get("author", [])))
            orgs = _as_list(fm.get("organizations", fm.get("organization", [])))
            if authors:
                lab_lines.append(f"[dim]Authors:[/dim]        [bold white]{', '.join(authors)}[/bold white]")
            if orgs:
                lab_lines.append(f"[dim]Organizations:[/dim]  {', '.join(orgs)}")

            tags = fm.get("tags", [])
            if tags:
                tag_str = "  ".join(f"[cyan]#{t}[/cyan]" for t in tags)
                lab_lines.append(f"[dim]Tags:[/dim]           {tag_str}")

            repo = fm.get("repository", "")
            if repo:
                short = repo.replace("https://", "").replace("http://", "").removesuffix(".git")
                lab_lines.append(f"[dim]Repository:[/dim]     [underline]{short}[/underline]")

            db_backend = fm.get("db", "")
            if db_backend:
                lab_lines.append(f"[dim]Database:[/dim]       [yellow]{db_backend}[/yellow]")

            body = "\n".join(lab_lines) if lab_lines else ""
            console.print(Panel(
                body,
                title=f"[bold green]Lab:[/bold green] [bold bright_white]{lab_name}[/bold bright_white]",
                border_style="green",
                padding=(0, 2),
                expand=False,
            ))

    # ── Discover experiments (suppress logger since we print a rich table) ──
    _exp_logger = _logging.getLogger("leap.core.experiment")
    _prev_level = _exp_logger.level
    _exp_logger.setLevel(_logging.WARNING)
    exps = discover_experiments(resolved)
    _exp_logger.setLevel(_prev_level)

    if exps:
        table = Table(
            show_header=True,
            header_style="bold",
            border_style="dim",
            padding=(0, 1),
            title="Experiments",
            title_style="bold",
        )
        table.add_column("", width=2, no_wrap=True)
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Funcs", justify="right")
        table.add_column("UI", justify="center", width=3)
        table.add_column("Description", max_width=44, overflow="ellipsis")

        for name, info in exps.items():
            fn_count = len(info.functions)
            has_ui = info.ui_dir.is_dir() and any(info.ui_dir.iterdir())
            desc = info.description or ""
            table.add_row(
                Text("\u2713", style="green"),
                info.display_name if info.display_name != name else name,
                str(fn_count),
                Text("\u2713", style="green") if has_ui else Text("\u2014", style="dim"),
                Text(desc, style="dim") if desc else Text("\u2014", style="dim"),
            )

        console.print(table)
    else:
        console.print("  [yellow]No experiments found.[/yellow] Add one with [cyan]leap new[/cyan] or [cyan]leap install[/cyan].")

    # ── Server start ──
    console.print()
    console.print(f"  [bold green]\u25b6[/bold green]  [bold]http://{host}:{port}[/bold]  [dim]Press Ctrl+C to stop[/dim]")
    console.print()

    # Suppress duplicate discovery logs from create_app lifespan
    _main_logger = _logging.getLogger("leap.main")
    _main_logger.setLevel(_logging.WARNING)
    _exp_logger.setLevel(_logging.WARNING)

    the_app = create_app(root=resolved)
    uvicorn.run(
        the_app,
        host=host,
        port=port,
        access_log=verbose,
        loop="uvloop",
        http="httptools",
    )


@app.command()
def version():
    """Show LEAP2 version."""
    typer.echo(f"LEAP2 v{__version__}")


@app.command()
def add_student(
    experiment: str = typer.Argument(..., help="Experiment name"),
    student_id: str = typer.Argument(..., help="Student ID"),
    name: Optional[str] = typer.Option(None, help="Student display name"),
    root: Optional[Path] = typer.Option(None, help="Project root override"),
):
    """Add a student to an experiment."""
    result = add_student_fn(experiment, student_id, name, root)
    typer.echo(f"Added student '{result['student_id']}' to '{experiment}'")


@app.command("import-students")
def import_students(
    experiment: str = typer.Argument(..., help="Experiment name"),
    csv_file: Path = typer.Argument(..., help="Path to CSV file"),
    root: Optional[Path] = typer.Option(None, help="Project root override"),
):
    """Bulk-import students from a CSV file."""
    try:
        result = import_students_fn(experiment, csv_file, root)
    except typer.BadParameter as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(
        f"Added: {len(result['added'])} | "
        f"Skipped: {len(result['skipped'])} (duplicates) | "
        f"Errors: {len(result['errors'])}"
    )
    for err in result["errors"]:
        typer.echo(f"  Error: student_id={err['student_id']!r} — {err['error']}")


@app.command()
def list_students(
    experiment: str = typer.Argument(..., help="Experiment name"),
    root: Optional[Path] = typer.Option(None, help="Project root override"),
):
    """List students in an experiment."""
    students = list_students_fn(experiment, root)
    if not students:
        typer.echo("No students registered.")
        return
    typer.echo(f"{'Student ID':<20} {'Name':<30} {'Email'}")
    typer.echo("-" * 70)
    for s in students:
        typer.echo(f"{s['student_id']:<20} {s['name']:<30} {s.get('email') or ''}")


def _is_url(s: str) -> bool:
    """Return True if *s* looks like a Git URL rather than an experiment name."""
    if "://" in s or s.endswith(".git"):
        return True
    return s.startswith(("github.com/", "gitlab.com/", "bitbucket.org/"))


def _is_local_path(s: str) -> bool:
    """Return True if *s* looks like a local filesystem path."""
    return os.sep in s or s.startswith(".") or s.startswith("~")


def copy_experiment_fn(
    src: str,
    name: str | None = None,
    root: Path | None = None,
) -> tuple[str, Path]:
    """Copy an experiment from a local directory into experiments/.

    Validates that the source has a README with type: experiment frontmatter.
    Returns (experiment_name, experiment_path).
    """
    from leap.core.experiment import parse_frontmatter, validate_experiment_name

    src_path = Path(src).expanduser().resolve()
    if not src_path.is_dir():
        raise typer.BadParameter(f"Path '{src}' is not a directory.")

    readme = src_path / "README.md"
    if not readme.is_file():
        raise typer.BadParameter(
            f"No README.md found in '{src_path}'. "
            "A valid experiment must have a README.md with type: experiment frontmatter."
        )

    fm = parse_frontmatter(readme)
    if fm.get("type") != "experiment":
        raise typer.BadParameter(
            f"'{src_path}' is not an experiment (type: '{fm.get('type')}'). "
            "Only experiments can be added to a lab."
        )

    exp_name = name or fm.get("name") or src_path.name.lower().replace(" ", "-")
    if not validate_experiment_name(exp_name):
        raise typer.BadParameter(
            f"Derived experiment name '{exp_name}' is invalid. "
            "Use --name to specify a valid name ([a-z0-9][a-z0-9_-]*)."
        )

    resolved = _resolve_root(root)
    exp_base = resolved / "experiments"
    exp_base.mkdir(parents=True, exist_ok=True)
    dest = exp_base / exp_name

    if dest.exists():
        raise typer.BadParameter(
            f"Experiment '{exp_name}' already exists at {dest}."
        )

    def _ignore_git(directory, contents):
        return [".git"] if ".git" in contents else []

    shutil.copytree(src_path, dest, ignore=_ignore_git)

    # Track in root README
    root_readme = resolved / "README.md"
    if root_readme.is_file():
        from leap.core.experiment import add_experiment_entry
        add_experiment_entry(root_readme, exp_name, source="")

    return exp_name, dest


def _handle_lab_add(url: str, name: str):
    """Handle adding a lab — clone into cwd or error if inside a lab/experiment."""
    cwd = Path.cwd()

    # Check if inside a lab or under a lab's directory tree
    if is_lab_root(cwd) or any(is_lab_root(p) for p in cwd.parents):
        typer.echo(
            f"Error: Cannot add lab '{name}' — you are inside another lab.\n"
            f"Run this command from a directory above, or: git clone {url}",
            err=True,
        )
        raise typer.Exit(1)

    # Plain directory — clone here
    dest = cwd / name
    if dest.exists():
        typer.echo(f"Error: Directory '{name}' already exists.", err=True)
        raise typer.Exit(1)

    try:
        subprocess.run(
            ["git", "clone", url, str(dest)],
            check=True, capture_output=True, text=True,
        )
    except FileNotFoundError:
        typer.echo("Error: git is not installed or not on PATH.", err=True)
        raise typer.Exit(1)
    except subprocess.CalledProcessError as e:
        typer.echo(f"Error: git clone failed: {e.stderr.strip()}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Cloned lab '{name}' to {dest}")
    typer.echo(f"Next: cd {name} && leap init")


@app.command("add")
def add_experiment(
    name_or_url: str = typer.Argument(..., help="Experiment name, Git URL, or local path"),
    name: Optional[str] = typer.Option(None, "--name", help="Override experiment name"),
    root: Optional[Path] = typer.Option(None, help="Project root override"),
    no_prompt: bool = typer.Option(False, "--no-prompt", help="Skip interactive prompts, use defaults"),
):
    """Add an experiment or lab — scaffold, clone from a Git URL, or copy from a local path."""
    # Normalize bare host URLs (e.g. github.com/owner/repo → https://github.com/owner/repo)
    if _is_url(name_or_url) and "://" not in name_or_url and not name_or_url.endswith(".git"):
        name_or_url = "https://" + name_or_url

    # Non-URL paths (scaffold or local copy) require an initialized lab
    if not _is_url(name_or_url) and not is_lab_root(_resolve_root(root)):
        typer.echo(
            "Error: This directory is not an initialized LEAP lab.\n"
            "Run 'leap init' first to set up the project.",
            err=True,
        )
        raise typer.Exit(1)

    if _is_url(name_or_url):
        try:
            exp_name, exp_path, updated = install_experiment_fn(name_or_url, name, root)
        except LabDetectedError as e:
            _handle_lab_add(e.url, e.name)
            return
        except typer.Abort:
            typer.echo("Aborted.")
            raise typer.Exit(0)
        except typer.BadParameter as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)

        if updated:
            typer.echo(f"Updated experiment '{exp_name}' at {exp_path}")
        else:
            typer.echo(f"Installed experiment '{exp_name}' at {exp_path}")

        _validate_and_report(exp_name, root)
    elif _is_local_path(name_or_url):
        try:
            exp_name, exp_path = copy_experiment_fn(name_or_url, name, root)
        except typer.BadParameter as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)

        typer.echo(f"Copied experiment '{exp_name}' from {name_or_url} to {exp_path}")

        _validate_and_report(exp_name, root)
    else:
        try:
            exp_path = new_experiment_fn(name_or_url, root, interactive=not no_prompt)
        except typer.BadParameter as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)
        typer.echo(f"\nCreated experiment '{name_or_url}' at {exp_path}")
        typer.echo("Next steps:")
        typer.echo(f"  1. Edit experiments/{name_or_url}/funcs/functions.py")
        typer.echo(f"  2. Edit experiments/{name_or_url}/README.md")
        typer.echo(f"  3. Restart server or reload functions")


@app.command("remove")
def remove_experiment(
    name: str = typer.Argument(..., help="Experiment name to remove"),
    root: Optional[Path] = typer.Option(None, help="Project root override"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Remove an experiment from the lab."""
    resolved = _resolve_root(root)
    exp_path = resolved / "experiments" / name
    if not exp_path.is_dir():
        typer.echo(f"Error: Experiment '{name}' not found at {exp_path}", err=True)
        raise typer.Exit(1)

    if not yes:
        typer.confirm(f"Remove experiment '{name}' at {exp_path}? This deletes the directory.", abort=True)

    try:
        remove_experiment_fn(name, root)
    except typer.BadParameter as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Removed experiment '{name}'.")


@app.command("list")
def list_exps(
    root: Optional[Path] = typer.Option(None, help="Project root override"),
):
    """List discovered experiments."""
    exps = list_experiments_fn(root)
    if not exps:
        typer.echo("No experiments found.")
        return
    typer.echo(f"{'Name':<20} {'Display Name':<25} {'Funcs':>5}  {'Registration'}")
    typer.echo("-" * 75)
    for e in exps:
        reg = "required" if e["require_registration"] else "open"
        typer.echo(f"{e['name']:<20} {e['display_name']:<25} {e['functions']:>5}  {reg}")


@app.command("validate")
def validate_exp(
    name: str = typer.Argument(..., help="Experiment name"),
    root: Optional[Path] = typer.Option(None, help="Project root override"),
):
    """Validate an experiment (README, funcs, entry_point)."""
    results = validate_experiment_fn(name, root)
    if _print_validation_results(results):
        raise typer.Exit(1)
    typer.echo("Validation passed.")


@app.command("config")
def show_config(
    root: Optional[Path] = typer.Option(None, help="Project root override"),
):
    """Show resolved LEAP2 configuration."""
    cfg = show_config_fn(root)
    typer.echo(f"Root:               {cfg['root']}")
    typer.echo(f"Experiments dir:    {cfg['experiments_dir']} ({cfg['experiment_count']} found)")
    typer.echo(f"Config dir:         {cfg['config_dir']}")
    typer.echo(f"Credentials:        {cfg['credentials_path']} ({'exists' if cfg['credentials_exist'] else 'MISSING'})")
    typer.echo(f"UI dir:             {cfg['ui_dir']}")
    typer.echo(f"DEFAULT_EXPERIMENT: {cfg['default_experiment']}")
    typer.echo(f"SESSION_SECRET_KEY: {'set' if cfg['session_secret_set'] else 'not set'}")
    typer.echo(f"ADMIN_PASSWORD env: {'set' if cfg['admin_password_env_set'] else 'not set'}")


@app.command("doctor")
def doctor(
    root: Optional[Path] = typer.Option(None, help="Project root override"),
):
    """Validate LEAP2 setup (Python, packages, directories)."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    results = doctor_fn(root)
    errors = 0
    warnings = 0

    table = Table(title="LEAP2 doctor", show_header=True, header_style="bold")
    table.add_column("Status", justify="center", style="bold", no_wrap=True, width=10)
    table.add_column("Check", style="cyan", no_wrap=True)
    table.add_column("Message")
    table.add_column("Fix", overflow="fold")

    for r in results:
        if r["status"] == "ok":
            status = Text("✓ OK", style="green")
        elif r["status"] == "warning":
            status = Text("! WARN", style="yellow")
            warnings += 1
        else:
            status = Text("✗ FAIL", style="red")
            errors += 1
        hint = (r.get("hint") or "").strip()
        fix_cell = Text("—", style="dim") if not hint else hint
        table.add_row(status, r["check"], r["message"], fix_cell)

    console = Console()
    console.print(table)
    console.print()

    # Interactive resolution for experiments_list mismatches
    exp_list_warnings = [r for r in results if r["check"] == "experiments_list" and r["status"] == "warning"]
    if exp_list_warnings:
        from leap.core.experiment import (
            add_experiment_entry, remove_experiment_entry, get_experiment_list,
            validate_experiment_name,
        )

        resolved = _resolve_root(root)
        root_readme = resolved / "README.md"
        exp_dir = resolved / "experiments"

        if root_readme.is_file() and exp_dir.is_dir():
            listed_entries = get_experiment_list(root_readme)
            listed = {e["name"] for e in listed_entries if isinstance(e, dict) and "name" in e}
            on_disk = {c.name for c in exp_dir.iterdir() if c.is_dir() and validate_experiment_name(c.name)}
            entries_by_name = {e["name"]: e for e in listed_entries if isinstance(e, dict)}

            console.print("[bold]Resolve experiment list mismatches:[/bold]")
            console.print()

            # Experiments on disk but not in README
            for name in sorted(on_disk - listed):
                source = _get_git_remote(exp_dir / name) or "local"
                if typer.confirm(f"  Add '{name}' (source: {source}) to README?", default=True):
                    add_experiment_entry(root_readme, name, source)
                    console.print(f"    Added '{name}'.")

            # Experiments in README but not on disk
            for name in sorted(listed - on_disk):
                entry = entries_by_name.get(name, {})
                source = entry.get("source", "local")
                if source and source != "local":
                    if typer.confirm(f"  '{name}' missing. Reinstall from {source}?", default=True):
                        try:
                            install_experiment_fn(source, name=name, root=resolved)
                            console.print(f"    Reinstalled '{name}'.")
                        except Exception as e:
                            console.print(f"    [red]Failed: {e}[/red]")
                            if typer.confirm(f"  Remove '{name}' from README?", default=False):
                                remove_experiment_entry(root_readme, name)
                                console.print(f"    Removed '{name}'.")
                    elif typer.confirm(f"  Remove '{name}' from README?", default=False):
                        remove_experiment_entry(root_readme, name)
                        console.print(f"    Removed '{name}'.")
                else:
                    if typer.confirm(f"  Local experiment '{name}' missing. Create empty scaffold?", default=False):
                        try:
                            new_experiment_fn(name, root=resolved, interactive=False)
                            console.print(f"    Created scaffold for '{name}'.")
                        except Exception as e:
                            console.print(f"    [red]Failed: {e}[/red]")
                    elif typer.confirm(f"  Remove '{name}' from README?", default=True):
                        remove_experiment_entry(root_readme, name)
                        console.print(f"    Removed '{name}'.")

            console.print()

    if errors:
        console.print(
            Panel(
                f"{errors} error(s), {warnings} warning(s)",
                title="Summary",
                border_style="red",
                style="red",
            )
        )
        raise typer.Exit(1)
    if warnings:
        console.print(
            Panel(
                f"All OK with {warnings} warning(s)",
                title="Summary",
                border_style="yellow",
                style="yellow",
            )
        )
    else:
        console.print(
            Panel(
                "All checks passed.",
                title="Summary",
                border_style="green",
                style="green",
            )
        )


@app.command("export")
def export_logs(
    experiment: str = typer.Argument(..., help="Experiment name"),
    fmt: str = typer.Option("jsonlines", "--format", "-f", help="Output format: jsonlines or csv"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output file (default: stdout)"),
    root: Optional[Path] = typer.Option(None, help="Project root override"),
):
    """Export all logs for an experiment to JSON Lines or CSV."""
    if fmt not in ("jsonlines", "csv"):
        typer.echo(f"Unknown format '{fmt}'. Use 'jsonlines' or 'csv'.", err=True)
        raise typer.Exit(1)
    if output is None:
        ext = "csv" if fmt == "csv" else "jsonl"
        output = Path(f"{experiment}.{ext}")
    try:
        count = export_logs_fn(experiment, fmt, output, root)
    except typer.BadParameter as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Exported {count} log(s) to {output} ({fmt})")


@app.command("discover")
def discover(
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    entry_type: Optional[str] = typer.Option(None, "--type", help="Filter by type: experiment or lab"),
    author: Optional[str] = typer.Option(None, "--author", "-a", help="Filter by author name (substring match)"),
    organization: Optional[str] = typer.Option(None, "--org", "-o", help="Filter by organization (substring match)"),
):
    """Browse experiments and labs in the leaplive registry."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text

    try:
        labs = discover_registry_fn(tag, entry_type=entry_type, author=author, organization=organization)
    except typer.BadParameter as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    console = Console()

    if not labs:
        console.print("[dim]No entries found in registry.[/dim]")
        return

    console.print(f"[bold]LEAP Registry[/bold]  [dim]({len(labs)} entries)[/dim]\n")

    for lab in labs:
        name = lab.get("name", "")
        entry_type = lab.get("type", "")
        type_color = "green" if entry_type == "lab" else "blue"

        lines = []
        desc = lab.get("description", "")
        if desc:
            lines.append(desc)
            lines.append("")

        lines.append(f"[dim]Type:[/dim]           [{type_color}]{entry_type}[/{type_color}]")

        version = lab.get("version", "")
        if version:
            lines.append(f"[dim]Version:[/dim]        {version}")

        _a = lab.get("authors", [])
        authors = ", ".join(_a) if isinstance(_a, list) else (_a or "")
        if authors:
            lines.append(f"[dim]Authors:[/dim]        [bold white]{authors}[/bold white]")

        _o = lab.get("organizations", [])
        orgs = ", ".join(_o) if isinstance(_o, list) else (_o or "")
        if orgs:
            lines.append(f"[dim]Organizations:[/dim]  {orgs}")

        tags = lab.get("tags", [])
        if tags:
            tag_str = "  ".join(f"[cyan]#{t}[/cyan]" for t in tags)
            lines.append(f"[dim]Tags:[/dim]           {tag_str}")

        repo = lab.get("repository", "")
        if repo:
            short_repo = _shorten_repo_url(repo)
            lines.append(f"[dim]Repository:[/dim]     [link={repo}][underline]{short_repo}[/underline][/link]")

        body = "\n".join(lines)
        console.print(Panel(
            body,
            title=f"[bold cyan]{name}[/bold cyan] [{type_color}]{entry_type}[/{type_color}]",
            border_style=type_color,
            padding=(0, 2),
            expand=False,
        ))
    console.print()
    console.print("[dim]Install a lab with:[/dim]  [bold]leap add <repository>[/bold]")


@app.command("publish")
def publish(
    experiment: Optional[str] = typer.Argument(None, help="Experiment name (omit to publish the lab)"),
    root: Optional[Path] = typer.Option(None, help="Project root override"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without submitting"),
):
    """Publish an experiment or lab to the leaplive registry."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from leap.core.experiment import parse_frontmatter

    if experiment:
        resolved = _resolve_root(root)
        exp_path = resolved / "experiments" / experiment
        if not exp_path.is_dir():
            typer.echo(f"Error: Experiment '{experiment}' not found", err=True)
            raise typer.Exit(1)
        readme_path = exp_path / "README.md"
    else:
        resolved = Path(root) if root else Path.cwd()
        readme_path = resolved / "README.md"

    # Run doctor checks first
    console = Console()
    results = doctor_fn(resolved)
    errors = [r for r in results if r["status"] == "error"]
    if errors:
        error_lines = "\n".join(f"  [red]✗[/red] {r['check']}: {r['message']}" for r in errors)
        console.print(Panel(
            error_lines,
            title="Doctor found errors — fix them before publishing",
            style="red",
        ))
        raise typer.Exit(1)

    fm = parse_frontmatter(readme_path)
    entry_type = fm.get("type", "experiment")
    name = fm.get("name", "") or experiment or ""

    # Preview table
    table = Table(title=f"Publishing {entry_type}", show_header=True, header_style="bold")
    table.add_column("Field", style="cyan", no_wrap=True)
    table.add_column("Value")

    table.add_row("name", Text(name, style="bold"))
    table.add_row("type", Text(entry_type))
    display_name = fm.get("display_name", "") or name
    table.add_row("display_name", Text(display_name))
    table.add_row("description", Text(fm.get("description", "") or "(missing)", style="" if fm.get("description") else "red"))
    version = fm.get("version", "")
    table.add_row("version", Text(version or "(none)", style="" if version else "dim"))
    _authors = fm.get("authors", fm.get("author", []))
    _authors_str = ", ".join(_authors) if isinstance(_authors, list) else (_authors or "")
    table.add_row("authors", Text(_authors_str or "(none)", style="" if _authors_str else "dim"))
    _orgs = fm.get("organizations", fm.get("organization", []))
    _orgs_str = ", ".join(_orgs) if isinstance(_orgs, list) else (_orgs or "")
    table.add_row("organizations", Text(_orgs_str or "(none)", style="" if _orgs_str else "dim"))
    tags = ", ".join(fm.get("tags", []))
    table.add_row("tags", Text(tags or "(none)", style="" if tags else "dim"))
    repo = fm.get("repository", "")
    table.add_row("repository", Text(repo or "(will infer from git)", style="" if repo else "yellow"))

    console.print(table)

    if not dry_run:
        typer.confirm("Submit to leaplive registry?", abort=True)

    try:
        result = publish_fn(experiment=experiment, root=root, dry_run=dry_run)
    except typer.BadParameter as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    if result["status"] == "dry_run":
        console.print(Panel(
            f"[bold]{result['name']}[/bold]\n"
            f"Repository: {result['repository']}",
            title="Dry run — no submission made",
            style="yellow",
        ))
    elif result["status"] == "no_gh":
        url = result['manual_url']
        console.print(Panel(
            "[bold]gh CLI not found.[/bold]\n"
            f"Install it from [link=https://cli.github.com]https://cli.github.com[/link]\n"
            f"Or submit manually: [link={url}]{url}[/link]",
            title="Manual submission required",
            style="yellow",
        ))
    elif result["status"] == "submitted":
        url = result['issue_url']
        console.print(Panel(
            f"Track it at: [link={url}]{url}[/link]",
            title="Submitted!",
            style="green",
        ))
    else:
        manual = result.get('manual_url', '')
        manual_line = f"\nSubmit manually: [link={manual}]{manual}[/link]" if manual else ""
        console.print(Panel(
            f"{result.get('error', 'unknown error')}{manual_line}",
            title="Failed",
            style="red",
        ))


def main():
    app()

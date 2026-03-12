"""Typer CLI for LEAP2. Shared functions used by both CLI and web API."""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import typer

from leap import __version__
from leap.config import get_root

logger = logging.getLogger(__name__)

app = typer.Typer(help="LEAP2 — Live Experiments for Active Pedagogy")


def _resolve_root(root: Path | None) -> Path:
    return root or get_root()


# ── Shared functions (importable by API routes) ──


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
    from leap.core.experiment import ExperimentInfo
    from leap.core import storage

    resolved = _resolve_root(root)
    exp_path = resolved / "experiments" / experiment
    if not exp_path.is_dir():
        raise typer.BadParameter(f"Experiment '{experiment}' not found at {exp_path}")

    exp_info = ExperimentInfo(experiment, exp_path)
    session = storage.get_session(experiment, exp_info.db_path)
    try:
        storage.add_student(session, student_id, name or student_id)
        return {"student_id": student_id, "name": name or student_id}
    finally:
        session.close()


def import_students_fn(
    experiment: str,
    csv_file: Path,
    root: Path | None = None,
) -> dict:
    """Import students from a CSV file. Returns result dict with added/skipped/errors."""
    from leap.core.experiment import ExperimentInfo
    from leap.core import storage

    resolved = _resolve_root(root)
    exp_path = resolved / "experiments" / experiment
    if not exp_path.is_dir():
        raise typer.BadParameter(f"Experiment '{experiment}' not found at {exp_path}")

    if not csv_file.is_file():
        raise typer.BadParameter(f"CSV file not found: {csv_file}")

    with open(csv_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "student_id" not in reader.fieldnames:
            raise typer.BadParameter("CSV must have a 'student_id' column header")
        rows = list(reader)

    exp_info = ExperimentInfo(experiment, exp_path)
    session = storage.get_session(experiment, exp_info.db_path)
    try:
        return storage.bulk_add_students(session, rows)
    finally:
        session.close()


def list_students_fn(experiment: str, root: Path | None = None) -> list[dict]:
    """List students in an experiment."""
    from leap.core.experiment import ExperimentInfo
    from leap.core import storage

    resolved = _resolve_root(root)
    exp_path = resolved / "experiments" / experiment
    if not exp_path.is_dir():
        raise typer.BadParameter(f"Experiment '{experiment}' not found at {exp_path}")

    exp_info = ExperimentInfo(experiment, exp_path)
    session = storage.get_session(experiment, exp_info.db_path)
    try:
        return storage.list_students(session)
    finally:
        session.close()


def init_project_fn(root: Path | None = None) -> dict[str, str]:
    """Bootstrap LEAP2 project structure. Returns {path: status} for each dir/file."""
    resolved = _resolve_root(root)
    results: dict[str, str] = {}

    dirs = [
        resolved / "experiments",
        resolved / "config",
        resolved / "ui" / "shared",
        resolved / "ui" / "landing",
    ]
    for d in dirs:
        rel = str(d.relative_to(resolved))
        if d.is_dir():
            results[rel] = "exists"
        else:
            d.mkdir(parents=True, exist_ok=True)
            results[rel] = "created"

    templates = {
        resolved / "ui" / "shared" / "theme.css": _template_theme_css,
        resolved / "ui" / "landing" / "index.html": _template_landing_html,
    }
    for path, template_fn in templates.items():
        rel = str(path.relative_to(resolved))
        if path.is_file():
            results[rel] = "exists"
        else:
            path.write_text(template_fn(), encoding="utf-8")
            results[rel] = "created"

    return results


def new_experiment_fn(name: str, root: Path | None = None) -> Path:
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

    exp_path.mkdir(parents=True)
    (exp_path / "funcs").mkdir()
    (exp_path / "ui").mkdir()
    (exp_path / "db").mkdir()

    readme = exp_path / "README.md"
    readme.write_text(
        f"---\nname: {name}\ndisplay_name: {name.replace('-', ' ').replace('_', ' ').title()}\n"
        f"description: \"\"\nentry_point: readme\nrequire_registration: true\n---\n\n"
        f"# {name.replace('-', ' ').replace('_', ' ').title()}\n\nExperiment instructions go here.\n",
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
    display = name.replace('-', ' ').replace('_', ' ').title()
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
    if not readme_path.is_file():
        results.append({"check": "readme", "status": "warning", "message": "README.md missing"})
    else:
        fm = parse_frontmatter(readme_path)
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

    # leap_version check
    fm = parse_frontmatter(readme_path) if readme_path.is_file() else {}
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


def doctor_fn(root: Path | None = None) -> list[dict]:
    """Validate overall LEAP2 setup. Returns list of {check, status, message}."""
    from leap.config import experiments_dir, credentials_path

    resolved = _resolve_root(root)
    results: list[dict] = []

    v = sys.version_info
    if v >= (3, 10):
        results.append({"check": "python", "status": "ok", "message": f"Python {v.major}.{v.minor}.{v.micro}"})
    else:
        results.append({"check": "python", "status": "error", "message": f"Python {v.major}.{v.minor} < 3.10"})

    if resolved.is_dir():
        results.append({"check": "root", "status": "ok", "message": str(resolved)})
    else:
        results.append({"check": "root", "status": "error", "message": f"Not found: {resolved}"})

    exp_dir = experiments_dir(resolved)
    if exp_dir.is_dir():
        results.append({"check": "experiments_dir", "status": "ok", "message": str(exp_dir)})
    else:
        results.append({"check": "experiments_dir", "status": "error", "message": "experiments/ not found"})

    if exp_dir.is_dir():
        exp_names = [c.name for c in sorted(exp_dir.iterdir()) if c.is_dir()]
        if exp_names:
            results.append({"check": "experiments", "status": "ok", "message": f"{len(exp_names)}: {', '.join(exp_names)}"})
        else:
            results.append({"check": "experiments", "status": "warning", "message": "No experiments found"})

    cred_path = credentials_path(resolved)
    if cred_path.is_file():
        results.append({"check": "credentials", "status": "ok", "message": str(cred_path)})
    else:
        results.append({"check": "credentials", "status": "warning", "message": "admin_credentials.json missing"})

    for pkg_name in ("fastapi", "uvicorn", "sqlalchemy", "duckdb", "typer"):
        try:
            __import__(pkg_name)
            results.append({"check": f"package:{pkg_name}", "status": "ok", "message": "importable"})
        except ImportError:
            results.append({"check": f"package:{pkg_name}", "status": "error", "message": "not installed"})

    return results


def export_logs_fn(
    experiment: str,
    fmt: str = "jsonlines",
    output: Path | None = None,
    root: Path | None = None,
) -> int:
    """Export all logs for an experiment. Returns number of rows exported."""
    from leap.core.experiment import ExperimentInfo
    from leap.core import storage

    resolved = _resolve_root(root)
    exp_path = resolved / "experiments" / experiment
    if not exp_path.is_dir():
        raise typer.BadParameter(f"Experiment '{experiment}' not found at {exp_path}")

    exp_info = ExperimentInfo(experiment, exp_path)
    session = storage.get_session(experiment, exp_info.db_path)

    try:
        all_logs: list[dict] = []
        after_id = None
        page_size = 5000

        while True:
            page = storage.query_logs(
                session,
                n=page_size,
                order="earliest",
                after_id=after_id,
            )
            if not page:
                break
            all_logs.extend(page)
            if len(page) < page_size:
                break
            after_id = page[-1]["id"]
    finally:
        session.close()

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


def install_experiment_fn(
    url: str,
    name: str | None = None,
    root: Path | None = None,
) -> tuple[str, Path]:
    """Clone an experiment from a Git URL into experiments/.

    Returns (experiment_name, experiment_path).
    """
    from leap.core.experiment import validate_experiment_name

    resolved = _resolve_root(root)
    exp_base = resolved / "experiments"
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
    if dest.exists():
        raise typer.BadParameter(f"Experiment '{name}' already exists at {dest}")

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

    return name, dest


# ── Template generators for `leap init` ──

def _template_theme_css() -> str:
    src = Path(__file__).resolve().parent.parent / "ui" / "shared" / "theme.css"
    if src.is_file():
        return src.read_text(encoding="utf-8")
    return "/* LEAP2 theme — run from repo root or copy theme.css manually */\n"


def _template_landing_html() -> str:
    src = Path(__file__).resolve().parent.parent / "ui" / "landing" / "index.html"
    if src.is_file():
        return src.read_text(encoding="utf-8")
    return "<!DOCTYPE html><html><body><h1>LEAP2</h1><p>Landing page placeholder.</p></body></html>\n"



# ── CLI commands ──


@app.command()
def set_password(
    root: Optional[Path] = typer.Option(None, help="Project root override"),
):
    """Set or update the admin password."""
    set_password_fn(root)


@app.command()
def run(
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(9000, help="Port"),
    root: Optional[Path] = typer.Option(None, help="Project root override"),
):
    """Start the LEAP2 server."""
    import uvicorn
    from leap.main import create_app

    resolved = _resolve_root(root)
    init_project_fn(resolved)
    the_app = create_app(root=resolved)
    typer.echo(f"Starting LEAP2 on {host}:{port} (root: {resolved})")
    uvicorn.run(the_app, host=host, port=port)


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


@app.command("new")
def new_experiment(
    name: str = typer.Argument(..., help="Experiment name (lowercase, digits, hyphens, underscores)"),
    root: Optional[Path] = typer.Option(None, help="Project root override"),
):
    """Create a new experiment scaffold."""
    try:
        exp_path = new_experiment_fn(name, root)
    except typer.BadParameter as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"Created experiment '{name}' at {exp_path}")
    typer.echo("Next steps:")
    typer.echo(f"  1. Edit experiments/{name}/funcs/functions.py")
    typer.echo(f"  2. Edit experiments/{name}/README.md")
    typer.echo(f"  3. Restart server or reload functions")


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
    has_errors = False
    for r in results:
        if r["status"] == "ok":
            icon = "✓"
        elif r["status"] == "warning":
            icon = "!"
        else:
            icon = "✗"
            has_errors = True
        typer.echo(f"  {icon} {r['check']}: {r['message']}")
    if has_errors:
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
    results = doctor_fn(root)
    errors = 0
    warnings = 0
    for r in results:
        if r["status"] == "ok":
            icon = "✓"
        elif r["status"] == "warning":
            icon = "!"
            warnings += 1
        else:
            icon = "✗"
            errors += 1
        typer.echo(f"  {icon} {r['check']}: {r['message']}")

    typer.echo("")
    if errors:
        typer.echo(f"{errors} error(s), {warnings} warning(s)")
        raise typer.Exit(1)
    elif warnings:
        typer.echo(f"All OK with {warnings} warning(s)")
    else:
        typer.echo("All checks passed.")


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


@app.command("install")
def install_experiment(
    url: str = typer.Argument(..., help="Git URL of the experiment repo"),
    name: Optional[str] = typer.Option(None, help="Override experiment name"),
    root: Optional[Path] = typer.Option(None, help="Project root override"),
):
    """Clone an experiment from a Git URL."""
    try:
        exp_name, exp_path = install_experiment_fn(url, name, root)
    except typer.BadParameter as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Installed experiment '{exp_name}' at {exp_path}")

    results = validate_experiment_fn(exp_name, root)
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

    if has_issues:
        typer.echo("Some checks had warnings — review above.")
    else:
        typer.echo("Validation passed.")
    typer.echo("Restart the server to load the new experiment.")


def main():
    app()

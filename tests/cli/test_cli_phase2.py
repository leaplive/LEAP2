"""Phase 2 CLI tests: init, new, list, validate, config, doctor."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from leap.cli import app, init_project_fn, new_experiment_fn, list_experiments_fn
from leap.cli import validate_experiment_fn, show_config_fn, doctor_fn

runner = CliRunner()


# ── Shared function tests ──


class TestInitProjectFn:
    def test_creates_directories(self, tmp_path):
        results = init_project_fn(root=tmp_path)
        assert (tmp_path / "experiments").is_dir()
        assert (tmp_path / "config").is_dir()
        assert (tmp_path / "ui" / "shared").is_dir()
        assert (tmp_path / "ui" / "landing").is_dir()
        assert any("created" == v for v in results.values())

    def test_idempotent(self, tmp_path):
        init_project_fn(root=tmp_path)
        results2 = init_project_fn(root=tmp_path)
        assert all(v == "exists" for v in results2.values())

    def test_creates_template_files(self, tmp_path):
        init_project_fn(root=tmp_path)
        assert (tmp_path / "ui" / "shared" / "theme.css").is_file()
        assert (tmp_path / "ui" / "landing" / "index.html").is_file()

    def test_does_not_overwrite_existing_files(self, tmp_path):
        (tmp_path / "ui" / "shared").mkdir(parents=True)
        custom = tmp_path / "ui" / "shared" / "theme.css"
        custom.write_text("/* custom */")
        init_project_fn(root=tmp_path)
        assert custom.read_text() == "/* custom */"


class TestNewExperimentFn:
    def test_creates_scaffold(self, tmp_root):
        path = new_experiment_fn("my-lab", root=tmp_root)
        assert path.is_dir()
        assert (path / "README.md").is_file()
        assert (path / "funcs" / "functions.py").is_file()
        assert (path / "ui" / "dashboard.html").is_file()
        assert (path / "db").is_dir()

    def test_readme_has_frontmatter(self, tmp_root):
        path = new_experiment_fn("test-exp", root=tmp_root)
        text = (path / "README.md").read_text()
        assert "---" in text
        assert "name: test-exp" in text
        assert "display_name:" in text

    def test_stub_function_file(self, tmp_root):
        path = new_experiment_fn("func-test", root=tmp_root)
        text = (path / "funcs" / "functions.py").read_text()
        assert "def hello" in text

    def test_rejects_invalid_name(self, tmp_root):
        import typer
        with pytest.raises(typer.BadParameter, match="Invalid"):
            new_experiment_fn("My Lab!", root=tmp_root)

    def test_rejects_uppercase(self, tmp_root):
        import typer
        with pytest.raises(typer.BadParameter, match="Invalid"):
            new_experiment_fn("MyLab", root=tmp_root)

    def test_rejects_duplicate(self, tmp_root):
        import typer
        new_experiment_fn("dup-test", root=tmp_root)
        with pytest.raises(typer.BadParameter, match="already exists"):
            new_experiment_fn("dup-test", root=tmp_root)

    def test_name_with_hyphens_underscores(self, tmp_root):
        path = new_experiment_fn("my-cool_lab2", root=tmp_root)
        assert path.is_dir()
        assert "my-cool_lab2" in path.name

    def test_dashboard_references_experiment(self, tmp_root):
        path = new_experiment_fn("viz-lab", root=tmp_root)
        html = (path / "ui" / "dashboard.html").read_text()
        assert "Viz Lab" in html


class TestListExperimentsFn:
    def test_lists_default(self, tmp_root):
        exps = list_experiments_fn(root=tmp_root)
        assert len(exps) >= 1
        names = [e["name"] for e in exps]
        assert "default" in names

    def test_experiment_metadata_shape(self, tmp_root):
        exps = list_experiments_fn(root=tmp_root)
        exp = exps[0]
        assert "name" in exp
        assert "display_name" in exp
        assert "functions" in exp
        assert "require_registration" in exp

    def test_includes_new_experiment(self, tmp_root):
        new_experiment_fn("extra-lab", root=tmp_root)
        exps = list_experiments_fn(root=tmp_root)
        names = [e["name"] for e in exps]
        assert "extra-lab" in names

    def test_empty_experiments(self, tmp_path):
        (tmp_path / "experiments").mkdir(parents=True)
        exps = list_experiments_fn(root=tmp_path)
        assert exps == []


class TestValidateExperimentFn:
    def test_valid_experiment_all_ok(self, tmp_root):
        results = validate_experiment_fn("default", root=tmp_root)
        statuses = [r["status"] for r in results]
        assert "error" not in statuses

    def test_invalid_name(self, tmp_root):
        results = validate_experiment_fn("Bad Name!", root=tmp_root)
        assert results[0]["status"] == "error"

    def test_nonexistent_experiment(self, tmp_root):
        results = validate_experiment_fn("nope", root=tmp_root)
        assert any(r["status"] == "error" for r in results)

    def test_missing_entry_point(self, tmp_root):
        exp_path = tmp_root / "experiments" / "no-ui"
        exp_path.mkdir(parents=True)
        (exp_path / "funcs").mkdir()
        (exp_path / "ui").mkdir()
        (exp_path / "README.md").write_text(
            "---\nname: no-ui\nentry_point: missing.html\n---\n"
        )
        results = validate_experiment_fn("no-ui", root=tmp_root)
        entry_check = [r for r in results if r["check"] == "entry_point"]
        assert entry_check[0]["status"] == "warning"

    def test_checks_readme_and_funcs(self, tmp_root):
        results = validate_experiment_fn("default", root=tmp_root)
        checks = [r["check"] for r in results]
        assert "readme" in checks
        assert "funcs" in checks

    def test_leap_version_check_passes(self, tmp_root):
        # Default experiment has leap_version: ">=1.0" which should pass
        (tmp_root / "experiments" / "default" / "README.md").write_text(
            "---\nname: default\nleap_version: '>=1.0'\n---\n"
        )
        results = validate_experiment_fn("default", root=tmp_root)
        ver_check = [r for r in results if r["check"] == "leap_version"]
        assert len(ver_check) == 1
        assert ver_check[0]["status"] == "ok"

    def test_leap_version_check_fails(self, tmp_root):
        (tmp_root / "experiments" / "default" / "README.md").write_text(
            "---\nname: default\nleap_version: '>=99.0'\n---\n"
        )
        results = validate_experiment_fn("default", root=tmp_root)
        ver_check = [r for r in results if r["check"] == "leap_version"]
        assert len(ver_check) == 1
        assert ver_check[0]["status"] == "error"


class TestShowConfigFn:
    def test_returns_dict(self, tmp_root):
        cfg = show_config_fn(root=tmp_root)
        assert isinstance(cfg, dict)
        assert "root" in cfg
        assert "experiments_dir" in cfg
        assert "experiment_count" in cfg

    def test_root_matches(self, tmp_root):
        cfg = show_config_fn(root=tmp_root)
        assert cfg["root"] == str(tmp_root)

    def test_experiment_count(self, tmp_root):
        cfg = show_config_fn(root=tmp_root)
        assert cfg["experiment_count"] >= 1

    def test_credentials_status(self, tmp_root):
        cfg = show_config_fn(root=tmp_root)
        assert "credentials_exist" in cfg


class TestDoctorFn:
    def test_all_ok_for_valid_setup(self, tmp_credentials):
        results = doctor_fn(root=tmp_credentials)
        statuses = [r["status"] for r in results]
        assert "error" not in statuses

    def test_checks_python_version(self, tmp_root):
        results = doctor_fn(root=tmp_root)
        python_check = [r for r in results if r["check"] == "python"]
        assert python_check[0]["status"] == "ok"

    def test_checks_packages(self, tmp_root):
        results = doctor_fn(root=tmp_root)
        pkg_checks = [r for r in results if r["check"].startswith("package:")]
        assert len(pkg_checks) >= 5
        assert all(r["status"] == "ok" for r in pkg_checks)

    def test_warns_on_missing_credentials(self, tmp_root):
        results = doctor_fn(root=tmp_root)
        cred_check = [r for r in results if r["check"] == "credentials"]
        assert cred_check[0]["status"] == "warning"

    def test_warns_on_missing_experiments(self, tmp_path):
        (tmp_path / "experiments").mkdir(parents=True)
        results = doctor_fn(root=tmp_path)
        exp_check = [r for r in results if r["check"] == "experiments"]
        assert exp_check[0]["status"] == "warning"


# ── CLI command tests (via CliRunner) ──


class TestNewCommand:
    def test_new_experiment(self, tmp_root):
        result = runner.invoke(app, ["new", "my-test", "--root", str(tmp_root)])
        assert result.exit_code == 0
        assert "Created" in result.output or "created" in result.output.lower()
        assert (tmp_root / "experiments" / "my-test").is_dir()

    def test_new_invalid_name(self, tmp_root):
        result = runner.invoke(app, ["new", "BAD NAME", "--root", str(tmp_root)])
        assert result.exit_code == 1

    def test_new_duplicate(self, tmp_root):
        runner.invoke(app, ["new", "dup-exp", "--root", str(tmp_root)])
        result = runner.invoke(app, ["new", "dup-exp", "--root", str(tmp_root)])
        assert result.exit_code == 1

    def test_new_shows_next_steps(self, tmp_root):
        result = runner.invoke(app, ["new", "steps-test", "--root", str(tmp_root)])
        assert "Next steps" in result.output


class TestListCommand:
    def test_list_experiments(self, tmp_root):
        result = runner.invoke(app, ["list", "--root", str(tmp_root)])
        assert result.exit_code == 0
        assert "default" in result.output

    def test_list_empty(self, tmp_path):
        (tmp_path / "experiments").mkdir(parents=True)
        result = runner.invoke(app, ["list", "--root", str(tmp_path)])
        assert result.exit_code == 0
        assert "No experiments" in result.output

    def test_list_shows_multiple(self, tmp_root):
        new_experiment_fn("another-one", root=tmp_root)
        result = runner.invoke(app, ["list", "--root", str(tmp_root)])
        assert "default" in result.output
        assert "another-one" in result.output


class TestValidateCommand:
    def test_validate_default(self, tmp_root):
        result = runner.invoke(app, ["validate", "default", "--root", str(tmp_root)])
        assert result.exit_code == 0
        assert "passed" in result.output.lower()

    def test_validate_nonexistent(self, tmp_root):
        result = runner.invoke(app, ["validate", "nope", "--root", str(tmp_root)])
        assert result.exit_code == 1

    def test_validate_shows_checks(self, tmp_root):
        result = runner.invoke(app, ["validate", "default", "--root", str(tmp_root)])
        assert "name" in result.output.lower()
        assert "readme" in result.output.lower()


class TestConfigCommand:
    def test_config_output(self, tmp_root):
        result = runner.invoke(app, ["config", "--root", str(tmp_root)])
        assert result.exit_code == 0
        assert "Root:" in result.output
        assert str(tmp_root) in result.output

    def test_config_shows_experiment_count(self, tmp_root):
        result = runner.invoke(app, ["config", "--root", str(tmp_root)])
        assert "found" in result.output.lower()


class TestDoctorCommand:
    def test_doctor_passes(self, tmp_credentials):
        result = runner.invoke(app, ["doctor", "--root", str(tmp_credentials)])
        assert result.exit_code == 0
        assert "passed" in result.output.lower() or "ok" in result.output.lower()

    def test_doctor_warns_without_credentials(self, tmp_root):
        result = runner.invoke(app, ["doctor", "--root", str(tmp_root)])
        assert result.exit_code == 0
        assert "missing" in result.output.lower() or "warning" in result.output.lower()

    def test_doctor_checks_python(self, tmp_root):
        result = runner.invoke(app, ["doctor", "--root", str(tmp_root)])
        assert "python" in result.output.lower()


class TestImportStudentsCommand:
    def _write_csv(self, tmp_path, filename, content):
        p = tmp_path / filename
        p.write_text(content, encoding="utf-8")
        return p

    def test_import_basic(self, tmp_root):
        csv_path = self._write_csv(
            tmp_root, "students.csv",
            "student_id,name,email\ns001,Alice,alice@u.edu\ns002,Bob,\ns003,Charlie,charlie@u.edu\n",
        )
        result = runner.invoke(app, ["import-students", "default", str(csv_path), "--root", str(tmp_root)])
        assert result.exit_code == 0
        assert "Added: 3" in result.output

    def test_import_skips_duplicates(self, tmp_root):
        # Pre-add one student
        runner.invoke(app, ["add-student", "default", "s001", "--root", str(tmp_root)])
        csv_path = self._write_csv(
            tmp_root, "students.csv",
            "student_id,name\ns001,Alice\ns002,Bob\n",
        )
        result = runner.invoke(app, ["import-students", "default", str(csv_path), "--root", str(tmp_root)])
        assert result.exit_code == 0
        assert "Added: 1" in result.output
        assert "Skipped: 1" in result.output

    def test_import_missing_header(self, tmp_root):
        csv_path = self._write_csv(
            tmp_root, "bad.csv",
            "id,name\ns001,Alice\n",
        )
        result = runner.invoke(app, ["import-students", "default", str(csv_path), "--root", str(tmp_root)])
        assert result.exit_code == 1
        assert "student_id" in result.output

    def test_import_email_optional(self, tmp_root):
        csv_path = self._write_csv(
            tmp_root, "students.csv",
            "student_id,name\ns001,Alice\ns002,Bob\n",
        )
        result = runner.invoke(app, ["import-students", "default", str(csv_path), "--root", str(tmp_root)])
        assert result.exit_code == 0
        assert "Added: 2" in result.output

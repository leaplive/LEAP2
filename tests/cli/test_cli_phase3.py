"""Phase 3 CLI tests: leap install command."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from leap.cli import app, install_experiment_fn, copy_experiment_fn

runner = CliRunner()


class TestInstallExperimentFn:
    def test_derives_name_from_url(self, tmp_root):
        with patch("leap.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            name, path, _ = install_experiment_fn(
                "https://github.com/user/my-experiment.git",
                root=tmp_root,
            )
        assert name == "my-experiment"
        assert "experiments/my-experiment" in str(path)

    def test_derives_name_strips_git_suffix(self, tmp_root):
        with patch("leap.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            name, *_ = install_experiment_fn(
                "https://github.com/user/cool-lab.git",
                root=tmp_root,
            )
        assert name == "cool-lab"

    def test_derives_name_without_git_suffix(self, tmp_root):
        with patch("leap.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            name, *_ = install_experiment_fn(
                "https://github.com/user/my-lab",
                root=tmp_root,
            )
        assert name == "my-lab"

    def test_name_override(self, tmp_root):
        with patch("leap.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            name, path, _ = install_experiment_fn(
                "https://github.com/user/whatever.git",
                name="custom-name",
                root=tmp_root,
            )
        assert name == "custom-name"
        assert "experiments/custom-name" in str(path)

    def test_rejects_invalid_derived_name(self, tmp_root):
        import typer
        with pytest.raises(typer.BadParameter, match="invalid"):
            install_experiment_fn(
                "https://github.com/user/My_Bad_Name!.git",
                root=tmp_root,
            )

    def test_rejects_duplicate_without_git(self, tmp_root):
        import typer
        with pytest.raises(typer.BadParameter, match="not installed from a remote"):
            install_experiment_fn(
                "https://github.com/user/default.git",
                root=tmp_root,
            )

    def test_git_not_found(self, tmp_root):
        import typer
        with patch("leap.cli.subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(typer.BadParameter, match="git is not installed"):
                install_experiment_fn(
                    "https://github.com/user/new-lab.git",
                    root=tmp_root,
                )

    def test_git_clone_failure(self, tmp_root):
        import typer
        err = subprocess.CalledProcessError(128, "git", stderr="fatal: repo not found")
        with patch("leap.cli.subprocess.run", side_effect=err):
            with pytest.raises(typer.BadParameter, match="git clone failed"):
                install_experiment_fn(
                    "https://github.com/user/new-lab.git",
                    root=tmp_root,
                )

    def test_creates_experiments_dir_if_missing(self, tmp_path):
        """If experiments/ doesn't exist yet, install should create it."""
        import typer
        (tmp_path / "README.md").write_text("---\nname: test\ntype: lab\n---\n")
        with patch("leap.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            name, path, _ = install_experiment_fn(
                "https://github.com/user/fresh-lab.git",
                root=tmp_path,
            )
        assert (tmp_path / "experiments").is_dir()

    def test_calls_git_with_correct_args(self, tmp_root):
        with patch("leap.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            install_experiment_fn(
                "https://github.com/user/test-lab.git",
                root=tmp_root,
            )
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "git"
        assert cmd[1] == "clone"
        assert cmd[2] == "https://github.com/user/test-lab.git"
        assert "test-lab" in cmd[3]

    def test_lowercases_name(self, tmp_root):
        with patch("leap.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            name, *_ = install_experiment_fn(
                "https://github.com/user/MyRepo.git",
                root=tmp_root,
            )
        assert name == "myrepo"

    def test_trailing_slash_in_url(self, tmp_root):
        with patch("leap.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            name, *_ = install_experiment_fn(
                "https://github.com/user/trail-lab/",
                root=tmp_root,
            )
        assert name == "trail-lab"


    def test_pip_install_when_requirements_exist(self, tmp_root):
        with patch("leap.cli.subprocess.run") as mock_run:
            def side_effect(cmd, **kwargs):
                if cmd[0] == "git":
                    dest = Path(cmd[3])
                    dest.mkdir(parents=True, exist_ok=True)
                    (dest / "requirements.txt").write_text("numpy>=1.20\nscipy\n")
                return MagicMock(returncode=0)

            mock_run.side_effect = side_effect
            install_experiment_fn(
                "https://github.com/user/dep-lab.git",
                root=tmp_root,
            )
        assert mock_run.call_count == 2
        pip_call = mock_run.call_args_list[1]
        pip_cmd = pip_call[0][0]
        assert pip_cmd[1:4] == ["-m", "pip", "install"]
        assert pip_cmd[4] == "-r"
        assert "requirements.txt" in pip_cmd[5]

    def test_no_pip_install_without_requirements(self, tmp_root):
        with patch("leap.cli.subprocess.run") as mock_run:
            def side_effect(cmd, **kwargs):
                if cmd[0] == "git":
                    dest = Path(cmd[3])
                    dest.mkdir(parents=True, exist_ok=True)
                return MagicMock(returncode=0)

            mock_run.side_effect = side_effect
            install_experiment_fn(
                "https://github.com/user/no-deps.git",
                root=tmp_root,
            )
        mock_run.assert_called_once()

    def test_pip_install_failure_does_not_abort(self, tmp_root):
        call_count = 0

        def side_effect(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if cmd[0] == "git":
                dest = Path(cmd[3])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "requirements.txt").write_text("nonexistent-pkg-xyz\n")
                return MagicMock(returncode=0)
            raise subprocess.CalledProcessError(1, "pip", stderr="No matching distribution")

        with patch("leap.cli.subprocess.run", side_effect=side_effect):
            name, path, _ = install_experiment_fn(
                "https://github.com/user/bad-deps.git",
                root=tmp_root,
            )
        assert name == "bad-deps"
        assert path.is_dir()
        assert call_count == 2


class TestInstallCommand:
    def test_install_success(self, tmp_root):
        with patch("leap.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            # Pre-create the directory since mock won't actually clone
            exp_path = tmp_root / "experiments" / "test-repo"
            # Need to handle that install_fn creates nothing (mocked git)
            # but validate_experiment_fn will run — create minimal structure
            def side_effect(cmd, **kwargs):
                dest = Path(cmd[3])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "README.md").write_text("---\nname: test-repo\n---\n")
                (dest / "funcs").mkdir()
                (dest / "ui").mkdir()
                return MagicMock(returncode=0)

            mock_run.side_effect = side_effect
            result = runner.invoke(app, [
                "add", "https://github.com/user/test-repo.git",
                "--root", str(tmp_root),
            ])
        assert result.exit_code == 0
        assert "Installed" in result.output or "installed" in result.output.lower()

    def test_install_with_name_override(self, tmp_root):
        with patch("leap.cli.subprocess.run") as mock_run:
            def side_effect(cmd, **kwargs):
                dest = Path(cmd[3])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "README.md").write_text("---\nname: custom\n---\n")
                (dest / "funcs").mkdir()
                (dest / "ui").mkdir()
                return MagicMock(returncode=0)

            mock_run.side_effect = side_effect
            result = runner.invoke(app, [
                "add", "https://github.com/user/whatever.git",
                "--name", "custom",
                "--root", str(tmp_root),
            ])
        assert result.exit_code == 0
        assert "custom" in result.output

    def test_install_duplicate_no_git(self, tmp_root):
        result = runner.invoke(app, [
            "add", "https://github.com/user/default.git",
            "--root", str(tmp_root),
        ])
        assert result.exit_code == 1
        assert "not installed from a remote" in result.output

    def test_install_shows_validation(self, tmp_root):
        with patch("leap.cli.subprocess.run") as mock_run:
            def side_effect(cmd, **kwargs):
                dest = Path(cmd[3])
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "README.md").write_text("---\nname: validated\n---\n")
                (dest / "funcs").mkdir()
                (dest / "ui").mkdir()
                return MagicMock(returncode=0)

            mock_run.side_effect = side_effect
            result = runner.invoke(app, [
                "add", "https://github.com/user/validated.git",
                "--root", str(tmp_root),
            ])
        assert "name" in result.output.lower()
        assert "Restart" in result.output


class TestInstallUpdate:
    def _make_installed_experiment(self, tmp_root, name="remote-lab"):
        """Create an experiment that looks like it was installed (has .git)."""
        exp_path = tmp_root / "experiments" / name
        exp_path.mkdir(parents=True, exist_ok=True)
        (exp_path / ".git").mkdir()
        (exp_path / "README.md").write_text(
            f"---\nname: {name}\ndisplay_name: Remote Lab\n"
            f'description: "A remote lab"\nauthor: "tester"\n'
            f"tags: [test]\n---\n\n# Test\n"
        )
        (exp_path / "funcs").mkdir()
        (exp_path / "ui").mkdir()
        return exp_path

    def test_update_existing_on_confirm(self, tmp_root):
        exp_path = self._make_installed_experiment(tmp_root)
        with patch("leap.cli.subprocess.run") as mock_run, \
             patch("leap.cli.typer.confirm", return_value=True):
            mock_run.return_value = MagicMock(returncode=0)
            name, path, updated = install_experiment_fn(
                "https://github.com/user/remote-lab.git",
                root=tmp_root,
            )
        assert updated is True
        assert name == "remote-lab"
        # Should have called git pull, not git clone
        call_args = mock_run.call_args[0][0]
        assert call_args == ["git", "pull"]

    def test_update_existing_aborts_on_decline(self, tmp_root):
        import typer
        self._make_installed_experiment(tmp_root)
        with patch("leap.cli.subprocess.run"), \
             patch("leap.cli.typer.confirm", side_effect=typer.Abort()):
            with pytest.raises(typer.Abort):
                install_experiment_fn(
                    "https://github.com/user/remote-lab.git",
                    root=tmp_root,
                )

    def test_update_reinstalls_requirements(self, tmp_root):
        exp_path = self._make_installed_experiment(tmp_root)
        (exp_path / "requirements.txt").write_text("numpy>=1.20\n")
        with patch("leap.cli.subprocess.run") as mock_run, \
             patch("leap.cli.typer.confirm", return_value=True):
            mock_run.return_value = MagicMock(returncode=0)
            install_experiment_fn(
                "https://github.com/user/remote-lab.git",
                root=tmp_root,
            )
        assert mock_run.call_count == 2
        pip_call = mock_run.call_args_list[1][0][0]
        assert pip_call[1:4] == ["-m", "pip", "install"]

    def test_update_command_shows_updated(self, tmp_root):
        self._make_installed_experiment(tmp_root)
        with patch("leap.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(app, [
                "add", "https://github.com/user/remote-lab.git",
                "--root", str(tmp_root),
            ], input="y\n")
        assert result.exit_code == 0
        assert "Updated" in result.output


class TestInstallRejectsLab:
    def test_rejects_lab_type(self, tmp_root):
        """leap add <url> should raise LabDetectedError for repos with type: lab."""
        from leap.cli import LabDetectedError
        with patch("leap.cli.subprocess.run") as mock_run:
            def side_effect(cmd, **kwargs):
                if cmd[0] == "git" and cmd[1] == "clone":
                    dest = Path(cmd[3])
                    dest.mkdir(parents=True, exist_ok=True)
                    (dest / "README.md").write_text(
                        "---\nname: other-lab\ntype: lab\nexperiments: []\n---\n"
                    )
                    return MagicMock(returncode=0)
                return MagicMock(returncode=0)

            mock_run.side_effect = side_effect
            with pytest.raises(LabDetectedError) as exc_info:
                install_experiment_fn(
                    "https://github.com/user/other-lab.git",
                    root=tmp_root,
                )
            assert exc_info.value.name == "other-lab"
            assert exc_info.value.url == "https://github.com/user/other-lab.git"
        # Should have cleaned up the cloned directory
        assert not (tmp_root / "experiments" / "other-lab").exists()

    def test_allows_experiment_type(self, tmp_root):
        """leap add <url> should allow repos with type: experiment."""
        with patch("leap.cli.subprocess.run") as mock_run:
            def side_effect(cmd, **kwargs):
                if cmd[0] == "git" and cmd[1] == "clone":
                    dest = Path(cmd[3])
                    dest.mkdir(parents=True, exist_ok=True)
                    (dest / "README.md").write_text(
                        "---\nname: cool-exp\ntype: experiment\n---\n"
                    )
                    return MagicMock(returncode=0)
                return MagicMock(returncode=0)

            mock_run.side_effect = side_effect
            name, path, updated = install_experiment_fn(
                "https://github.com/user/cool-exp.git",
                root=tmp_root,
            )
        assert name == "cool-exp"
        assert path.is_dir()


class TestInstallGitignore:
    def test_install_adds_gitignore_entry(self, tmp_root):
        with patch("leap.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            install_experiment_fn(
                "https://github.com/user/my-lab.git",
                root=tmp_root,
            )
        gitignore = tmp_root / ".gitignore"
        assert gitignore.is_file()
        assert "experiments/my-lab/" in gitignore.read_text()

    def test_install_appends_to_existing_gitignore(self, tmp_root):
        (tmp_root / ".gitignore").write_text("*.pyc\n__pycache__/\n")
        with patch("leap.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            install_experiment_fn(
                "https://github.com/user/new-lab.git",
                root=tmp_root,
            )
        content = (tmp_root / ".gitignore").read_text()
        assert "*.pyc" in content
        assert "experiments/new-lab/" in content

    def test_install_no_duplicate_gitignore_entry(self, tmp_root):
        (tmp_root / ".gitignore").write_text("experiments/remote-lab/\n")
        exp_path = tmp_root / "experiments" / "remote-lab"
        exp_path.mkdir(parents=True, exist_ok=True)
        (exp_path / ".git").mkdir()
        (exp_path / "README.md").write_text("---\nname: remote-lab\n---\n")
        with patch("leap.cli.subprocess.run") as mock_run, \
             patch("leap.cli.typer.confirm", return_value=True):
            mock_run.return_value = MagicMock(returncode=0)
            install_experiment_fn(
                "https://github.com/user/remote-lab.git",
                root=tmp_root,
            )
        content = (tmp_root / ".gitignore").read_text()
        assert content.count("experiments/remote-lab/") == 1

    def test_remove_cleans_gitignore_entry(self, tmp_root):
        from leap.cli import remove_experiment_fn
        (tmp_root / ".gitignore").write_text(
            "*.pyc\nexperiments/default/\n__pycache__/\n"
        )
        remove_experiment_fn("default", root=tmp_root)
        content = (tmp_root / ".gitignore").read_text()
        assert "experiments/default/" not in content
        assert "*.pyc" in content
        assert "__pycache__/" in content


class TestCopyExperimentFn:
    def _make_source_experiment(self, tmp_path, name="cool-viz", exp_type="experiment"):
        """Create a source experiment directory with valid frontmatter."""
        src = tmp_path / "source-lab" / "experiments" / name
        src.mkdir(parents=True)
        (src / "README.md").write_text(
            f"---\nname: {name}\ntype: {exp_type}\n"
            f"display_name: Cool Viz\ndescription: A cool experiment\n"
            f"authors: someone\ntags: [viz]\n---\n\n# {name}\n",
            encoding="utf-8",
        )
        (src / "funcs").mkdir()
        (src / "funcs" / "functions.py").write_text("def hello(): return 'hi'\n")
        (src / "ui").mkdir()
        return src

    def test_copies_experiment(self, tmp_root):
        src = self._make_source_experiment(tmp_root)
        name, path = copy_experiment_fn(str(src), root=tmp_root)
        assert name == "cool-viz"
        assert path.is_dir()
        assert (path / "funcs" / "functions.py").is_file()

    def test_uses_name_from_frontmatter(self, tmp_root):
        src = self._make_source_experiment(tmp_root, name="my-exp")
        name, _ = copy_experiment_fn(str(src), root=tmp_root)
        assert name == "my-exp"

    def test_name_override(self, tmp_root):
        src = self._make_source_experiment(tmp_root)
        name, path = copy_experiment_fn(str(src), name="custom", root=tmp_root)
        assert name == "custom"
        assert "experiments/custom" in str(path)

    def test_rejects_non_experiment_type(self, tmp_root):
        import typer
        src = self._make_source_experiment(tmp_root, exp_type="lab")
        with pytest.raises(typer.BadParameter, match="not an experiment"):
            copy_experiment_fn(str(src), root=tmp_root)

    def test_rejects_missing_readme(self, tmp_root):
        import typer
        src = tmp_root / "empty-dir"
        src.mkdir(parents=True)
        with pytest.raises(typer.BadParameter, match="No README.md"):
            copy_experiment_fn(str(src), root=tmp_root)

    def test_rejects_nonexistent_path(self, tmp_root):
        import typer
        with pytest.raises(typer.BadParameter, match="not a directory"):
            copy_experiment_fn("/nonexistent/path", root=tmp_root)

    def test_rejects_duplicate(self, tmp_root):
        import typer
        src = self._make_source_experiment(tmp_root, name="default")
        with pytest.raises(typer.BadParameter, match="already exists"):
            copy_experiment_fn(str(src), root=tmp_root)

    def test_excludes_git_dir(self, tmp_root):
        src = self._make_source_experiment(tmp_root)
        (src / ".git").mkdir()
        (src / ".git" / "config").write_text("gitconfig")
        _, path = copy_experiment_fn(str(src), root=tmp_root)
        assert not (path / ".git").exists()

    def test_tracks_in_readme(self, tmp_root):
        from leap.core.experiment import get_experiment_list
        (tmp_root / "README.md").write_text(
            "---\nname: test-lab\ntype: lab\nexperiments: []\n---\n\n# Test\n",
            encoding="utf-8",
        )
        src = self._make_source_experiment(tmp_root)
        copy_experiment_fn(str(src), root=tmp_root)
        entries = get_experiment_list(tmp_root / "README.md")
        assert any(e["name"] == "cool-viz" for e in entries)


class TestCopyCommand:
    def _make_source_experiment(self, tmp_path):
        src = tmp_path / "other-lab" / "experiments" / "viz-exp"
        src.mkdir(parents=True)
        (src / "README.md").write_text(
            "---\nname: viz-exp\ntype: experiment\n"
            "display_name: Viz Exp\ndescription: A viz experiment\n"
            "authors: someone\ntags: [viz]\n---\n\n# viz-exp\n",
            encoding="utf-8",
        )
        (src / "funcs").mkdir()
        (src / "funcs" / "functions.py").write_text("def f(): return 1\n")
        (src / "ui").mkdir()
        return src

    def test_add_local_path(self, tmp_root):
        src = self._make_source_experiment(tmp_root)
        result = runner.invoke(app, [
            "add", str(src), "--root", str(tmp_root),
        ])
        assert result.exit_code == 0
        assert "Copied" in result.output
        assert "viz-exp" in result.output

    def test_add_local_path_not_experiment(self, tmp_root):
        src = tmp_root / "some-lab"
        src.mkdir(parents=True)
        (src / "README.md").write_text(
            "---\nname: some-lab\ntype: lab\n---\n\n# Lab\n",
            encoding="utf-8",
        )
        result = runner.invoke(app, [
            "add", str(src), "--root", str(tmp_root),
        ])
        assert result.exit_code == 1
        assert "not an experiment" in result.output


class TestInstallExperimentTracking:
    def test_install_adds_url_to_readme(self, tmp_root):
        from leap.core.experiment import get_experiment_list

        # Create lab README
        (tmp_root / "README.md").write_text(
            "---\nname: test-lab\ntype: lab\nexperiments: []\n---\n\n# Test\n",
            encoding="utf-8",
        )
        url = "https://github.com/user/my-exp.git"
        with patch("leap.cli.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            install_experiment_fn(url, root=tmp_root)
        entries = get_experiment_list(tmp_root / "README.md")
        assert any(e["name"] == "my-exp" and e["source"] == url for e in entries)

"""Phase 5 CLI tests: discover and publish commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from leap.cli import app, discover_registry_fn, publish_experiment_fn, publish_fn, install_experiment_fn, _is_url, LabDetectedError

runner = CliRunner()

SAMPLE_REGISTRY = [
    {
        "name": "gradient-descent",
        "display_name": "Gradient Descent Lab",
        "description": "2D gradient descent visualization",
        "authors": "sampad",
        "repository": "https://github.com/someone/gradient-descent",
        "tags": ["optimization", "ml"],
    },
    {
        "name": "graph-search",
        "display_name": "Graph Search Lab",
        "description": "BFS/DFS exploration on grids",
        "authors": "neveisa",
        "repository": "https://github.com/someone/graph-search",
        "tags": ["algorithms", "graphs"],
    },
]

SAMPLE_YAML = """\
- name: gradient-descent
  display_name: Gradient Descent Lab
  description: 2D gradient descent visualization
  authors: sampad
  repository: https://github.com/someone/gradient-descent
  tags:
    - optimization
    - ml

- name: graph-search
  display_name: Graph Search Lab
  description: BFS/DFS exploration on grids
  authors: neveisa
  repository: https://github.com/someone/graph-search
  tags:
    - algorithms
    - graphs
"""


def _mock_response(text, status_code=200):
    resp = MagicMock()
    resp.text = text
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


class TestDiscoverRegistryFn:
    @patch("leap.cli.requests.get")
    def test_fetches_and_parses_registry(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_YAML)
        labs = discover_registry_fn()
        assert len(labs) == 2
        assert labs[0]["name"] == "gradient-descent"
        assert labs[1]["name"] == "graph-search"

    @patch("leap.cli.requests.get")
    def test_filter_by_tag(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_YAML)
        labs = discover_registry_fn(tag="algorithms")
        assert len(labs) == 1
        assert labs[0]["name"] == "graph-search"

    @patch("leap.cli.requests.get")
    def test_filter_case_insensitive(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_YAML)
        labs = discover_registry_fn(tag="ML")
        assert len(labs) == 1
        assert labs[0]["name"] == "gradient-descent"

    @patch("leap.cli.requests.get")
    def test_empty_registry(self, mock_get):
        mock_get.return_value = _mock_response("")
        labs = discover_registry_fn()
        assert labs == []

    @patch("leap.cli.requests.get")
    def test_network_error(self, mock_get):
        mock_get.side_effect = Exception("Connection refused")
        with pytest.raises(typer.BadParameter, match="Failed to fetch registry"):
            discover_registry_fn()


class TestDiscoverCommand:
    @patch("leap.cli.requests.get")
    def test_prints_table(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_YAML)
        result = runner.invoke(app, ["discover"])
        assert result.exit_code == 0
        assert "gradient-descent" in result.output
        assert "graph-search" in result.output
        assert "Install a lab with:" in result.output

    @patch("leap.cli.requests.get")
    def test_with_tag_filter(self, mock_get):
        mock_get.return_value = _mock_response(SAMPLE_YAML)
        result = runner.invoke(app, ["discover", "--tag", "algorithms"])
        assert result.exit_code == 0
        assert "graph-search" in result.output
        assert "gradient-descent" not in result.output

    @patch("leap.cli.requests.get")
    def test_empty(self, mock_get):
        mock_get.return_value = _mock_response("")
        result = runner.invoke(app, ["discover"])
        assert result.exit_code == 0
        assert "No entries found" in result.output


class TestPublishExperimentFn:
    def _make_experiment(self, tmp_root, description="Test experiment",
                         repository="", authors="tester", tags=None):
        """Helper to create an experiment with specific frontmatter."""
        exp_path = tmp_root / "experiments" / "default"
        readme = exp_path / "README.md"
        tags_yaml = f" [{', '.join(tags)}]" if tags else " []"
        readme.write_text(
            f"---\nname: default\ndisplay_name: Test Lab\n"
            f'description: "{description}"\n'
            f'authors: "{authors}"\n'
            f'repository: "{repository}"\n'
            f"tags:{tags_yaml}\n"
            f"---\n\n# Test\n",
            encoding="utf-8",
        )

    @patch("leap.cli.subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/gh")
    def test_reads_frontmatter_and_submits(self, mock_which, mock_run, tmp_root):
        self._make_experiment(tmp_root, repository="https://github.com/test/repo")
        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and "status" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "log" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "ls-remote" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "gh":
                return MagicMock(returncode=0, stdout="https://github.com/leaplive/registry/issues/1\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")
        mock_run.side_effect = side_effect

        result = publish_experiment_fn("default", root=tmp_root)
        assert result["status"] == "submitted"
        assert "issues/1" in result["issue_url"]

        # Check gh was called with correct args
        gh_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "gh"]
        assert len(gh_calls) == 1
        args = gh_calls[0][0][0]
        assert "issue" in args
        assert "create" in args

    @patch("leap.cli.subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/gh")
    def test_infers_repository_from_git_remote(self, mock_which, mock_run, tmp_root):
        self._make_experiment(tmp_root, repository="")
        # First call is git remote for exp_path, second for resolved root
        # Third call is git add, fourth is git commit, fifth is gh issue create
        def run_side_effect(cmd, **kwargs):
            if cmd[0] == "git" and "get-url" in cmd:
                return MagicMock(returncode=0, stdout="https://github.com/inferred/repo\n", stderr="")
            if cmd[0] == "git" and "ls-remote" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "add" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "commit" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "gh":
                return MagicMock(returncode=0, stdout="https://github.com/leaplive/registry/issues/2\n", stderr="")
            return MagicMock(returncode=1, stdout="", stderr="")

        mock_run.side_effect = run_side_effect
        result = publish_experiment_fn("default", root=tmp_root)
        assert result["status"] == "submitted"
        assert result["repository"] == "https://github.com/inferred/repo"

    @patch("leap.cli.subprocess.run")
    def test_writes_back_repository(self, mock_run, tmp_root):
        self._make_experiment(tmp_root, repository="")
        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and "get-url" in cmd:
                return MagicMock(returncode=0, stdout="https://github.com/remote/repo\n", stderr="")
            if cmd[0] == "git" and "ls-remote" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "status" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "log" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")
        mock_run.side_effect = side_effect

        publish_experiment_fn("default", root=tmp_root, dry_run=True)

        # Verify update_frontmatter_field was called (repository written back)
        readme = tmp_root / "experiments" / "default" / "README.md"
        content = readme.read_text()
        assert "https://github.com/remote/repo" in content

    def test_missing_required_fields_raises(self, tmp_root):
        self._make_experiment(tmp_root, description="", repository="")
        with pytest.raises(typer.BadParameter, match="description"):
            publish_experiment_fn("default", root=tmp_root)

    @patch("leap.cli._get_git_remote", return_value="")
    def test_no_repository_anywhere_raises(self, mock_remote, tmp_root):
        self._make_experiment(tmp_root, repository="")
        with pytest.raises(typer.BadParameter, match="repository"):
            publish_experiment_fn("default", root=tmp_root)

    @patch("leap.cli.subprocess.run")
    @patch("shutil.which", return_value=None)
    def test_no_gh_returns_manual_url(self, mock_which, mock_run, tmp_root):
        self._make_experiment(tmp_root, repository="https://github.com/test/repo")
        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and "ls-remote" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=1, stdout="", stderr="")
        mock_run.side_effect = side_effect

        result = publish_experiment_fn("default", root=tmp_root)
        assert result["status"] == "no_gh"
        assert "manual_url" in result

    @patch("leap.cli.subprocess.run")
    def test_unreachable_repository_raises(self, mock_run, tmp_root):
        self._make_experiment(tmp_root, repository="https://github.com/fake/nonexistent")
        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and "ls-remote" in cmd:
                return MagicMock(returncode=128, stdout="", stderr="fatal: repository not found")
            return MagicMock(returncode=0, stdout="", stderr="")
        mock_run.side_effect = side_effect
        with pytest.raises(typer.BadParameter, match="not reachable"):
            publish_experiment_fn("default", root=tmp_root)

    @patch("leap.cli.subprocess.run")
    def test_dry_run_skips_submission(self, mock_run, tmp_root):
        self._make_experiment(tmp_root, repository="https://github.com/test/repo")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = publish_experiment_fn("default", root=tmp_root, dry_run=True)
        assert result["status"] == "dry_run"
        # gh should never be called
        gh_calls = [c for c in mock_run.call_args_list if c[0] and c[0][0] and c[0][0][0] == "gh"]
        assert len(gh_calls) == 0


class TestPublishCommand:
    def _make_experiment(self, tmp_root, repository="https://github.com/test/repo"):
        exp_path = tmp_root / "experiments" / "default"
        readme = exp_path / "README.md"
        readme.write_text(
            f"---\nname: default\ndisplay_name: Test Lab\n"
            f'description: "A test lab"\n'
            f'authors: "tester"\n'
            f'repository: "{repository}"\n'
            f"tags: [test]\n"
            f"---\n\n# Test\n",
            encoding="utf-8",
        )

    @patch("leap.cli.subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/gh")
    def test_confirms_before_submitting(self, mock_which, mock_run, tmp_root):
        self._make_experiment(tmp_root)
        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and "status" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "log" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "ls-remote" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "gh":
                return MagicMock(returncode=0, stdout="https://github.com/leaplive/registry/issues/5\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")
        mock_run.side_effect = side_effect
        result = runner.invoke(app, [
            "publish", "default", "--root", str(tmp_root),
        ], input="y\n")
        assert result.exit_code == 0
        assert "Submitted!" in result.output

    @patch("leap.cli.subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/gh")
    def test_aborts_on_no(self, mock_which, mock_run, tmp_root):
        self._make_experiment(tmp_root)
        result = runner.invoke(app, [
            "publish", "default", "--root", str(tmp_root),
        ], input="n\n")
        assert result.exit_code != 0

    @patch("leap.cli.subprocess.run")
    def test_dry_run_shows_preview(self, mock_run, tmp_root):
        self._make_experiment(tmp_root)
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = runner.invoke(app, [
            "publish", "default", "--dry-run", "--root", str(tmp_root),
        ])
        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert "default" in result.output


class TestPublishLabFn:
    """Tests for publishing a lab (no experiment argument)."""

    def _make_lab_readme(self, tmp_root, name="mylab", description="A test lab",
                         repository="", authors="tester", tags=None):
        readme = tmp_root / "README.md"
        tags_yaml = f" [{', '.join(tags)}]" if tags else " []"
        readme.write_text(
            f"---\nname: {name}\ntype: lab\ndisplay_name: My Lab\n"
            f'description: "{description}"\n'
            f'authors: "{authors}"\n'
            f'repository: "{repository}"\n'
            f"tags:{tags_yaml}\n"
            f"experiments:\n  - name: default\n"
            f"---\n\n# My Lab\n",
            encoding="utf-8",
        )

    @patch("leap.cli.subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/gh")
    def test_publishes_lab_from_root_readme(self, mock_which, mock_run, tmp_root):
        self._make_lab_readme(tmp_root, repository="https://github.com/test/mylab")
        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and "status" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "log" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "ls-remote" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "gh":
                return MagicMock(returncode=0, stdout="https://github.com/leaplive/registry/issues/10\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")
        mock_run.side_effect = side_effect

        result = publish_fn(experiment=None, root=tmp_root)
        assert result["status"] == "submitted"
        assert "issues/10" in result["issue_url"]

        # Verify gh was called with "Add lab:" title
        gh_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "gh"]
        assert len(gh_calls) == 1
        args = gh_calls[0][0][0]
        assert "Add lab: mylab" in " ".join(args)

    @patch("leap.cli.subprocess.run")
    def test_lab_dry_run(self, mock_run, tmp_root):
        self._make_lab_readme(tmp_root, repository="https://github.com/test/mylab")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = publish_fn(experiment=None, root=tmp_root, dry_run=True)
        assert result["status"] == "dry_run"
        assert result["name"] == "mylab"

    def test_lab_missing_description_raises(self, tmp_root):
        self._make_lab_readme(tmp_root, description="", repository="")
        with pytest.raises(typer.BadParameter, match="description"):
            publish_fn(experiment=None, root=tmp_root)

    @patch("leap.cli.subprocess.run")
    def test_lab_infers_repository_from_git_remote(self, mock_run, tmp_root):
        self._make_lab_readme(tmp_root, repository="")
        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and "get-url" in cmd:
                return MagicMock(returncode=0, stdout="https://github.com/inferred/lab\n", stderr="")
            if cmd[0] == "git" and "ls-remote" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "add" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "commit" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(returncode=1, stdout="", stderr="")
        mock_run.side_effect = side_effect

        result = publish_fn(experiment=None, root=tmp_root, dry_run=True)
        assert result["repository"] == "https://github.com/inferred/lab"


class TestPublishLabCommand:
    """CLI integration tests for publishing a lab."""

    def _make_lab_readme(self, tmp_root, repository="https://github.com/test/mylab"):
        readme = tmp_root / "README.md"
        readme.write_text(
            f"---\nname: mylab\ntype: lab\ndisplay_name: My Lab\n"
            f'description: "A test lab"\n'
            f'authors: "tester"\n'
            f'repository: "{repository}"\n'
            f"tags: [test]\n"
            f"experiments:\n  - name: default\n"
            f"---\n\n# My Lab\n",
            encoding="utf-8",
        )

    @patch("leap.cli.subprocess.run")
    @patch("shutil.which", return_value="/usr/bin/gh")
    def test_publish_lab_no_argument(self, mock_which, mock_run, tmp_root):
        self._make_lab_readme(tmp_root)
        def side_effect(cmd, **kwargs):
            if cmd[0] == "git" and "status" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "log" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "git" and "ls-remote" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if cmd[0] == "gh":
                return MagicMock(returncode=0, stdout="https://github.com/leaplive/registry/issues/11\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")
        mock_run.side_effect = side_effect
        result = runner.invoke(app, [
            "publish", "--root", str(tmp_root),
        ], input="y\n")
        assert result.exit_code == 0
        assert "Publishing lab" in result.output
        assert "Submitted!" in result.output

    @patch("leap.cli.subprocess.run")
    def test_publish_lab_dry_run(self, mock_run, tmp_root):
        self._make_lab_readme(tmp_root)
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = runner.invoke(app, [
            "publish", "--dry-run", "--root", str(tmp_root),
        ])
        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert "mylab" in result.output


class TestIsUrl:
    """Tests for _is_url recognizing various URL forms."""

    def test_https_url(self):
        assert _is_url("https://github.com/leaplive/starterlab") is True

    def test_git_suffix(self):
        assert _is_url("git@github.com:leaplive/starterlab.git") is True

    def test_bare_github(self):
        assert _is_url("github.com/leaplive/starterlab") is True

    def test_bare_gitlab(self):
        assert _is_url("gitlab.com/someone/repo") is True

    def test_bare_bitbucket(self):
        assert _is_url("bitbucket.org/someone/repo") is True

    def test_plain_name_is_not_url(self):
        assert _is_url("my-experiment") is False

    def test_local_path_is_not_url(self):
        assert _is_url("./some/path") is False


class TestBareHostUrl:
    """Tests for leap add github.com/owner/repo bare host URL resolution."""

    @patch("leap.cli.install_experiment_fn")
    def test_bare_github_url_resolves(self, mock_install, tmp_root):
        mock_install.return_value = ("starterlab", tmp_root / "experiments" / "starterlab", False)

        result = runner.invoke(app, [
            "add", "github.com/leaplive/starterlab", "--root", str(tmp_root),
        ])
        assert result.exit_code == 0
        mock_install.assert_called_once()
        assert mock_install.call_args[0][0] == "https://github.com/leaplive/starterlab"

    @patch("leap.cli.install_experiment_fn")
    def test_bare_gitlab_url_resolves(self, mock_install, tmp_root):
        mock_install.return_value = ("myexp", tmp_root / "experiments" / "myexp", False)

        result = runner.invoke(app, [
            "add", "gitlab.com/someone/myexp", "--root", str(tmp_root),
        ])
        assert result.exit_code == 0
        mock_install.assert_called_once()
        assert mock_install.call_args[0][0] == "https://gitlab.com/someone/myexp"

    @patch("leap.cli.install_experiment_fn")
    def test_bare_bitbucket_url_resolves(self, mock_install, tmp_root):
        mock_install.return_value = ("myexp", tmp_root / "experiments" / "myexp", False)

        result = runner.invoke(app, [
            "add", "bitbucket.org/someone/myexp", "--root", str(tmp_root),
        ])
        assert result.exit_code == 0
        mock_install.assert_called_once()
        assert mock_install.call_args[0][0] == "https://bitbucket.org/someone/myexp"


class TestAddLab:
    """Tests for leap add detecting labs and handling them correctly."""

    @patch("leap.cli.install_experiment_fn")
    def test_add_lab_inside_lab_errors(self, mock_install, tmp_root, monkeypatch):
        """Adding a lab while inside another lab should error."""
        # Make cwd look like a lab root
        lab_readme = tmp_root / "README.md"
        lab_readme.write_text(
            "---\nname: mylab\ntype: lab\ndescription: test\n---\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_root)
        mock_install.side_effect = LabDetectedError("starterlab", "https://github.com/leaplive/starterlab")

        result = runner.invoke(app, [
            "add", "https://github.com/leaplive/starterlab",
        ])
        assert result.exit_code == 1
        assert "Cannot add lab" in result.output
        assert "inside another lab" in result.output

    @patch("leap.cli.install_experiment_fn")
    def test_add_lab_inside_experiment_errors(self, mock_install, tmp_root, monkeypatch):
        """Adding a lab while inside a lab's subdirectory should error."""
        # Create a lab root above cwd
        lab_readme = tmp_root / "README.md"
        lab_readme.write_text(
            "---\nname: mylab\ntype: lab\ndescription: test\n---\n",
            encoding="utf-8",
        )
        exp_dir = tmp_root / "experiments" / "default"
        exp_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.chdir(exp_dir)
        mock_install.side_effect = LabDetectedError("starterlab", "https://github.com/leaplive/starterlab")

        result = runner.invoke(app, [
            "add", "https://github.com/leaplive/starterlab",
        ])
        assert result.exit_code == 1
        assert "Cannot add lab" in result.output

    @patch("leap.cli.subprocess.run")
    @patch("leap.cli.install_experiment_fn")
    def test_add_lab_plain_dir_clones(self, mock_install, mock_run, tmp_path, monkeypatch):
        """Adding a lab from a plain directory should git clone into cwd."""
        plain_dir = tmp_path / "workspace"
        plain_dir.mkdir()
        monkeypatch.chdir(plain_dir)
        mock_install.side_effect = LabDetectedError("starterlab", "https://github.com/leaplive/starterlab")
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = runner.invoke(app, [
            "add", "https://github.com/leaplive/starterlab",
        ])
        assert result.exit_code == 0
        assert "Cloned lab" in result.output
        assert "leap init" in result.output

        # Verify git clone was called
        clone_calls = [c for c in mock_run.call_args_list if "clone" in c[0][0]]
        assert len(clone_calls) == 1
        assert "https://github.com/leaplive/starterlab" in clone_calls[0][0][0]

    @patch("leap.cli.install_experiment_fn")
    def test_add_lab_dest_exists_errors(self, mock_install, tmp_path, monkeypatch):
        """Adding a lab when target directory already exists should error."""
        plain_dir = tmp_path / "workspace"
        plain_dir.mkdir()
        (plain_dir / "starterlab").mkdir()
        monkeypatch.chdir(plain_dir)
        mock_install.side_effect = LabDetectedError("starterlab", "https://github.com/leaplive/starterlab")

        result = runner.invoke(app, [
            "add", "https://github.com/leaplive/starterlab",
        ])
        assert result.exit_code == 1
        assert "already exists" in result.output


class TestAddRequiresLab:
    """Tests that leap add refuses to run outside an initialized lab."""

    def test_add_scaffold_outside_lab_fails(self, tmp_path, monkeypatch):
        """leap add <name> from a bare directory should fail."""
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["add", "my-experiment"])
        assert result.exit_code == 1
        assert "not an initialized LEAP lab" in result.output
        assert "leap init" in result.output

    def test_add_local_path_outside_lab_fails(self, tmp_path, monkeypatch):
        """leap add ./path from a bare directory should fail."""
        src = tmp_path / "source-exp"
        src.mkdir()
        (src / "funcs").mkdir()
        (src / "README.md").write_text("---\nname: src\ntype: experiment\n---\n")
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["add", str(src)])
        assert result.exit_code == 1
        assert "not an initialized LEAP lab" in result.output

    def test_add_experiment_url_outside_lab_fails(self, tmp_path):
        """leap add <experiment-url> from a bare directory should fail after clone."""
        def fake_clone(*args, **kwargs):
            # Simulate git clone creating the destination directory
            dest = tmp_path / "experiments" / "some-exp"
            dest.mkdir(parents=True, exist_ok=True)
            return MagicMock(returncode=0)

        with patch("leap.cli.subprocess.run", side_effect=fake_clone):
            with pytest.raises(typer.BadParameter, match="not an initialized LEAP lab"):
                install_experiment_fn(
                    "https://github.com/user/some-exp.git",
                    root=tmp_path,
                )
        # Cleanup should have removed experiments/
        assert not (tmp_path / "experiments").is_dir()

    @patch("leap.cli.install_experiment_fn")
    def test_add_lab_url_outside_lab_works(self, mock_install, tmp_path, monkeypatch):
        """leap add <lab-url> from outside a lab should work via _handle_lab_add."""
        plain_dir = tmp_path / "workspace"
        plain_dir.mkdir()
        monkeypatch.chdir(plain_dir)
        mock_install.side_effect = LabDetectedError("starterlab", "https://github.com/leaplive/starterlab")

        with patch("leap.cli.subprocess.run") as mock_clone:
            mock_clone.return_value = MagicMock(returncode=0)
            result = runner.invoke(app, [
                "add", "https://github.com/leaplive/starterlab",
            ])
        assert result.exit_code == 0
        assert "Cloned lab" in result.output

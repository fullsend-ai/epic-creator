"""Repository planning consumes the checkout prepared by Epic Creator bootstrap."""
from pathlib import Path
import os
import subprocess

import pytest

from repository_fixtures import ROOT, configured, git, git_source, strategy
import repository_context
from rhai_config import RepositoryError


@pytest.fixture
def bootstrapped(tmp_path, monkeypatch):
    upstream = tmp_path / "architecture-upstream"
    upstream.mkdir()
    git(upstream, "init", "-b", "main")
    git(upstream, "config", "user.name", "Fixture")
    git(upstream, "config", "user.email", "fixture@example.org")
    for path, text in {
        "architecture/rhoai-2.0/operator.md": "Previous release architecture\n",
        "architecture/rhoai-3.0/operator.md": "Current operator architecture\n",
        "overlays/operator.md": "Active operator overlay\n",
    }.items():
        target = upstream / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    git(upstream, "add", ".")
    git(upstream, "commit", "--signoff", "-m", "Architecture fixture")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    curl = binaries / "curl"
    curl.write_text("#!/bin/sh\nprintf '%s' '[{\"name\":\"rhoai-2.0\"},{\"name\":\"rhoai-3.0\"}]'\n")
    curl.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    env = {**os.environ, "PATH": str(binaries) + os.pathsep + os.environ.get("PATH", os.defpath),
           "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.{upstream.as_uri()}.insteadOf",
           "GIT_CONFIG_VALUE_0": "https://github.com/opendatahub-io/architecture-context",
           "GIT_ALLOW_PROTOCOL": "file"}
    env.pop("EPIC_SKIP_BOOTSTRAP", None)
    result = subprocess.run(["bash", str(ROOT / "scripts/fetch-architecture-context.sh")],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    checkout = tmp_path / ".context/architecture-context"
    git(checkout, "config", "user.name", "Fixture")
    git(checkout, "config", "user.email", "fixture@example.org")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    return checkout


def test_existing_bootstrap_prepares_selected_release_and_overlays(bootstrapped):
    assert (bootstrapped / "architecture/rhoai-3.0/operator.md").is_file()
    assert not (bootstrapped / "architecture/rhoai-2.0/operator.md").exists()
    assert (bootstrapped / "overlays/operator.md").is_file()


def test_repository_context_reuses_bootstrapped_architecture_without_network(bootstrapped, tmp_path):
    selected = configured(git_source(tmp_path))
    context = repository_context.load_repository_context(selected, strategy(tmp_path), tmp_path / "artifacts")
    assert context["status"] == "ready"
    sources = {source["id"]: source for source in context["sources"]}
    assert set(sources) == {"mapping", "architecture-context"}
    architecture = sources["architecture-context"]
    assert architecture["commit"] == git(bootstrapped, "rev-parse", "HEAD")
    assert architecture["role"] == "supporting"
    assert set(architecture["files"]) == {"architecture/rhoai-3.0/operator.md", "overlays/operator.md"}
    assert Path(architecture["files"]["architecture/rhoai-3.0/operator.md"]["local_path"]).read_text() == "Current operator architecture\n"


def test_missing_bootstrap_blocks_repository_planning(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    selected = configured(git_source(tmp_path))
    context = repository_context.load_repository_context(selected, strategy(tmp_path), tmp_path / "artifacts")
    assert context["status"] == "blocked"
    assert context["reason"] == "source_unavailable"


def test_refreshed_architecture_requires_replanning(bootstrapped, tmp_path):
    selected, source = configured(git_source(tmp_path)), strategy(tmp_path)
    context = repository_context.load_repository_context(selected, source, tmp_path / "artifacts")
    architecture = next(item for item in context["sources"] if item["id"] == "architecture-context")
    saved = Path(architecture["files"]["architecture/rhoai-3.0/operator.md"]["local_path"])
    (bootstrapped / "architecture/rhoai-3.0/operator.md").write_text("Changed component responsibility\n")
    git(bootstrapped, "add", ".")
    git(bootstrapped, "commit", "--signoff", "-m", "Refreshed architecture")
    with pytest.raises(RepositoryError, match="changed"):
        repository_context.assert_context_current(context, selected, source)
    assert saved.read_text() == "Current operator architecture\n"

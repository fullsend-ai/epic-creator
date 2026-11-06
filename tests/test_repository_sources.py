from pathlib import Path

import pytest

from repository_fixtures import git, git_source, module, policy
from rhai_config import RepositoryError


@pytest.mark.parametrize("url", [
    "https://github.com/example/sources",
    "https://github.com/example/sources/blob/main/mapping.md",
    "https://gitlab.example.org/group/sources/-/blob/main/mapping.md",
])
def test_repository_and_document_links_normalize_to_same_source(url):
    source = module("repository_sources").normalize_git_source(
        {"id": "mapping", "url": url, "ref": "main", "paths": ["mapping.md"]})
    assert source["ref"] == "main"
    assert source["paths"] == ["mapping.md"]
    assert source["url"].endswith("/sources.git")


def test_git_sources_are_fetched_at_one_resolved_commit(tmp_path):
    repo = git_source(tmp_path)
    result = module("repository_sources").fetch_git_sources(policy(repo), tmp_path / "cache")
    assert len(result) == 1
    assert result[0]["commit"] == git(repo, "rev-parse", "HEAD")
    assert set(result[0]["files"]) == {"inventory.yaml", "mapping.md", "architecture.json"}
    assert Path(result[0]["files"]["mapping.md"]["local_path"]).read_text().startswith("Manager")


@pytest.mark.parametrize("changes", [
    {"ref": "missing"}, {"paths": ["missing.md"]}, {"paths": ["../escape"]},
    {"url": "https://secret-user:secret-password@example.org/repo.git"},
    {"url": "ext::sh -c true"},
])
def test_invalid_or_unavailable_source_never_falls_back(tmp_path, changes):
    repo = git_source(tmp_path)
    data = policy(repo)
    data["sources"][0].update(changes)
    with pytest.raises(RepositoryError) as failure:
        module("repository_sources").fetch_git_sources(data, tmp_path / "cache")
    assert "secret-password" not in str(failure.value)


def test_document_ref_with_slash_is_explicit(tmp_path):
    normalize = module("repository_sources").normalize_git_source
    source = normalize({"url": "https://github.com/example/source/blob/release/v1/docs/map.md",
                        "ref": "release/v1", "id": "map"})
    assert source["paths"] == ["docs/map.md"]
    with pytest.raises(RepositoryError):
        normalize({"url": "https://github.com/example/source/blob/release/v1/docs/map.md", "id": "map"})


def test_self_source_reads_committed_mapping_snapshot(tmp_path, monkeypatch):
    repo = git_source(tmp_path)
    api = module("repository_sources")
    monkeypatch.setattr(api, "__file__", str(repo / "scripts/repository_sources.py"))
    (repo / "mapping.md").write_text("Uncommitted edit must not replace pinned evidence.\n")
    data = policy(repo)
    data["sources"][0].update(url="self", ref="HEAD", paths=["mapping.md"])
    sources = api.fetch_git_sources(data, tmp_path / "cache")
    assert sources[0]["commit"] == git(repo, "rev-parse", "HEAD")
    assert Path(sources[0]["files"]["mapping.md"]["local_path"]).read_text() == "Manager and cloudmanager changes belong in operator.\n"

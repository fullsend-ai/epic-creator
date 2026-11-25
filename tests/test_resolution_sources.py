#!/usr/bin/env python3
"""Tests for skills/repository-resolution/scripts/sources.py — the source loader."""
import copy
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "repository-resolution" / "scripts"))
import sources  # noqa: E402
from sources import SourceConfigError, SourceError  # noqa: E402

SCHEMA = ROOT / "config" / "_schema" / "resolver.schema.json"
ARCH_URL = "https://github.com/example/architecture-context"


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e"))


def _make_repo(path, files):
    path.mkdir(parents=True)
    _git(path, "init", "--quiet")
    for rel, content in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(path, "add", "-A")
    _git(path, "commit", "--quiet", "-m", "init")
    return path


def _local_fetcher(remotes):
    """A fetcher that serves HTTPS source URLs from local repositories."""
    calls = []

    def fetch(url, ref, paths, workdir):
        calls.append((url, ref, paths))
        repo = remotes[url]
        sha = subprocess.run(["git", "rev-parse", f"{ref}^{{commit}}"], cwd=repo,
                             check=True, capture_output=True, text=True).stdout.strip()
        return repo, sha

    fetch.calls = calls
    return fetch


def _config(**overrides):
    config = {
        "version": 1,
        "group": "test",
        "sources": [{
            "id": "architecture", "url": ARCH_URL, "ref": "main",
            "paths": ["architecture/v1/"], "role": "architecture",
        }],
        "repository_data": {"source": "architecture",
                            "path": "architecture/v1/component-map.json",
                            "host": "github.com"},
        "jira": {"repository_url_field": "customfield_10933"},
    }
    config.update(overrides)
    return config


def _write_config(tmp_path, config):
    path = tmp_path / "resolver.yaml"
    path.write_text(yaml.safe_dump(config))
    return path


@pytest.fixture
def arch_repo(tmp_path):
    repo = _make_repo(tmp_path / "arch", {
        "architecture/v1/component-map.json": '{"components": {}}\n',
        "architecture/v1/dashboard.md": "The dashboard lives in odh-dashboard.\n",
        "architecture/v2/other.md": "not declared\n",
    })
    _git(repo, "branch", "-M", "main")
    return repo


class TestRealConfig:
    def test_shipped_config_is_valid(self):
        config, _ = sources.read_config(ROOT / "config" / "resolver.yaml")
        assert config["group"] == "rhai"


class TestValidation:
    def _invalid(self, config, match):
        with pytest.raises(SourceConfigError, match=match):
            sources.validate_config(config, SCHEMA)

    def _with_source(self, **changes):
        config = _config()
        config["sources"][0].update(changes)
        return config

    @pytest.mark.parametrize("url", [
        "file:///etc",
        "http://github.com/a/b",
        "https://token@github.com/a/b",
        "https://user:pass@github.com/a/b",
        "https://github.com/a/b?x=1",
        "https://github.com/a/b#frag",
        "https://github.com:8443/a/b",
        "ssh://git@github.com/a/b",
        "git@github.com:a/b.git",
        "/local/path",
    ])
    def test_rejects_non_contract_urls(self, url):
        self._invalid(self._with_source(url=url), "invalid|HTTPS|credentials")

    def test_check_url_rejects_dot_segments(self):
        with pytest.raises(SourceConfigError, match="not canonical"):
            sources.check_url("https://github.com/a/../b")

    @pytest.mark.parametrize("path", ["../outside", "a/../../b", "/etc", "a/.."])
    def test_rejects_escaping_paths(self, path):
        self._invalid(self._with_source(paths=[path]), "invalid|'..'|relative|escapes")

    def test_rejects_duplicate_ids(self):
        config = _config()
        config["sources"].append(dict(config["sources"][0], role="mapping"))
        self._invalid(config, "duplicate source ids")

    def test_requires_exactly_one_architecture_source(self):
        config = _config()
        config["sources"].append(dict(config["sources"][0], id="arch2"))
        self._invalid(config, "role 'architecture'")
        self._invalid(self._with_source(role="mapping"), "role 'architecture'")

    def test_repository_data_must_name_declared_source(self):
        config = _config()
        config["repository_data"]["source"] = "missing"
        self._invalid(config, "not a declared source")

    def test_self_source_is_pinned_to_head(self):
        config = _config()
        config["sources"].append({"id": "mapping", "url": "self", "ref": "main",
                                  "paths": ["config/x.yaml"], "role": "mapping"})
        self._invalid(config, "pinned to HEAD")

    def test_missing_jira_field_is_invalid(self):
        config = _config()
        del config["jira"]["repository_url_field"]
        self._invalid(config, "repository_url_field")

    def test_unknown_keys_are_invalid(self):
        self._invalid(_config(extra=True), "invalid")

    def test_invalid_yaml_is_config_error(self, tmp_path):
        path = tmp_path / "resolver.yaml"
        path.write_text("sources: [unclosed\n")
        with pytest.raises(SourceConfigError, match="not valid YAML"):
            sources.read_config(path, SCHEMA)


class TestLoad:
    def test_snapshots_declared_paths_at_resolved_sha(self, tmp_path, arch_repo):
        fetcher = _local_fetcher({ARCH_URL: arch_repo})
        out = tmp_path / "out"
        manifest = sources.load(_write_config(tmp_path, _config()), out,
                                fetcher=fetcher, schema_path=SCHEMA)

        source = manifest["sources"][0]
        head = subprocess.run(["git", "rev-parse", "main"], cwd=arch_repo, check=True,
                              capture_output=True, text=True).stdout.strip()
        assert source["sha"] == head
        assert source["paths"] == ["architecture/v1"]
        assert source["files"] == 2
        assert (out / "architecture/architecture/v1/dashboard.md").read_text() == \
            "The dashboard lives in odh-dashboard.\n"
        assert not (out / "architecture/architecture/v2").exists()
        assert (out / "manifest.json").exists()
        assert fetcher.calls == [(ARCH_URL, "main", ["architecture/v1"])]

    def test_content_hash_is_stable_and_content_sensitive(self, tmp_path, arch_repo):
        fetcher = _local_fetcher({ARCH_URL: arch_repo})
        config = _write_config(tmp_path, _config())
        first = sources.load(config, tmp_path / "a", fetcher=fetcher, schema_path=SCHEMA)
        second = sources.load(config, tmp_path / "b", fetcher=fetcher, schema_path=SCHEMA)
        assert first["sources"][0]["content_hash"] == second["sources"][0]["content_hash"]

        (arch_repo / "architecture/v1/dashboard.md").write_text("changed\n")
        _git(arch_repo, "commit", "--quiet", "-am", "change")
        third = sources.load(config, tmp_path / "c", fetcher=fetcher, schema_path=SCHEMA)
        assert third["sources"][0]["content_hash"] != first["sources"][0]["content_hash"]

    def test_self_source_reads_committed_head_not_working_tree(self, tmp_path, arch_repo):
        consumer = _make_repo(tmp_path / "consumer",
                              {"config/mapping.yaml": "committed: true\n"})
        (consumer / "config/mapping.yaml").write_text("uncommitted: true\n")
        config = _config()
        config["sources"].append({"id": "mapping", "url": "self", "ref": "HEAD",
                                  "paths": ["config/mapping.yaml"], "role": "mapping"})
        out = tmp_path / "out"
        sources.load(_write_config(tmp_path, config), out, repo_root=consumer,
                     fetcher=_local_fetcher({ARCH_URL: arch_repo}), schema_path=SCHEMA)
        assert (out / "mapping/config/mapping.yaml").read_text() == "committed: true\n"

    def test_missing_declared_path_is_config_error(self, tmp_path, arch_repo):
        config = _config()
        config["sources"][0]["paths"] = ["architecture/v1", "architecture/v9"]
        with pytest.raises(SourceConfigError, match="architecture/v9"):
            sources.load(_write_config(tmp_path, config), tmp_path / "out",
                         fetcher=_local_fetcher({ARCH_URL: arch_repo}),
                         schema_path=SCHEMA)

    def test_symlink_in_declared_path_is_rejected(self, tmp_path, arch_repo):
        (arch_repo / "architecture/v1/link.md").symlink_to("/etc/passwd")
        _git(arch_repo, "add", "-A")
        _git(arch_repo, "commit", "--quiet", "-m", "symlink")
        with pytest.raises(SourceConfigError, match="non-regular file"):
            sources.load(_write_config(tmp_path, _config()), tmp_path / "out",
                         fetcher=_local_fetcher({ARCH_URL: arch_repo}),
                         schema_path=SCHEMA)

    def test_refuses_non_empty_output_directory(self, tmp_path, arch_repo):
        out = tmp_path / "out"
        out.mkdir()
        (out / "stale.json").write_text("{}")
        with pytest.raises(SourceConfigError, match="not empty"):
            sources.load(_write_config(tmp_path, _config()), out,
                         fetcher=_local_fetcher({ARCH_URL: arch_repo}),
                         schema_path=SCHEMA)

    def test_invalid_config_fetches_nothing(self, tmp_path, arch_repo):
        config = _config()
        config["sources"][0]["url"] = "file:///etc"
        fetcher = _local_fetcher({ARCH_URL: arch_repo})
        with pytest.raises(SourceConfigError):
            sources.load(_write_config(tmp_path, config), tmp_path / "out",
                         fetcher=fetcher, schema_path=SCHEMA)
        assert fetcher.calls == []


class TestCli:
    def test_errors_exit_nonzero_with_kind(self, tmp_path, capsys):
        config = _config()
        config["sources"][0]["url"] = "file:///etc"
        path = _write_config(tmp_path, config)
        (tmp_path / "_schema").mkdir()
        (tmp_path / "_schema" / "resolver.schema.json").write_text(SCHEMA.read_text())
        assert sources.main(["validate", "--config", str(path)]) == 1
        err = capsys.readouterr().err
        assert "ERROR [config]" in err
        assert "not an unresolved result" in err

    def test_validate_ok(self, capsys):
        assert sources.main(["validate", "--config",
                             str(ROOT / "config" / "resolver.yaml")]) == 0
        assert "is valid" in capsys.readouterr().out

from pathlib import Path
import sys
from unittest.mock import Mock

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_components
import submit


@pytest.fixture
def component_output(tmp_path, monkeypatch):
    monkeypatch.delenv("EPIC_SKIP_BOOTSTRAP", raising=False)
    monkeypatch.delenv("EPIC_SKIP_COMPONENT_FETCH", raising=False)
    output = tmp_path / ".context" / "rhai-components.txt"
    monkeypatch.setattr(fetch_components, "OUTPUT_PATH", str(output))
    return output


@pytest.mark.parametrize("bootstrap_skip", [None, "1"])
@pytest.mark.parametrize("component_skip", [None, ""])
def test_fetches_components_with_architecture_bootstrap_skipped(
        component_output, monkeypatch, bootstrap_skip, component_skip):
    if bootstrap_skip is not None:
        monkeypatch.setenv("EPIC_SKIP_BOOTSTRAP", bootstrap_skip)
    if component_skip is not None:
        monkeypatch.setenv("EPIC_SKIP_COMPONENT_FETCH", component_skip)
    credentials = ("https://jira.example.com", "user", "test-token")
    monkeypatch.setattr(fetch_components, "require_env", lambda: credentials)
    api = Mock(return_value=[{"name": "Widget B"}, {"name": " Widget A "}])
    monkeypatch.setattr(fetch_components, "api_call_with_retry", api)

    fetch_components.main()

    api.assert_called_once_with(
        credentials[0], f"/project/{fetch_components.PROJECT}/components",
        credentials[1], credentials[2])
    assert component_output.read_text() == "Widget A\nWidget B\n"


@pytest.mark.parametrize("use_eval_config", [False, True])
def test_component_skip_preserves_fixture_without_credentials_or_network(
        component_output, monkeypatch, use_eval_config):
    if use_eval_config:
        config_path = (Path(__file__).resolve().parents[1]
                       / "eval" / "epic-decompose.yaml")
        env = yaml.safe_load(config_path.read_text())["execution"]["env"]
    else:
        env = {"EPIC_SKIP_COMPONENT_FETCH": "1"}
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    component_output.parent.mkdir()
    fixture = b"Fixture Component\n"
    component_output.write_bytes(fixture)
    credentials = Mock(side_effect=AssertionError("Unexpected credential lookup"))
    api = Mock(side_effect=AssertionError("Unexpected API request"))
    monkeypatch.setattr(fetch_components, "require_env", credentials)
    monkeypatch.setattr(fetch_components, "api_call_with_retry", api)

    fetch_components.main()

    credentials.assert_not_called()
    api.assert_not_called()
    assert component_output.read_bytes() == fixture


def test_fetch_and_submit_share_working_directory_component_cache(
        tmp_path, monkeypatch):
    monkeypatch.delenv("EPIC_SKIP_BOOTSTRAP", raising=False)
    monkeypatch.delenv("EPIC_SKIP_COMPONENT_FETCH", raising=False)
    monkeypatch.chdir(tmp_path)

    expected = tmp_path / ".context" / "rhai-components.txt"
    assert Path(fetch_components.OUTPUT_PATH).resolve() == expected.resolve()
    assert Path(submit.COMPONENTS_PATH).resolve() == expected.resolve()
    assert not expected.exists()

    credentials = ("https://jira.example.com", "user", "test-token")
    monkeypatch.setattr(fetch_components, "require_env", lambda: credentials)
    api = Mock(return_value=[{"name": "Widget B"}, {"name": " Widget A "}])
    monkeypatch.setattr(fetch_components, "api_call_with_retry", api)

    fetch_components.main()

    api.assert_called_once_with(
        credentials[0], f"/project/{fetch_components.PROJECT}/components",
        credentials[1], credentials[2])
    assert expected.read_text() == "Widget A\nWidget B\n"
    assert submit._load_valid_components() == {"Widget A", "Widget B"}

"""Load the fixed RHAI repository configuration used by the main pipeline."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

from jsonschema import Draft202012Validator
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/rhai-repositories.yaml"


class RepositoryError(ValueError):
    """Invalid RHAI repository configuration or bound planning artifact."""


def _digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Planning:
    repository_policy: dict | None
    digest: str
    component_file: str = ".context/rhai-components.txt"

    def canonical_components(self):
        try:
            names = {s.strip() for s in Path(self.component_file).read_text().splitlines() if s.strip()}
        except OSError:
            raise RepositoryError("Run scripts/fetch_components.py before repository planning") from None
        if not names:
            raise RepositoryError("Canonical RHAI component cache is empty")
        return names

    def validate_component(self, name, names=None):
        if name not in (self.canonical_components() if names is None else names):
            raise RepositoryError(f"Unknown RHAI component: {name}")


@dataclass(frozen=True)
class RhaiConfig:
    version: int
    digest: str
    planning: Planning
    id: str = "rhai"
    project: str = "RHAISTRAT"
    issue_type: str = "Feature"
    id_pattern: str = r"RHAISTRAT-\d+"
    processed_label: str = "epic-creator-auto-decomposed"

    @property
    def submission(self):
        return SimpleNamespace(project="RHAI", issue_type="Epic", parent={"relation": "parent"},
            dependency_link_type="Blocks", labels={"created": "epic-creator-auto-created",
            "investigation": "epic-creator-investigation", "implementation_prefix": "epic-creator-impl-",
            "ai_prefix": "epic-creator-ai-impl-", "needs_component": "epic-creator-needs-component"})

    def validate_identity(self, key, project=None, issue_type=None):
        if not isinstance(key, str) or re.fullmatch(self.id_pattern, key) is None:
            raise RepositoryError("Repository mode requires a RHAISTRAT strategy")
        if project is not None and (project, issue_type) != (self.project, self.issue_type):
            raise RepositoryError("Unexpected RHAISTRAT source project or issue type")

    def validate_ready(self, issue):
        fields = issue.get("fields", {})
        self.validate_identity(issue.get("key"), fields.get("project", {}).get("key"), fields.get("issuetype", {}).get("name"))
        if fields.get("project", {}).get("key") != self.project or fields.get("issuetype", {}).get("name") != self.issue_type:
            raise RepositoryError("Missing or invalid RHAISTRAT source identity")
        if not {"strat-creator-rubric-pass", "strat-creator-human-sign-off"} <= set(fields.get("labels", [])):
            raise RepositoryError("Strategy is missing approval labels")


def load_config(path=None):
    path = Path(path) if path is not None else CONFIG_PATH
    try:
        data = yaml.safe_load(path.read_text())
        schema = json.loads((ROOT / "config/_schema/rhai-repositories.schema.json").read_text())
        errors = list(Draft202012Validator(schema).iter_errors(data))
        if errors:
            raise RepositoryError(f"Invalid RHAI repository configuration: {errors[0].message}")
        from repository_sources import normalize_git_source, guidance_reference, _path
        policy = data["repository_policy"]
        ids = [source["id"] for source in policy["sources"]]
        if len(ids) != len(set(ids)) or policy["inventory"]["source_id"] not in ids:
            raise RepositoryError("Duplicate or missing repository inventory source")
        _path(policy["inventory"]["path"])
        guidance_reference(policy)
        for source in policy["sources"]:
            normalize_git_source(source)
        prompts = ROOT / "skills/epic-decompose/prompts"
        prompt_content = {p.name: p.read_text() for p in prompts.glob("*.md")}
        return RhaiConfig(version=data["schema_version"], digest=_digest(data),
            planning=Planning(policy if data["enabled"] else None,
                              _digest({"configuration": data, "prompts": prompt_content})))
    except (OSError, yaml.YAMLError, TypeError, KeyError) as exc:
        raise RepositoryError("Cannot load the fixed RHAI repository configuration") from exc

"""Load the fixed RHAI repository configuration used by the main pipeline."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator
import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config/rhai-repositories.yaml"
COMPONENT_FILE = ".context/rhai-components.txt"


class RepositoryError(ValueError):
    """Invalid RHAI repository configuration or bound planning artifact."""


def _digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class RhaiConfig:
    version: int
    digest: str
    planning_digest: str
    repository_policy: dict | None

    def canonical_components(self):
        try:
            names = {s.strip() for s in Path(COMPONENT_FILE).read_text().splitlines() if s.strip()}
        except OSError:
            raise RepositoryError("Run scripts/fetch_components.py before repository planning") from None
        if not names:
            raise RepositoryError("Canonical RHAI component cache is empty")
        return names

    def validate_component(self, name, names=None):
        if name not in (self.canonical_components() if names is None else names):
            raise RepositoryError(f"Unknown RHAI component: {name}")

    def validate_identity(self, key):
        if not isinstance(key, str) or re.fullmatch(r"RHAISTRAT-\d+", key) is None:
            raise RepositoryError("Repository mode requires a RHAISTRAT strategy")

    def validate_ready(self, issue):
        fields = issue.get("fields", {})
        self.validate_identity(issue.get("key"))
        if fields.get("project", {}).get("key") != "RHAISTRAT" or fields.get("issuetype", {}).get("name") != "Feature":
            raise RepositoryError("Missing or invalid RHAISTRAT source identity")
        if not {"strat-creator-rubric-pass", "strat-creator-human-sign-off"} <= set(fields.get("labels", [])):
            raise RepositoryError("Strategy is missing approval labels")


def load_config(path=None):
    path = Path(path) if path is not None else CONFIG_PATH
    try:
        data = yaml.safe_load(path.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("schema_version"), int):
            raise RepositoryError("Cannot load the fixed RHAI repository configuration")

        # Keep the legacy pipeline independent from repository-mode inputs.  In
        # particular, a disabled feature must not fail because the new
        # repository policy is empty, its schema is unavailable, or a source
        # path is stale.  We still bind the top-level switch and schema version
        # so an accidental malformed config is visible when the file is read.
        if data.get("enabled") is False:
            return RhaiConfig(version=data["schema_version"], digest=_digest(data),
                repository_policy=None, planning_digest=_digest({"configuration": data}))

        if data.get("enabled") is not True:
            raise RepositoryError("RHAI repository configuration must set enabled to true or false")
        schema = json.loads((ROOT / "config/_schema/rhai-repositories.schema.json").read_text())
        errors = list(Draft202012Validator(schema).iter_errors(data))
        if errors:
            raise RepositoryError(f"Invalid RHAI repository configuration: {errors[0].message}")
        from repository_sources import normalize_git_source, guidance_reference, _path
        policy = data["repository_policy"]
        ids = [source["id"] for source in policy["sources"]]
        if "architecture-context" in ids:
            raise RepositoryError("Architecture context is supplied by the existing bootstrap, not repository sources")
        if len(ids) != len(set(ids)) or policy["inventory"]["source_id"] not in ids:
            raise RepositoryError("Duplicate or missing repository inventory source")
        _path(policy["inventory"]["path"])
        guidance_reference(policy)
        for source in policy["sources"]:
            normalize_git_source(source)
        prompts = ROOT / "skills/epic-decompose/prompts"
        prompt_content = {p.name: p.read_text() for p in prompts.glob("*.md")}
        return RhaiConfig(version=data["schema_version"], digest=_digest(data),
            repository_policy=policy if data["enabled"] else None,
            planning_digest=_digest({"configuration": data, "prompts": prompt_content}))
    except (OSError, yaml.YAMLError, TypeError, KeyError) as exc:
        raise RepositoryError("Cannot load the fixed RHAI repository configuration") from exc

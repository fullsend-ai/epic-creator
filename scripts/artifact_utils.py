"""Artifact schema definitions, frontmatter read/write/validate.

Owns all structured metadata for strategy and epic decomposition artifacts.
Scripts and skills use this module instead of regex-parsing markdown prose.

Frontmatter is stored as YAML between --- delimiters at the top of markdown files.
"""

import os
import re
import sys

import yaml


# ─── Schema Definitions ────────────────────────────────────────────────────────

# Each schema is a dict of field_name -> field_spec.
# field_spec keys:
#   type:     "string" | "int" | "bool" | "list" | "dict"
#   required: bool (default False)
#   enum:     list of allowed values (optional)
#   pattern:  regex pattern the value must match (optional, strings only)
#   default:  default value when not provided (optional)
#   fields:   nested schema for type="dict" (optional)

SCHEMAS = {
    "strat-task": {
        "strat_id": {
            "type": "string",
            "required": True,
            "pattern": r"^RHAISTRAT-\d+$",
        },
        "title": {
            "type": "string",
            "required": True,
        },
        "status": {
            "type": "string",
            "required": False,
            "default": "",
        },
        "priority": {
            "type": "string",
            "required": False,
            "default": "",
        },
        "labels": {
            "type": "list",
            "required": False,
            "default": [],
        },
        "links": {
            "type": "list",
            "required": False,
            "default": [],
        },
        "attachment_source": {
            "type": "string",
            "required": False,
            "default": "",
        },
    },
    "epic-task": {
        "epic_id": {
            "type": "string",
            "required": True,
        },
        "title": {
            "type": "string",
            "required": True,
        },
        "parent_strat": {
            "type": "string",
            "required": True,
            "pattern": r"^RHAISTRAT-\d+$",
        },
        "component": {
            "type": "string",
            "required": True,
        },
        "team": {
            "type": "string",
            "required": True,
        },
        "type": {
            "type": "string",
            "required": True,
            "enum": ["Implementation", "Investigation"],
        },
        "implementation_type": {
            "type": "string",
            "required": False,
            "enum": ["docs-authoring", "konflux-onboarding",
                     "license-validation", "repo-onboarding"],
            "default": None,
        },
        "priority": {
            "type": "string",
            "required": True,
            "enum": ["P0", "P1", "P2"],
        },
        "dependencies": {
            "type": "list",
            "required": False,
            "default": [],
        },
        "ai_signals": {
            "type": "dict",
            "required": False,
            "default": None,
            "fields": {
                "change_specificity":  {"type": "int", "required": False, "default": 0},
                "pattern_precedent":   {"type": "int", "required": False, "default": 0},
                "adapter_pattern":     {"type": "int", "required": False, "default": 0},
                "existing_foundation": {"type": "int", "required": False, "default": 0},
                "open_questions":      {"type": "int", "required": False, "default": 0},
                "external_dependency": {"type": "int", "required": False, "default": 0},
                "human_process_gates": {"type": "int", "required": False, "default": 0},
                "repo_access":         {"type": "int", "required": False, "default": 0},
                "architecture_claims": {"type": "int", "required": False, "default": 0},
            },
        },
        # Investigation epics use this set instead of ai_signals. The
        # Implementation signals above penalize the *defining* traits of an
        # investigation (open questions, no foundation), so they systematically
        # mis-route investigations to Low. These five predict, at decomposition
        # time, whether the AI skill can resolve the unknowns (assign to skill)
        # or they need a person. Scored +1/0 for positives, 0/-1/-2 for blockers.
        "investigation_signals": {
            "type": "dict",
            "required": False,
            "default": None,
            "fields": {
                # Positives: is it well-posed, readable, and runnable?
                "question_specificity":        {"type": "int", "required": False, "default": 0, "enum": [-1, 0, 1]},
                "source_accessibility":        {"type": "int", "required": False, "default": 0, "enum": [0, 1]},
                "local_runnability":           {"type": "int", "required": False, "default": 0, "enum": [0, 1]},
                # Blockers (0/-1/-2): does resolving it need a cluster/hardware
                # or a human decision the AI can't make?
                "cluster_hardware_dependence": {"type": "int", "required": False, "default": 0, "enum": [-2, -1, 0]},
                "human_judgment_required":     {"type": "int", "required": False, "default": 0, "enum": [-2, -1, 0]},
            },
        },
        "ai_implementability": {
            "type": "string",
            "required": False,
            "enum": ["High", "Medium", "Low"],
            "default": None,
        },
        "ai_implementability_score": {
            "type": "int",
            "required": False,
            "default": None,
        },
        "jira_key": {
            "type": "string",
            "required": False,
            "pattern": r"^[A-Z][A-Z0-9_]*-\d+$",
            "default": None,
        },
        "branch": {
            "type": "string",
            "required": False,
            "default": None,
        },
        "gated_by": {
            "type": "string",
            "required": False,
            "default": None,
        },
        "gate_failure_impact": {
            "type": "dict",
            "required": False,
            "default": None,
            "fields": {
                "action": {
                    "type": "string",
                    "required": False,
                    "enum": ["rewrite", "remove", "add_remediation"],
                    "default": None,
                },
                "fallback_approach": {
                    "type": "string",
                    "required": False,
                    "default": None,
                },
            },
        },
    },
    "decomp-summary": {
        "parent_strat": {
            "type": "string",
            "required": True,
            "pattern": r"^RHAISTRAT-\d+$",
        },
        "epic_count": {
            "type": "int",
            "required": True,
        },
        "critical_path_length": {
            "type": "int",
            "required": True,
        },
        "decompose_complete": {
            "type": "bool",
            "required": False,
            "default": False,
        },
        "revised": {
            "type": "bool",
            "required": False,
            "default": False,
        },
        "triage": {
            "type": "string",
            "required": False,
            "enum": ["below-threshold", "docs-only"],
            "default": None,
        },
        "triage_rationale": {
            "type": "string",
            "required": False,
            "default": None,
        },
    },
    "decomp-review": {
        "strat_id": {
            "type": "string",
            "required": True,
            "pattern": r"^RHAISTRAT-\d+$",
        },
        "score": {
            "type": "int",
            "required": True,
        },
        "pass": {
            "type": "bool",
            "required": True,
        },
        "recommendation": {
            "type": "string",
            "required": True,
            "enum": ["accept", "revise"],
        },
        "issues": {
            "type": "list",
            "required": False,
            "default": [],
        },
        "error": {
            "type": "string",
            "required": False,
            "default": None,
        },
    },
}


# ─── Validation ─────────────────────────────────────────────────────────────────

class ValidationError(Exception):
    """Raised when frontmatter fails schema validation."""
    pass


SCHEMAS["epic-task"].update({
    "repository_scope": {"type": "string"},
    "work_item_ids": {"type": "list"},
    "components": {"type": "list"},
    "mapping_snapshot_digest": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
    "target_repository": {"type": "dict", "fields": {
        name: {"type": "string", "required": True}
        for name in ("inventory_key", "provider", "host", "namespace", "repository")}},
})
SCHEMAS["decomp-summary"]["mapping_snapshot_digest"] = {"type": "string", "pattern": r"^[0-9a-f]{64}$"}
SCHEMAS["decomp-review"].update({
    name: {"type": "string", "pattern": r"^[0-9a-f]{64}$"}
    for name in ("repository_plan_digest", "reviewed_decomposition_digest")
})

_REPOSITORY_SOURCE_FIELDS = {
    "rhai_config_digest": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
    "planning_digest": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
    "source_revision": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
    "attachment_id": {"type": "string"},
    "attachment_created": {"type": "string"},
}
for _schema in SCHEMAS.values():
    _schema.update(_REPOSITORY_SOURCE_FIELDS)


def _validate_field(name, value, spec, path=""):
    """Validate a single field against its spec. Returns list of errors."""
    errors = []
    full_name = f"{path}.{name}" if path else name

    if value is None:
        if spec.get("required", False) and "default" not in spec:
            errors.append(f"Missing required field: {full_name}")
        return errors

    expected_type = spec.get("type", "string")

    if expected_type == "string":
        if not isinstance(value, str):
            errors.append(
                f"{full_name}: expected string, got {type(value).__name__}")
            return errors
        if "enum" in spec and value not in spec["enum"]:
            errors.append(
                f"{full_name}: '{value}' not in {spec['enum']}")
        if "pattern" in spec and not re.match(spec["pattern"], value):
            errors.append(
                f"{full_name}: '{value}' does not match {spec['pattern']}")

    elif expected_type == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(
                f"{full_name}: expected int, got {type(value).__name__}")
        elif "enum" in spec and value not in spec["enum"]:
            errors.append(
                f"{full_name}: {value} not in {spec['enum']}")

    elif expected_type == "bool":
        if not isinstance(value, bool):
            errors.append(
                f"{full_name}: expected bool, got {type(value).__name__}")

    elif expected_type == "list":
        if not isinstance(value, list):
            errors.append(
                f"{full_name}: expected list, got {type(value).__name__}")

    elif expected_type == "dict":
        if not isinstance(value, dict):
            errors.append(
                f"{full_name}: expected dict, got {type(value).__name__}")
            return errors
        nested_schema = spec.get("fields", {})
        for key in value:
            if key not in nested_schema:
                errors.append(f"{full_name}: unknown field '{key}'")
        for field_name, field_spec in nested_schema.items():
            errors.extend(_validate_field(
                field_name, value.get(field_name), field_spec, full_name))

    return errors


def validate(data, schema_type):
    """Validate frontmatter data against a schema.

    Returns list of error strings (empty if valid).
    Raises ValueError if schema_type is unknown.
    """
    if schema_type not in SCHEMAS:
        raise ValueError(
            f"Unknown schema type: {schema_type}. "
            f"Valid types: {list(SCHEMAS.keys())}")

    schema = SCHEMAS[schema_type]
    errors = []

    for key in data:
        if key not in schema:
            errors.append(f"Unknown field: {key}")

    for field_name, field_spec in schema.items():
        errors.extend(_validate_field(
            field_name, data.get(field_name), field_spec))

    if data.get("rhai_config_digest"):
        from rhai_config import load_config, RepositoryError
        try:
            selected = load_config()
            if not selected.repository_policy or data["rhai_config_digest"] != selected.digest or data.get("planning_digest") != selected.planning_digest:
                raise RepositoryError("RHAI repository configuration or prompts changed; start a new run")
            required = list(_REPOSITORY_SOURCE_FIELDS)[:3]
            if schema_type == "epic-task":
                required += ["repository_scope", "work_item_ids", "components", "mapping_snapshot_digest"]
                if data.get("type") == "Implementation":
                    required.append("target_repository")
                for name in ("components", "work_item_ids"):
                    values = data.get(name)
                    if not isinstance(values, list) or not values or any(not isinstance(x, str) or not x for x in values) or len(values) != len(set(values)):
                        raise RepositoryError(f"{name} must contain unique nonempty strings")
                if data.get("component") != data["components"][0]:
                    raise RepositoryError("Primary component must be first in components")
                for component in data["components"]:
                    selected.validate_component(component)
            elif schema_type == "decomp-summary": required.append("mapping_snapshot_digest")
            elif schema_type == "decomp-review": required += ["repository_plan_digest", "reviewed_decomposition_digest"]
            if any(not data.get(name) for name in required):
                raise RepositoryError("Missing repository fields: " + ", ".join(name for name in required if not data.get(name)))
        except RepositoryError as exc:
            errors.append(str(exc))
    return errors


def apply_defaults(data, schema_type):
    """Apply default values for missing optional fields. Modifies in-place."""
    schema = SCHEMAS[schema_type]
    for field_name, field_spec in schema.items():
        if field_name not in data and "default" in field_spec:
            data[field_name] = field_spec["default"]
        if field_spec.get("type") == "dict" and field_name in data:
            nested = data[field_name]
            if isinstance(nested, dict):
                for nested_name, nested_spec in \
                        field_spec.get("fields", {}).items():
                    if nested_name not in nested and \
                            "default" in nested_spec:
                        nested[nested_name] = nested_spec["default"]
    return data


def get_schema_yaml(schema_type):
    """Return the schema definition as a YAML string for display."""
    if schema_type not in SCHEMAS:
        raise ValueError(
            f"Unknown schema type: {schema_type}. "
            f"Valid types: {list(SCHEMAS.keys())}")

    schema = SCHEMAS[schema_type]
    output = {"required": {}, "optional": {}}

    for name, spec in schema.items():
        entry = {"type": spec["type"]}
        if "enum" in spec:
            entry["enum"] = spec["enum"]
        if "pattern" in spec:
            entry["pattern"] = spec["pattern"]
        if "default" in spec:
            entry["default"] = spec["default"]
        if spec.get("type") == "dict" and "fields" in spec:
            entry["fields"] = {}
            for fname, fspec in spec["fields"].items():
                fentry = {"type": fspec["type"]}
                if "enum" in fspec:
                    fentry["enum"] = fspec["enum"]
                entry["fields"][fname] = fentry

        if spec.get("required", False):
            output["required"][name] = entry
        else:
            output["optional"][name] = entry

    return yaml.dump(output, default_flow_style=False, sort_keys=False)


# ─── Frontmatter Read/Write ────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(
    r'^---\s*\n(.*?\n)---\s*\n', re.DOTALL)


def read_frontmatter(path):
    """Read and parse YAML frontmatter from a markdown file.

    Returns:
        (data_dict, body_string) — frontmatter as dict, remainder as string.
        Returns ({}, full_content) if no frontmatter found.
    """
    if not os.path.exists(path):
        return {}, ""

    with open(path, encoding="utf-8") as f:
        content = f.read()

    match = _FRONTMATTER_RE.match(content)
    if not match:
        return {}, content

    yaml_str = match.group(1)
    body = content[match.end():]

    data = yaml.safe_load(yaml_str)
    if not isinstance(data, dict):
        return {}, content

    return data, body


def read_frontmatter_validated(path, schema_type):
    """Read frontmatter and validate against schema.

    Raises:
        ValidationError: if frontmatter fails validation
        FileNotFoundError: if file doesn't exist
    """
    data, body = read_frontmatter(path)
    if not data:
        raise ValidationError(f"No frontmatter found in {path}")

    apply_defaults(data, schema_type)
    errors = validate(data, schema_type)
    if errors:
        raise ValidationError(
            f"Frontmatter validation failed in {path}:\n"
            + "\n".join(f"  - {e}" for e in errors))

    return data, body


def write_frontmatter(path, data, schema_type):
    """Write/update YAML frontmatter on a markdown file.

    Validates data against the schema before writing. Preserves the
    markdown body below the frontmatter. Creates the file if it doesn't
    exist (with empty body).

    Raises:
        ValidationError: if data fails schema validation
    """
    apply_defaults(data, schema_type)
    errors = validate(data, schema_type)
    if errors:
        raise ValidationError(
            f"Frontmatter validation failed:\n"
            + "\n".join(f"  - {e}" for e in errors))

    body = ""
    if os.path.exists(path):
        _, body = read_frontmatter(path)

    yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False,
                         allow_unicode=True)
    content = f"---\n{yaml_str}---\n{body}"

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def update_frontmatter(path, updates, schema_type):
    """Merge updates into existing frontmatter and rewrite.

    Raises:
        ValidationError: if merged data fails validation
        FileNotFoundError: if file doesn't exist
    """
    data, body = read_frontmatter(path)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value

    apply_defaults(data, schema_type)
    errors = validate(data, schema_type)
    if errors:
        raise ValidationError(
            f"Frontmatter validation failed after update in {path}:\n"
            + "\n".join(f"  - {e}" for e in errors))

    yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False,
                         allow_unicode=True)
    content = f"---\n{yaml_str}---\n{body}"

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

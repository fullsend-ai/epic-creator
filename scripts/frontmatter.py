#!/usr/bin/env python3
"""CLI for reading, writing, and inspecting artifact frontmatter.

Skills call this script instead of writing YAML by hand, ensuring
schema-validated frontmatter on all artifact files.

Usage:
    # Show schema for a file type
    python3 scripts/frontmatter.py schema strat-task
    python3 scripts/frontmatter.py schema epic-task
    python3 scripts/frontmatter.py schema decomp-summary
    python3 scripts/frontmatter.py schema decomp-review

    # Set/update frontmatter on a file (validates before writing)
    python3 scripts/frontmatter.py set artifacts/epic-tasks/RHAISTRAT-1234-E001.md \
        epic_id=RHAISTRAT-1234-E001 parent_strat=RHAISTRAT-1234 \
        component=model-serving team=model-serving type=Implementation \
        priority=P0 ai_implementability=High ai_implementability_score=5

    # Set nested fields with dot notation
    python3 scripts/frontmatter.py set artifacts/epic-tasks/RHAISTRAT-1234-E001.md \
        gate_failure_impact.action=rewrite \
        gate_failure_impact.fallback_approach="Use alternative approach"

    # Read and validate frontmatter from a file
    python3 scripts/frontmatter.py read artifacts/epic-tasks/RHAISTRAT-1234-E001.md

    # Read frontmatter from multiple files
    python3 scripts/frontmatter.py batch-read artifacts/epic-tasks/*.md
"""

import argparse
import json
import os
import sys

from artifact_utils import (
    SCHEMAS,
    get_schema_yaml,
    read_frontmatter,
    read_frontmatter_validated,
    write_frontmatter,
    update_frontmatter,
    ValidationError,
    _WORKLOAD_FIELDS,
)


def _coerce_value(value_str, field_spec):
    """Coerce a CLI string value to the correct type based on field spec."""
    field_type = field_spec.get("type", "string")

    # null/none clears a field regardless of its type. Previously only string
    # and list accepted it: `gated_by=null` on a dict field reached the schema
    # as the string "null" ("expected dict, got str") and int fields crashed
    # in int() — both hit by revise agents in the 2026-08-19 eval run, one of
    # which resorted to editing this script mid-run to proceed.
    if value_str.lower() in ("null", "none"):
        return None

    if field_type == "bool":
        if value_str.lower() in ("true", "1", "yes"):
            return True
        if value_str.lower() in ("false", "0", "no"):
            return False
        raise ValueError(f"Cannot convert '{value_str}' to bool")

    if field_type == "int":
        try:
            return int(value_str)
        except ValueError:
            raise ValueError(
                f"Cannot convert '{value_str}' to int (use 'null' to clear)")

    if field_type == "list":
        if value_str.startswith("["):
            import json
            try:
                return json.loads(value_str)
            except json.JSONDecodeError:
                # Agents commonly write [A-1,B-2] without quotes, which is not
                # JSON. Fall back to comma-splitting the bracket contents
                # instead of failing the whole set command.
                inner = value_str.strip()[1:-1] if value_str.strip().endswith("]") \
                    else value_str.strip()[1:]
                return [v.strip().strip("'\"") for v in inner.split(",") if v.strip()]
        return [v.strip() for v in value_str.split(",") if v.strip()]

    if field_type == "dict":
        import json
        try:
            parsed = json.loads(value_str)
        except json.JSONDecodeError:
            raise ValueError(
                f"Cannot convert '{value_str}' to dict: pass a JSON object "
                f"(e.g. '{{\"action\": \"rewrite\"}}'), 'null' to clear, or set "
                f"nested fields individually with parent.child=value")
        if not isinstance(parsed, dict):
            raise ValueError(f"Expected a JSON object for dict field, got "
                             f"{type(parsed).__name__}")
        return parsed

    if field_type == "string":
        return value_str

    return value_str


def _detect_schema_type(path):
    """Detect schema type from file path."""
    basename = os.path.basename(path)

    if "/epic-reviews/" in path or path.startswith("epic-reviews/"):
        return "decomp-review"
    if "/epic-tasks/" in path or path.startswith("epic-tasks/"):
        if basename.endswith("-decomposition.md"):
            return "decomp-summary"
        return "epic-task"
    if "/strat-tasks/" in path or path.startswith("strat-tasks/"):
        return "strat-task"
    return None


def cmd_schema(args):
    """Print the schema for a file type."""
    try:
        yaml_str = get_schema_yaml(args.schema_type, args.workload)
        print(yaml_str)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_read(args):
    """Read and display frontmatter from a file."""
    if not os.path.exists(args.file):
        print(f"Error: {args.file} not found", file=sys.stderr)
        sys.exit(1)

    schema_type = args.schema_type or _detect_schema_type(args.file)

    if schema_type:
        try:
            data, _ = read_frontmatter_validated(args.file, schema_type)
        except ValidationError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        data, _ = read_frontmatter(args.file)
        if not data:
            print(f"Error: no frontmatter found in {args.file}",
                  file=sys.stderr)
            sys.exit(1)

    json.dump(data, sys.stdout, indent=2, default=str)
    print()


def cmd_set(args):
    """Set/update frontmatter fields on a file."""
    schema_type = args.schema_type or _detect_schema_type(args.file)
    if not schema_type:
        print("Error: cannot detect schema type from path. "
              "Use --schema-type.", file=sys.stderr)
        sys.exit(1)

    schema = SCHEMAS[schema_type]

    data = {}
    for field_value in args.fields:
        if "=" not in field_value:
            print(f"Error: expected field=value, got '{field_value}'",
                  file=sys.stderr)
            sys.exit(1)

        field_name, value_str = field_value.split("=", 1)

        # Handle dot notation for nested fields (e.g., gate_failure_impact.action=rewrite)
        if "." in field_name:
            parts = field_name.split(".", 1)
            parent_name, child_name = parts
            if parent_name not in schema:
                print(f"Error: unknown field '{parent_name}'",
                      file=sys.stderr)
                sys.exit(1)
            parent_spec = schema[parent_name]
            if parent_spec.get("type") != "dict":
                print(f"Error: '{parent_name}' is not a dict field",
                      file=sys.stderr)
                sys.exit(1)
            nested_fields = parent_spec.get("fields", {})
            if child_name not in nested_fields:
                print(f"Error: unknown field '{field_name}'",
                      file=sys.stderr)
                sys.exit(1)
            coerced = _coerce_value(value_str, nested_fields[child_name])
            if parent_name not in data:
                data[parent_name] = {}
            data[parent_name][child_name] = coerced
        else:
            if field_name not in schema:
                print(f"Error: unknown field '{field_name}' for schema "
                      f"'{schema_type}'", file=sys.stderr)
                sys.exit(1)
            data[field_name] = _coerce_value(value_str, schema[field_name])

    if args.source:
        try:
            source, _ = read_frontmatter_validated(args.source, "strat-task")
            inherited = {name: source[name] for name in _WORKLOAD_FIELDS if name in source}
            for name, value in inherited.items():
                if name in data and data[name] != value:
                    raise ValidationError(f"Explicit {name} conflicts with source provenance")
            if os.path.exists(args.file):
                existing, _ = read_frontmatter(args.file)
                for name in _WORKLOAD_FIELDS:
                    if name in existing and existing[name] != inherited.get(name):
                        raise ValidationError(f"Existing {name} conflicts with source provenance")
            identity = "parent_strat" if schema_type in ("epic-task", "decomp-summary") else "strat_id"
            if identity in data and data[identity] != source["strat_id"]:
                raise ValidationError("Generated identity conflicts with source strategy")
            data = {**inherited, **data}
        except (OSError, ValidationError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

    if schema_type == "epic-task" and "component" in data:
        try:
            existing, _ = read_frontmatter(args.file)
            combined = {**existing, **data}
            if combined.get("workload_id"):
                from workload import get_workload
                get_workload(combined["workload_id"]).planning.validate_component(data["component"])
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

    if os.path.exists(args.file):
        try:
            update_frontmatter(args.file, data, schema_type)
        except ValidationError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            write_frontmatter(args.file, data, schema_type)
        except ValidationError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    print(f"OK: {args.file}")


def cmd_batch_read(args):
    """Read frontmatter from multiple files and output as JSON array."""
    results = []
    for filepath in args.files:
        if not os.path.exists(filepath):
            results.append({"_file": filepath, "_error": "not found"})
            continue

        schema_type = _detect_schema_type(filepath)
        if schema_type:
            try:
                data, _ = read_frontmatter_validated(filepath, schema_type)
                data["_file"] = filepath
                results.append(data)
            except ValidationError as e:
                results.append({"_file": filepath, "_error": str(e)})
        else:
            data, _ = read_frontmatter(filepath)
            if data:
                data["_file"] = filepath
                results.append(data)
            else:
                results.append({"_file": filepath, "_error": "no frontmatter"})

    json.dump(results, sys.stdout, indent=2, default=str)
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Artifact frontmatter CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # schema
    p_schema = subparsers.add_parser("schema",
                                     help="Show schema for a file type")
    p_schema.add_argument("schema_type",
                          choices=list(SCHEMAS.keys()),
                          help="Schema type to display")
    p_schema.add_argument("--workload", help="Include the selected planning policy's field contract")
    p_schema.set_defaults(func=cmd_schema)

    # read
    p_read = subparsers.add_parser("read",
                                   help="Read frontmatter from a file")
    p_read.add_argument("file", help="Path to the markdown file")
    p_read.add_argument("--schema-type", dest="schema_type",
                        choices=list(SCHEMAS.keys()),
                        help="Schema type (auto-detected from path if omitted)")
    p_read.set_defaults(func=cmd_read)

    # set
    p_set = subparsers.add_parser(
        "set", help="Set/update frontmatter fields")
    p_set.add_argument("file", help="Path to the markdown file")
    p_set.add_argument("fields", nargs="+",
                       help="Fields as field=value pairs. Use dot notation "
                            "for nested fields (e.g., "
                            "gate_failure_impact.action=rewrite)")
    p_set.add_argument("--schema-type", dest="schema_type",
                       choices=list(SCHEMAS.keys()),
                       help="Schema type (auto-detected from path if omitted)")
    p_set.add_argument("--source", help="Inherit validated strategy provenance")
    p_set.set_defaults(func=cmd_set)

    # batch-read
    p_batch = subparsers.add_parser("batch-read",
                                    help="Read frontmatter from multiple files")
    p_batch.add_argument("files", nargs="+",
                         help="Paths to markdown files")
    p_batch.set_defaults(func=cmd_batch_read)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

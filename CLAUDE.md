# Epic Creator

Skills for decomposing refined RHAISTRAT strategies into implementation epic DAGs.

## Artifact Conventions

All skills read from and write to the `artifacts/` directory in the working directory.

```
artifacts/
  strat-tasks/                # Fetched strategy files (input)
    RHAISTRAT-1234.md          # Strategy with YAML frontmatter

  epic-tasks/                 # Decomposition output (per-epic files)
    RHAISTRAT-1234-E001.md     # Per-epic files with frontmatter
    RHAISTRAT-1234-E002.md
    RHAISTRAT-1234-decomposition.md  # Summary: DAG, traceability, ambiguity flags

  epic-reviews/               # Adversarial review of decomposition
    RHAISTRAT-1234-decomp-review.md

  decompose-runs/             # Pipeline run reports
    2026-05-04T12-00-00Z.yaml
```

### Frontmatter

All task and review files use YAML frontmatter for structured metadata. Skills must use `scripts/frontmatter.py` to read schemas, set fields, and read validated data — never write YAML by hand.

```bash
# Get schema for a file type
python3 scripts/frontmatter.py schema strat-task
python3 scripts/frontmatter.py schema epic-task
python3 scripts/frontmatter.py schema decomp-review

# Set/update frontmatter on a file
python3 scripts/frontmatter.py set <path> field=value field=value ...

# Read validated frontmatter as JSON
python3 scripts/frontmatter.py read <path>
```

### State Persistence

Long-running skills use `scripts/state.py` to persist state to `tmp/` files so it survives context compression. All skills must use this utility instead of inline bash commands (cat, echo, mkdir) to avoid unnecessary auth prompts.

```bash
python3 scripts/state.py init <file> key=value ...    # Create config file
python3 scripts/state.py set <file> key=value ...     # Update keys in place
python3 scripts/state.py set-default <file> key=value ...  # Set only if key absent
python3 scripts/state.py read <file>                  # Print file contents
python3 scripts/state.py write-ids <file> ID ...      # Write ID list (one per line, deduped)
python3 scripts/state.py read-ids <file>              # Print IDs space-separated
python3 scripts/state.py timestamp                    # Print current UTC time (ISO 8601)
```

## Jira Integration

### Read Operations (fetch strategies)

Read operations use the Jira REST API via `scripts/fetch_strategy.py`.

### Write Operations (attach decomposition, apply labels)

Write operations use `scripts/attach_decomposition.py` to:
- Attach decomposition output as a file to the RHAISTRAT
- Apply `decomp-ready` label

Required environment variables:

```
JIRA_SERVER=https://your-site.atlassian.net
JIRA_USER=your-email@example.com
JIRA_TOKEN=your-api-token
```

## Jira Field Mappings

### RHAISTRAT Project
- **Project**: `RHAISTRAT`
- **Issue Type**: `Feature`
- **Labels applied by this pipeline**: `decomp-ready`

## Pipeline Execution Constraint

When `tmp/pipeline-state.yaml` exists and the phase is not DONE:

1. A text-only response (no tool call) during pipeline execution terminates the CI process.
2. After launching each wave of agents, your next Bash call MUST be
   `python3 scripts/pipeline_state.py wait-for-wave`. This is a blocking
   synchronization barrier that reads artifact files on disk. On exit 3,
   re-run the same command.
3. Do not wait for agent-completion notifications — the wait-for-wave command
   is unrelated to the Agent tool's notification system.

## Eval Dataset Anonymization

Files under `eval/cases/` and `eval/shared/` must contain no PII — the same
policy as rfe-creator and strat-creator. Never commit real customer/partner
names, individual names, email addresses, internal Slack / Google Docs / Miro /
workshop links, or other personally identifiable or confidential information.

The dataset has two tiers: **synthetic** (`case-001..020`, `RHAISTRAT-90xx`,
authored from scratch) and **real** (`case-021..030`, `RHAISTRAT-80xx`, verbatim
anonymized snapshots of signed-off production strategies). Every case declares
its tier in `annotations.yaml`.

### Real-tier pipeline

Raw content never enters a committed file, and the fidelity path never passes
through a model — the body is the verbatim Jira description with substitutions
applied by script:

```bash
# 1. snapshot verbatim from an auto-saved Atlassian MCP result (git-ignored)
python3 eval/scripts/extract_strategy.py <mcp-tool-result.json>
# 2. per strategy, an LLM pass writes eval/.raw/<KEY>.tokens.yaml (semantic PII)
#    and <KEY>.meta.yaml (case slug, arch files, grader notes)
# 3. resolve originals -> fictional values globally in eval/.entity-map.yaml,
#    then build the cases (re-keys tickets, redacts links, provisions .context)
python3 eval/scripts/build_real_cases.py
# 4. gate: emails, non-public URLs, real-range ticket keys, entity-map originals
python3 eval/scripts/scan_real_pii.py
```

`eval/.raw/` and `eval/.entity-map.yaml` are git-ignored and hold the only copies
of the originals. Ticket keys are re-keyed into 7000-7899; `scan_real_pii.py`
flags any Jira-prefixed key outside 7000-9999, so high-numbered real keys cannot
slip through. Two lessons already paid for: keep public vendor names (a cloud
provider whose name is also a CRD kind was wrongly anonymized, contradicting the
vendored public architecture docs), and never re-key technical identifiers that
merely look like Jira keys (`KEM-768`, `AC-2`, `SHA-256`).

The rules below govern any future case, especially ones derived from real
strategies:

- **Replacements you invent — web-search every one** (bare-name query only) to
  catch collisions with real companies; prefer clearly invented compounds.
  rfe-creator shipped six "fictional" names that turned out to be real
  companies and had to redo them.
- **Originals from real data — keep them out of search engines.** Never search
  emails, usernames, internal links, or any name combined with context.
  Individuals are replaced unconditionally — there is nothing to verify.
- **Keep names consistent across files** (same real entity → same fictional
  entity), including input.yaml, the strat-task file, and annotations.yaml.
- **Kept as-is (not PII):** Red Hat and its products, upstream/OSS projects,
  public vendors and hardware, generic team names, RHAISTRAT ticket keys
  (use the synthetic 9xxx range for invented ones), public GitHub/docs URLs.
- `eval/runs/` is git-ignored: run artifacts contain local machine paths and
  raw model transcripts and must not be committed or shared unscrubbed.

## Architecture Context

The pipeline fetches architecture context from [opendatahub-io/architecture-context](https://github.com/opendatahub-io/architecture-context) into `.context/architecture-context/` at bootstrap. A component is "in the platform" if it has an architecture context file.

Architecture context is used during decomposition to:
- Validate component existence and dependencies
- Determine upstream dependency maturity
- Inform AI implementability scoring (Signal 9: architecture claims)

## Isolated repository mapping

After the final decomposition review/revision, `MAP_REPOSITORIES` dispatches
`skills/repo-map/SKILL.md` through a strategy-specific agent. Mapping results
live in `artifacts/repository-mappings/{ID}.json`. Inputs remain unchanged.
The result includes a digest of the final drafts, strategy and Architecture
Context; changed inputs require a fresh selection.

The publisher consumes resolved repository URLs for new Epics. Set
`EPIC_REPOSITORY_URL_FIELD` to the existing RHAI Epic URL custom-field ID;
the publisher verifies its URL type and create-screen availability. Historical
RHAI configuration uses `customfield_10933`; verify it before activation.
An unresolved result creates an Epic without a URL and adds an explanatory
Jira comment. Already-published Epics and manually supplied Jira URLs remain
untouched. Missing/incompatible URL configuration also leaves the field unset
and adds a comment. Mapper result files are not Jira credentials. Pending repository-warning
comments use an optional Epic metadata marker so a retry reuses the created
issue and deduplicates its comment; unrelated existing Epics are skipped.

Mapper-only synthetic cases and exact expected-answer scoring are in
`eval/repo-map.yaml`. Agent runs are required to establish mapping correctness;
Python tests validate the result and publication contracts only.

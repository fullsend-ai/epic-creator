# RHAI repository-oriented epics

This configuration extends the current RHAISTRAT → RHAI workflow. It does not
require workload descriptors or PR #40. The architectural decision is tracked in
[proposed ADR 0002](https://github.com/fullsend-ai/epic-creator/pull/45).

## Configure the RHAI sources

Edit [`config/rhai-repositories.yaml`](../config/rhai-repositories.yaml). The
configuration is checked against its JSON schema when pipeline commands run.

- `inventory` selects a YAML file listing approved contribution destinations,
  their canonical identities, stable `target-repo-<inventory_key>` labels and
  approval references. Start with the schema in
  [`config/_schema/repository-inventory.schema.json`](../config/_schema/repository-inventory.schema.json).
- `sources` selects Git repositories and paths. Each has an explicit ref, owner
  and `authoritative` or `supporting` role. HTTPS repository, file and directory
  links are accepted. Authentication uses the host Git credential helper.
- `guidance` optionally selects a Markdown file explaining how to interpret the
  sources. It cannot override the approved inventory or establish ownership
  without citations to authoritative evidence.

`url: self` reads the committed files in this Epic Creator checkout at the
configured ref. Commit bundled source changes before starting a run; uncommitted
edits are not Git evidence. Other sources can use HTTPS Git URLs and paths.
Fetched refs are pinned to commits and each evidence file is hashed. The loader
rejects unavailable sources and changed snapshots instead of choosing a default.

Architecture context comes from Epic Creator's existing bootstrap,
`scripts/fetch-architecture-context.sh`, which prepares the latest RHOAI release
and overlays in `.context/architecture-context/`. Repository planning snapshots
that checkout's selected paths at its current Git commit and records file hashes.
It does not fetch a second architecture repository or independently select a
release. A missing checkout blocks planning; refreshing it requires a new plan
and review. The `architecture-context` source ID is reserved for this snapshot.

The bundled [mapping snapshot](../config/rhai/repository-mapping.md) is a Markdown
conversion of Component Mapping Results, with its original URL, retrieval time
and document revision. It describes downstream repositories. Populate
[`repository-inventory.yaml`](../config/rhai/repository-inventory.yaml) and
[`contribution-rules.md`](../config/rhai/contribution-rules.md) with confirmed
contribution destinations and ownership relationships before changing `enabled`
to `true`. The initial inventory is intentionally empty and activation is off.
Source owner placeholders also need confirmation. Verify that the release and
overlays selected by the shared architecture bootstrap fit the strategy.

## Run the existing pipeline

After committing the configuration and sources, install `requirements.txt` and
use the existing `epic-decompose` skill. Its bootstrap runs
`fetch-architecture-context.sh` and `fetch_components.py`. Repository mode inserts
a stage before drafting:

```text
FETCH → PLAN_REPOSITORIES → DECOMPOSE → REVIEW_DECOMP → revision/review → REPORT
```

The planner extracts requirements and acceptance conditions, resolves each work
item against the configured evidence, then finalizes repository scopes. Drafting
creates one Implementation epic per repository in each effective scenario.
Manager and cloudmanager changes in one operator repository stay in one epic,
including their components, owners, tests and documentation. Dashboard changes
in another repository get a separate epic and retain real dependencies. Genuine
Investigation questions and conditional outcomes remain explicit.

When a strategy names repositories, those names constrain the work they refer to;
the planner still checks them against the approved inventory and evidence.
Missing, conflicting or ambiguous destinations remain unresolved and block drafts
for that strategy. Other strategies in the batch continue. Run reports retain the
blocking status and repository-plan location.

For troubleshooting a single fetched strategy:

```bash
python3 scripts/repository_plan.py prepare --source RHAISTRAT-1234
# The planner writes artifacts/repository-plans/RHAISTRAT-1234-proposal.json.
python3 scripts/repository_plan.py finalize --source RHAISTRAT-1234
python3 scripts/repository_plan.py validate --source RHAISTRAT-1234
```

Finalization returns 0 for a ready plan, 2 for retained unresolved work, and 1 for
malformed output. The dispatcher prepares the context automatically. Do not edit
the final plan or its cached evidence by hand.

## Review and publish

Review checks the actual modification scope and acceptance criteria against the
original strategy and pinned evidence. Repository fields alone cannot prove a
correct target. Reviews bind the exact plan and decomposition content; later
changes require fresh review. Changes to configuration, prompts, source strategy,
mapping, inventory or guidance require a new run and reviewed plan.

Use the existing submission commands after review:

```bash
python3 scripts/submit.py --dry-run RHAISTRAT-1234
python3 scripts/submit.py RHAISTRAT-1234
```

Both modes use the publication phases in `scripts/submit.py`: create epics,
link dependencies, attach metadata and branch plans, then label the strategy.
`scripts/repository_submit.py` adds repository preflight and retry recovery to
those phases; it does not run a separate publisher. Repository configuration
contains sources and planning policy. Jira projects, issue types and link types
continue to use Epic Creator's fixed RHAI conventions.

Dry-run performs local validation without Jira writes. Publication also checks
the current approved Jira source and content before writing. Each initial create
includes its repository label and all components. Published issues retain the
canonical target, source hashes, inventory revision, scope and work evidence.
Conditional branch plans are attached to their Investigation gate.

Keep `artifacts/submissions/` across retries. The publisher journals creation
intent before sending a create request and recovers only an issue matching the
same identity, reviewed content and target. An unknown result with no indexed
match blocks another create until it can be reconciled. Jira labels do not enforce
uniqueness across independent workspaces; serialize publication for a strategy.
Disabling repository mode cannot send bound artifacts through legacy submission.

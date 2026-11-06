# RHAI repository-oriented epics

This configuration extends the current RHAISTRAT → RHAI workflow. It does not
require workload descriptors or PR #40. The architectural decision is tracked in
[proposed ADR 0002](https://github.com/fullsend-ai/epic-creator/pull/45).

## Configure the RHAI sources

Edit [`config/rhai-repositories.yaml`](../config/rhai-repositories.yaml). The
configuration is checked against its JSON schema when pipeline commands run.

- `inventory` selects a YAML file listing approved contribution destinations,
  their canonical identities and approval references. Historical
  `target-repo-<inventory_key>` labels remain part of the inventory input format;
  the publisher does not add these labels to Jira. Start with the schema in
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
item against the configured evidence, and assigns implementation work to explicit
planned PRs. Drafting creates **one Implementation Epic per planned PR**, with
exactly one target repository per Epic. Several planned PRs can target the same
repository; their Epics stay separate and retain real dependencies. Work items,
incidental documentation and tests that belong to one PR stay together. Genuine
Investigation questions and conditional outcomes remain explicit.

Every implementation work item requires a stable `planned_pr` ID, such as
`PR-001`; items in the same proposed PR share that ID. The finalizer rejects a
PR spanning multiple repositories or inconsistent conditional branches/gates.
Each finalized Implementation scope has `scope_id: pr/PR-001` and `planned_pr:
PR-001`; the Epic copies the scope ID as `repository_scope`. For example, two
Feast PRs (API and operator provisioning) plus a platform-operator integration
PR produce three Epics across two repositories, not two repository-wide Epics.

Repository policy mode is `one-implementation-epic-per-planned-pr`. Older
repository-only configurations/proposals must be regenerated with this policy
and the required PR assignments. Previously reviewed plans are not migrated
automatically; run planning, drafting and review again.

When a strategy names repositories, those names constrain the work they refer to;
the planner still checks them against the approved inventory and evidence.
Missing, conflicting or ambiguous destinations remain unresolved. Their planned
PR scopes remain available for drafting and creation with no guessed repository.
Each affected Epic carries the warning: "The target repository is missing or
unresolved. Confirm and update the target repository before starting work on
this epic." Submission includes this warning in its description and adds a Jira
comment, checking existing comments on retries. Dependencies and all assigned
work stay intact. Plans with pending resolution set `partial: true`; the source
is not marked fully decomposed. Invalid source bindings, evidence or global plan
conflicts still block creation.

For troubleshooting a single fetched strategy:

```bash
python3 scripts/repository_plan.py prepare --source RHAISTRAT-1234
# The planner writes artifacts/repository-plans/RHAISTRAT-1234-proposal.json.
python3 scripts/repository_plan.py finalize --source RHAISTRAT-1234
python3 scripts/repository_plan.py validate --source RHAISTRAT-1234
```

Finalization returns 0 when eligible scopes are ready (including partial plans),
2 when drafting is entirely blocked, and 1 for malformed output. The dispatcher prepares the context automatically. Do not edit
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
the current approved Jira source and content before writing. Each resolved initial create
sets the canonical HTTPS repository URL in the existing Jira URL field
`customfield_10933` (display name `URL`) and includes all components. The publisher
selects that exact ID from RHAI Epic create metadata and verifies its URL type.
The field must be available on the RHAI Epic create screen (and its edit/view
screens for normal use). Missing, duplicate or incorrectly typed metadata blocks
publication before any writes. The publisher does not create or rename fields;
no new `repo-url` field is required.

Unresolved Epics and Investigations leave `customfield_10933` empty. Unresolved Epics keep
the existing warning in their description and comment. Repository labels are no
longer added; unrelated classification and retry-identity labels remain. Recovery
verifies `customfield_10933` against the reviewed target and stops on missing or changed
values rather than silently rewriting a published epic. Existing publications
with the old label contract require explicit reconciliation; the publisher does
not migrate or relabel existing Jira issues. Published issues retain the
canonical target, source hashes, inventory revision, scope and work evidence.
Conditional branch plans are attached to their Investigation gate. Publishing
eligible Epics from a partial plan does not apply the source's fully decomposed
label. The unresolved work remains recorded for later planning and review.

Keep `artifacts/submissions/` across retries. The publisher journals creation
intent before sending a create request and recovers only an issue matching the
same identity, reviewed content and target. An unknown result with no indexed
match blocks another create until it can be reconciled. Jira labels do not enforce
uniqueness across independent workspaces; serialize publication for a strategy.
Disabling repository mode cannot send bound artifacts through legacy submission.

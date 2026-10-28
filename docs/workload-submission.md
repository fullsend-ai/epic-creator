# Workload submission and recovery

`submit.py --workload ID` submits reviewed, typed artifacts using the selected
workload's destination and relationship policy. Both shipped descriptors create
RHAI Epics under the original source issue, use Blocks dependency links, and retain
the existing RHAI labels. Initiative submission keeps the
RHOAIENG source key; it does not create a RHAISTRAT clone.

The planning skill still ends at REPORT. Submission is a separate explicit step;
production Fullsend configuration and model quality evaluation remain outstanding.
The architecture remains [Proposed](ADRs/0001-descriptor-driven-planning-workloads.md).

## Run

First fetch and plan with the current descriptor. Adding `submission` changes the
descriptor digest, so typed artifacts created before this change need a fresh fetch
and planning run. Old untyped RHAISTRAT artifacts retain the existing submission
command. Typed artifacts require explicit workload selection.

Use the existing Jira environment variables through your normal credential setup.
The dry run requires no credentials, makes no network calls, and writes no journal:

```bash
python3 scripts/submit.py --workload rhai-initiative --dry-run RHOAIENG-9001
python3 scripts/submit.py --workload rhai-initiative RHOAIENG-9001

# Alternate artifact root, or all sources belonging to the selected workload:
python3 scripts/submit.py --workload rhai-initiative --artifacts-dir artifacts --all --dry-run
```

The key is synthetic and illustrative. A dry run shows the destination, original
source relationship, dependency policy, components, labels and recovery identity.
Live submission requires `JIRA_SERVER`, `JIRA_USER`, and `JIRA_TOKEN` and the normal
permissions to read the source, search recovery labels, create/link destination
issues, add labels and upload attachments.

## Descriptor contract

The optional `submission` block contains:

```yaml
submission:
  project: RHAI
  issue_type: Epic
  parent:
    relation: parent
  dependency_link_type: Blocks
  labels:
    created: epic-creator-auto-created
    investigation: epic-creator-investigation
    implementation_prefix: epic-creator-impl-
    ai_prefix: epic-creator-ai-impl-
    needs_component: epic-creator-needs-component
```

`parent.relation: parent` sets Jira's parent field to the original source key.
`parent.relation: link` requires `parent.link_type` and links source to destination
using that type instead. Dependencies retain the existing inward=blocker,
outward=blocked convention. The source's `processed_label` controls completion.
The component cache comes from `planning.components`; its project must match the
submission destination. Typed planning writes and submission require a populated
cache and exact canonical component names. Missing caches and unknown components
stop before Jira writes, including for conditional branches and dry runs. Refresh
the cache with `scripts/fetch_components.py --workload ID`. The untyped legacy
submission path retains its existing component warnings and fallback behavior.

## Validation and completion

Before the first Jira write, validate the complete local source, summary, main and
conditional epic set, dependency DAG, passing review and matching source/workload/
policy provenance. Malformed artifacts are errors, rather than silently skipped.
Conditional branches must point to an existing main Investigation epic; their
plans are attached to that gate and do not become Jira issues.
A main Epic may also name an Investigation in `gated_by`; that gate must exist
in the main plan and be included in the Epic's dependencies.

Live execution rechecks source project/type, approval labels and the newest exact
strategy attachment against the fetched attachment ID and timestamp. For
description-backed strategies it compares the current description and rejects a
new attachment superseding that description. It resolves
all main epic recovery identities before creating any epic for that source.
Foreign projects/types/parents, conflicting keys, changed reviewed content,
duplicate identities, and incomplete search results stop submission.

Existing keys are retained in epic frontmatter. Missing parent relationships,
configured labels, dependency/source links and evidence attachments are reconciled
on rerun. The source is marked processed only after every phase succeeds.

## Recovery

Each create request includes two labels atomically with the issue:

- `epic-creator-id-<sha256>`: stable identity from workload ID, source key and local
  epic ID. Descriptor version is recorded separately in artifact provenance.
- `epic-creator-plan-<sha256>`: fingerprint of the reviewed epic, its source
  provenance, gated branch content and intended submission policy/payload.

The local journal is `artifacts/submissions/<workload>/<source>.json`. It records
intent before creation and saves returned keys atomically. A local file lock
prevents overlapping submitters using that artifact root. Retain the entire
artifact directory, including this journal, across retries.

Issue creation and attachment upload are sent once per attempt. After a lost
response, rerun the same command: a unique matching issue can be recovered from
its label, checked against the plan fingerprint, and restored to frontmatter.
The remaining links, labels and attachments are then completed.

Jira search is [eventually consistent](https://developer.atlassian.com/cloud/jira/platform/search-and-reconcile/).
If an attempted create has no known key and search currently finds nothing,
submission stops with `prior create outcome unknown`. Wait for indexing and retry.
If it still cannot be resolved, an operator must establish the actual Jira outcome
and repair the journal/artifact key. Do not discard an uncertain intent and blindly
retry creation. This also means a request that failed before reaching Jira may need
manual reconciliation; safety takes precedence over automatic recreation.

Labels do not enforce global uniqueness. Independent workspaces must be serialized
by the caller/runner, and the authenticated account must be able to see the
destination issues. A lost journal plus an unindexed or invisible issue cannot
provide duplicate prevention. Conflicting/duplicate matches require human repair.

Evidence attachments use content-addressed filenames, such as
`RHAI-9001-frontmatter-<sha256>.yaml` and
`RHAI-9001-branch-a-plan-<sha256>.md`. Identical evidence is skipped on rerun;
existing attachments are retained. Submission never deletes attachments or moves
an epic away from a different existing parent. Legacy cleanup/re-decomposition
commands have not been expanded; do not use them to reset a typed submission.

## Tests

For CLI-to-HTTPS end-to-end checks and optional real-model planning without live
Jira writes, see [workload verification](workload-verification.md).

```bash
python3 -m pytest -q tests/test_workload_submit.py
python3 -m pytest -q
```

The submission tests exercise real schemas, frontmatter, CLI, journal and HTTP
payload construction against an in-memory synthetic Jira boundary. They cover
no-network dry runs, destination policies, repeated runs, conditional plans,
lost responses, key-persistence failures, each partial phase, stale searches,
duplicate/conflicting identities and invalid inputs. These establish local
submission behavior; they do not establish production Jira configuration or
model quality. No live Jira issues were created during implementation.

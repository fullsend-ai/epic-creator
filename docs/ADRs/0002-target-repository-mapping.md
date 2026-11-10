---
title: 'Assign one target repository to each implementation epic'
status: Proposed
topics:
  - workloads
  - jira
  - repositories
  - provenance
---

# ADR-0002: Assign one target repository to each implementation epic

Date: 2026-10-22
Updated: 2026-11-10

## Status

Proposed.

## Context

Epic Creator must identify where the work in each implementation Epic should be
contributed. A Jira project or Component can span several repositories, while a
product repository, fork or build source is not necessarily the approved
contribution destination. Assigning a repository after drafting can also leave
an Epic whose acceptance criteria require changes across multiple repositories.

For RHAI, Architecture Context provides component responsibilities, APIs and
repository relationships needed to select a target. Product/upstream mapping
serves a different purpose: discovering repositories and connecting products,
builds, packages and upstreams. Those relationships can help populate architecture
sources, but do not by themselves explain where a particular code change belongs.

This decision covers Epic Creator's repository planning and Jira publication.
Code-agent dispatch and support for tickets created outside Epic Creator are
separate consumer concerns.

## Decision

### Plan repository scopes before drafting

Run repository planning between strategy retrieval and Epic drafting. Use the
strategy's requirements, acceptance criteria and SME input together with the
selected workload's evidence and approved contribution inventory.

Create one implementation Epic per planned pull request, with exactly one
canonical contribution repository per PR. Several distinct, reviewable PRs may
target the same repository. Preserve all source requirements and dependencies
within and across repository scopes; keep incidental documentation and tests
with their planned PR.

Repository names supplied in the strategy are inputs to validate against the
inventory and evidence. Resolve each implementation scope to a supported target
or explicitly mark it unresolved. Never select a default repository to complete
the plan.

### Use Architecture Context for RHAI repository selection

Use release-appropriate documents and active overlays from
[Architecture Context](https://github.com/opendatahub-io/architecture-context/)
as RHAI's architectural evidence. Reuse the shared bootstrap's checkout rather
than fetching an independent architecture source. The imported RHAI
component/repository mapping remains supporting evidence.

Keep approved contribution destinations and contribution rules in the workload's
inventory and policy. Architecture descriptions, product mappings and fork
relationships do not independently authorize a contribution target. Conflicting
or insufficient evidence leaves the assignment unresolved.

Do not add the [product/upstream mapping project](https://gitlab.cee.redhat.com/ospodata/mapping)
as a direct input to Epic Creator for the current RHAI scope. For broader adoption,
its repository-discovery data may help populate platform definitions and generate
Architecture Context. That is a future integration, not a dependency of this
Epic Creator decision.

Other workloads configure their own maintained evidence and inventory while
using the same planner. Source definitions declare ownership, authoritative or
supporting roles, Git locations, revisions and relevant paths. Project-specific
mapping rules belong in workload data.

### Publish the canonical URL in the existing Jira field

Keep the structured canonical target in the reviewed plan and Epic Markdown.
For each resolved implementation Epic, include its canonical HTTPS repository
URL in the initial Jira create request under a workload-configured existing Jira
URL field. RHAI uses `customfield_10933` (display name `URL`); other Jira projects
configure their own field ID rather than relying on a shared display name.

The current RHAI publisher hardcodes this ID. Workload-level field selection
remains an implementation requirement before supporting other Jira projects.

Select the configured field by ID from the project's Epic create metadata and verify its URL type
before any Jira writes. The field must be available on the Epic create screen.
Do not create a new repository field or use repository labels as the target
contract. Preserve unrelated workflow and classification labels.

Unresolved implementation Epics retain their work and dependencies and omit the
repository URL. Add the following warning to both the Epic description and a
comment, deduplicating the comment across retries:

> The target repository is missing or unresolved. Confirm and update the target
> repository before starting work on this epic.

Their source remains incomplete. Investigation Epics also omit the repository
URL. Invalid plans, stale inputs and failures to retrieve required evidence still
stop processing.

### Human approval before coding

Require a human to approve each Epic before coding starts. Repository assignment,
strategy-level human sign-off and automated decomposition review do not establish
Epic approval. The current publisher does not record or enforce a distinct
per-Epic approval artifact.

Before coding is enabled, the consumer contract must define a durable approval
record identifying the Epic, approving human and approved scope and repository.
The consumer must block dispatch when that record is absent or no longer matches
the current Epic. The human must confirm the target repository and resolve any
missing-repository warning before approval. This ADR establishes the requirement;
consumer enforcement remains separate work.

### Preserve existing submission retry behavior

Preserve existing submission retry behavior and avoid duplicate warning comments.

### URL validation shared by publisher and consumer

Represent a resolved target as `https://<host>/<namespace>/<repository>`, derived
from the reviewed approved-inventory entry. Preserve the canonical inventory
spelling and namespace path. Omit a trailing slash and `.git` suffix; credentials,
ports, query strings, fragments and repository subpaths are not part of the URL.
Do not infer a contribution destination from a redirect or a product mapping.

The publisher validates the selected identity against the bound inventory and
source evidence before creation. An unresolved or Investigation Epic omits the
field; an empty value never authorizes dispatch. Malformed or conflicting values
require explicit reconciliation rather than normalization into a guessed target.

Before dispatch, the consumer must validate the URL against the approved inventory
and the reviewed assignment, and verify the matching human approval. An unknown
or no-longer-approved entry blocks dispatch pending reconciliation and, when bound
inputs change, replanning and review. A syntactically valid URL alone is
insufficient. These consumer checks remain to be implemented in the separate
consumer contract.

## Consequences

- Epic boundaries align with reviewable PRs and a single contribution repository,
  while preserving cross-repository dependencies and strategy coverage.
- Assignments are traceable to reviewed evidence, at the cost of maintaining
  source ownership, architecture context and approved contribution inventories.
- Unresolved work remains visible without inventing a dispatch destination;
  missing targets must be resolved before coding starts.
- Source changes can invalidate a plan and require fresh review.
- The consumer must read and validate the URL before dispatch. Enabling the field
  in Jira and implementing that consumer contract remain separate responsibilities.

## References

- [Architecture Context](https://github.com/opendatahub-io/architecture-context/)
- [Epic Creator repository planning and publication proposal](https://github.com/fullsend-ai/epic-creator/pull/56)

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
Updated: 2026-11-06

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
URL in the initial Jira create request under the existing `URL` field,
`customfield_10933`.

Select the field by ID from RHAI Epic create metadata and verify its URL type
before any Jira writes. The field must be available on the Epic create screen.
Do not create a new repository field or use repository labels as the target
contract. Preserve unrelated workflow and classification labels.

Unresolved implementation Epics retain their work and dependencies, carry an
explicit missing-repository warning, and omit the URL. Their source remains
incomplete. Investigation Epics also omit the repository URL. Invalid plans,
stale inputs and failures to retrieve required evidence still stop processing.

### Preserve the reviewed assignment through publication and recovery

Bind the plan to its strategy inputs, workload policy, inventory revision and
source commits/content digests. Drafting and review use that same evidence.
Changed bound inputs require replanning and review before publication.

Recovery must verify the stored URL and published scope against the reviewed
plan. A missing or conflicting URL on a resolved Epic requires explicit
reconciliation; the publisher must not silently overwrite it or create a
duplicate issue. Automatic migration of existing issues is outside this decision.

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

- [AISDLC-90 — Target repository assignment](https://redhat.atlassian.net/browse/AISDLC-90)
- [AISDLC-547 — Epic Creator implementation](https://redhat.atlassian.net/browse/AISDLC-547)
- [AISDLC-548 — Consumer contract](https://redhat.atlassian.net/browse/AISDLC-548)
- [RHAI source configuration](https://github.com/amastbau/epic-creator/blob/jira/AISDLC-547-repo-url-field/config/rhai-repositories.yaml)
- [Imported component/repository mapping](https://github.com/amastbau/epic-creator/blob/jira/AISDLC-547-repo-url-field/config/rhai/repository-mapping.md)

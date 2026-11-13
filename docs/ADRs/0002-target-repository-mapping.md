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
Updated: 2026-11-11

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

This decision covers Epic Creator's repository planning, validation and Jira
publication. Its scope ends with the published Epic containing a repository URL
or an explicit warning. Fullsend ticket filtering, code-agent dispatch and
approval mechanisms are outside this decision.

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

If drafting or review finds that the repository plan is wrong, even when its
inputs have not changed, return the finding to repository planning as input to
a new plan. For example, concrete acceptance criteria may reveal that a planned
PR requires changes in two repositories. Keep each finalized plan immutable
within its drafting and review pass; do not edit it in place. Draft and review
the replacement plan again before publication. Limit the number of replanning
attempts per source. If the limit is reached without a valid reviewed plan,
report that source as needing replanning, distinct from other review failures,
and preserve the findings for a later attempt.

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
repository URL. Add the following warning as a Jira comment, deduplicating it
across retries. Keep the warning out of the Epic description so it does not leave
stale description text after a human supplies the repository URL:

> The target repository is missing or unresolved. Confirm and update the target
> repository before starting work on this epic.

A human can add or correct the repository URL after publication. Their source
remains incomplete. Investigation Epics also omit the repository URL. Invalid
plans, stale inputs and failures to retrieve required evidence still stop
processing.

### Preserve existing submission retry behavior

Preserve existing submission retry behavior so resuming an interrupted submission
reuses already-created Epics and avoids duplicate Epics and warning comments.

### Validate the repository selection before publication

Represent a resolved target as `https://<host>/<namespace>/<repository>`, derived
from the reviewed approved-inventory entry. Preserve the canonical inventory
spelling and namespace path. Omit a trailing slash and `.git` suffix; credentials,
ports, query strings, fragments and repository subpaths are not part of the URL.
Do not infer a contribution destination from a redirect or a product mapping.

Epic Creator validates each resolved repository against the contribution inventory
and supporting source evidence before publication. Record the selection evidence
and the inventory, mapping and plan revisions or digests needed to verify the
reviewed assignment. Before submission, check that the plan and its bound inputs
still match the reviewed versions. Changes that invalidate that review require
replanning and review before publication.

A missing repository, ambiguous mapping, conflicting evidence or unsupported
candidate leaves the assignment unresolved. Create the Epic without a repository
URL and add the warning as a deduplicated comment so a human can supply or
correct the repository. Preserve the specific reason and
candidate repositories, when available, in the selection evidence. An unresolved
assignment alone does not block Epic creation. Investigation Epics also omit the
repository field.

Malformed plans, stale reviewed inputs and failures to retrieve required evidence
still stop submission; they must not be treated as ordinary unresolved assignments.
These checks belong to Epic Creator. This decision does not require Fullsend to
download evidence attachments or repeat inventory, revision or digest validation.

## Consequences

- Epic boundaries align with reviewable PRs and a single contribution repository,
  while preserving cross-repository dependencies and strategy coverage.
- Assignments are traceable to reviewed evidence, at the cost of maintaining
  source ownership, architecture context and approved contribution inventories.
- Unresolved work remains visible with an empty repository field and a warning
  that enables human correction.
- Source changes can invalidate a plan and require fresh review.
- Drafting or review can expose a wrong plan without source changes; bounded
  replanning carries those findings forward into a new drafting and review pass.
- Publication provides a canonical repository URL or an explicit unresolved
  result. Adoption of that field by coding automation is separate work.

## References

- [Architecture Context](https://github.com/opendatahub-io/architecture-context/)
- [Epic Creator repository planning and publication proposal](https://github.com/fullsend-ai/epic-creator/pull/56)

---
title: 'Assign one target repository to each implementation epic'
status: Accepted
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

Accepted upon merge of this decision and its first-stage implementation.

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

### Deliver isolated selection first

The first delivery, AISDLC-719, invokes an isolated RepoMapper after Epic
Creator finishes drafting and its existing review/revision cycle. The mapper
reads each final draft Epic, its parent strategy and Architecture Context,
then returns one evidence-supported contribution repository or an explicit
unresolved result. The publisher owns Jira writes. The mapper does not change
Epic content, requirement coverage, scope or dependencies.

Missing, ambiguous, conflicting and cross-repository evidence produces an
unresolved result. Never select a default repository to complete a mapping.
Do not overwrite an existing manually supplied URL or retroactively map
already-published Epics.

### Retain repository-aware decomposition as the later goal

A later delivery should let repository evidence inform Epic boundaries so
that each implementation Epic can be completed in one repository. Separate
reviewable deliverables may warrant separate Epics, and several Epics can
target the same repository. AISDLC-719 does not implement this redesign or
claim that adding a URL completes it.

Historical PR destinations describe what teams did, not necessarily what the
work required. Code and an externally published specification can be distinct
deliverables; their historical repository count alone does not prove a
required split. Evaluate targets against deliverables and architectural
evidence, with reviewed expected answers.

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

Broader workload-specific inventory and source-policy configuration remains
future work. The isolated first delivery consumes the existing local
Architecture Context; it does not introduce a new inventory framework.

### Publish the canonical URL in the existing Jira field

Keep the structured target and selection rationale in a mapping artifact.
For each resolved Epic, include its canonical HTTPS repository URL in the
initial Jira create request under a configured existing Jira URL field.
Verify field identity, URL type and Epic create-screen availability. RHAI's
historical field is `customfield_10933`; verify the current configuration
before activation. Do not create or modify shared Jira fields.

When the agent cannot determine the repository, create the Epic without a
URL and add an explanatory Jira comment, following the existing unresolved
comment pattern. Preserve the specific reason and candidate repositories
in the mapping evidence. An unresolved assignment alone does not block Epic
creation. Keep the warning out of the Epic description so a human can supply
or correct the URL without leaving stale description text.

### Preserve existing submission retry behavior

Preserve existing submission retry behavior so resuming an interrupted submission
reuses already-created Epics and avoids duplicate Epics and warning comments.

### Validate selection and existing behavior separately

Validate canonical repository URL syntax and supporting evidence before
publication. Distinguish a valid unresolved result from malformed or
incomplete mapper output. The latter is an execution failure, not evidence
that the model found an unresolved target.

Use agent-eval-harness to evaluate mapping against frozen expected answers.
A wrong repository fails even if it is an allowed candidate. Separately
compare existing decomposition behavior on main and the integrated branch
using the same strategies, context, models and scoring. Preserve requirement
coverage, Epic scope, dependencies and output structure. Main has no mapper,
so its missing repository output is not a mapping correctness baseline.

### Forge scope

This decision concerns repository selection and Jira publication. Canonical
HTTPS targets may be hosted on GitHub, GitLab or other configured forges;
selection must not assume every target is on GitHub. It introduces no forge
API, code dispatch or contribution-authorization changes.

## Consequences

- The first delivery adds a narrow selection boundary without changing the
  existing decomposition workflow's decisions.
- Unresolved work remains visible with an empty URL and an explanatory Jira
  comment that enables human correction.
- Architectural evidence and curated expected answers need maintenance.
- Existing Epics may still span repositories; repository-aware decomposition
  remains a later delivery, not an implicit capability of the mapper.
- Publication provides a target or an explicit unresolved outcome. Adoption
  of the field by coding automation remains separate work.

## References

- [Architecture Context](https://github.com/opendatahub-io/architecture-context/)
- [Original broader repository planning and publication proposal](https://github.com/fullsend-ai/epic-creator/pull/56)
- [AISDLC-719 isolated RepoMapper](https://redhat.atlassian.net/browse/AISDLC-719)
- [Earlier ADR proposal and discussion](https://github.com/fullsend-ai/epic-creator/pull/45)

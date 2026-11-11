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

Epic Creator turns a strategy describing a desired product change into Jira
Epics. Each Epic describes a piece of implementation work, with its scope,
acceptance criteria and dependencies. Today, Epic Creator can draft and publish
that work without identifying the source repository where it should be done.
A person or coding agent picking up the Epic must then find that repository.

The repository cannot always be inferred from the Jira project or component.
For example, Notebooks Server work can involve kubeflow, workbenches or
workbenches-operator. Knowing the component does not tell the implementer
which repository contains the code relevant to a particular change.

Epic Creator already reads Architecture Context: documents describing product
components, their responsibilities and repository relationships. Together with
the strategy and the Epic's actual deliverables, those documents can support a
repository choice. They can also be incomplete or leave several plausible
choices. In that situation, the Epic needs an explanation of the uncertainty
so a person can resolve it; a guessed URL would misdirect the work.

There are two related changes to consider. One is to choose a repository for
an Epic that has already been drafted. The other is to change how work is
divided into Epics so that each Epic fits in one repository. The first is a
smaller change to the existing workflow, but cannot make an existing Epic
with deliverables in several repositories fit into one. For example, an Epic
may include both product code and a specification published elsewhere.
Historical PR destinations show where a team chose to contribute; they do
not establish that those destinations were required.

This ADR chooses the smaller change as the first delivery: add a dedicated
repository-selection agent after drafting and review, then publish its
supported choice in the Epic's existing Jira URL field. If it cannot choose,
publish the Epic with an empty URL and an explanatory comment. Changing Epic
boundaries remains a later delivery. How coding automation selects tickets,
starts work or requests approval is outside this decision.

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

Conflicting or insufficient evidence leaves the assignment unresolved.

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

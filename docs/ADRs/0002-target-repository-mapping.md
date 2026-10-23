---
title: "2. Evidence-backed repository mapping for implementation epics"
status: Proposed
topics:
  - workloads
  - jira
  - repositories
  - provenance
---

# 2. Evidence-backed repository mapping for implementation epics

Date: 2026-10-22

## Status

Proposed — awaiting review. Items listed as open below are not settled decisions.

## Context

AISDLC-90 requires each implementation epic to carry a typed, machine-readable list of approved repository targets and evidence that makes the mapping auditable. Repository relationships can be one-to-many, many-to-one, or many-to-many; the contract must not assume a global one-to-one Jira-component-to-repository mapping.

(Fullsend's current Jira poller uses a workflow-wide `--target-repo` and has no per-epic repository field, so the initial integration can dispatch only one target; a list prepares the contract for future multi-repository support without changing the value type.)

The current AISDLC-4 contract scopes an implementation epic to one repository and one invocation/PR. The assignment therefore must be explicit, checked against approved repository data, and compatible with current single-target dispatch. Repository identity must distinguish provider, host, namespace, and repository name so forks and similarly named repositories are not conflated.

The implementation plan proposes two stages: classify Jira content against a finite, versioned architecture-area catalog, then resolve the classification through deterministic approved mapping data. An architecture-area key is an internal classification result, not an assumed Jira field. The plan does not assign a taxonomy maintainer or settle mapping ownership and precedence.

## Decision drivers

- Preserve the complete repository mapping result, including when it contains more than one target.
- Keep model classification bounded to an approved taxonomy and separate it from repository selection.
- Make repository selection deterministic, auditable, and restricted to approved canonical identities.
- Allow today's Fullsend consumer to dispatch only a valid single-target assignment and fail closed on unsupported results.
- Keep unresolved choices visible for review rather than treating plan proposals as approved policy.

## Options

1. **Use a scalar repository value.** This matches today's execution limit but cannot preserve a multi-repository result and would require changing the value type when fan-out becomes available.
2. **Use a repository list from the start and gate current dispatch.** This preserves complete results while allowing today's consumer to accept only a single-item list.
3. **Enable multi-repository dispatch immediately.** This requires an explicit fan-out contract in Fullsend and a compatible update to AISDLC-4; current behavior does not provide that support.

## Decision

Propose option 2. The list-valued handoff is the contract direction; the mapping sources, ownership, precedence, and exact Jira encoding remain open for review before implementation.

### Classification and repository resolution

- Bind a finite, versioned architecture-area catalog to the selected workload, as proposed in the implementation plan. The catalog contains approved keys and descriptions; its maintainer, change process, and reuse across workloads remain undecided.
- The classifier may return only catalog keys, along with evidence citing the Jira fields that support the classification. The plan proposes one or more keys; cardinality, overlap handling, and the verified Jira input fields remain open. Candidate inputs include summary, description, acceptance criteria, components, labels, and Architectural Context only when those fields are actually available. Do not assume a Jira field named `architecture_area`.
- The classifier does not nominate repositories. A deterministic resolver maps the selected area key or keys and any explicitly configured Jira-field selectors to the full list of approved repository identities.
- A selector must name a verified Jira field and value. Keep derived architecture-area keys separate from Jira-field predicates. Do not use defaults, first-match ordering, or model confidence to discard targets. Selector combination, mapping-source precedence, and conflict handling remain open.
- Evaluate existing Epic Code Gen matching behavior and Forge SDLC as prior art; neither is an approved repository authority by this ADR.

### Repository assignment and evidence

- Every typed assignment uses `repository_targets: RepositoryIdentity[]`, including the single-target case. Targets identify provider, host, namespace, and repository name; they must be unique and present in the approved repository inventory. Never serialize a singleton as a scalar or silently select the first target.
- Preserve these semantic outcomes in artifacts and review:
  - **`resolved`**: a nonempty list of unique approved targets, with classification and mapping evidence.
  - **`unresolved`**: no dispatchable target; include the reason and any candidates. Missing, ambiguous, or conflicting evidence blocks handoff.
  - **`multiple_repositories`**: preserve every required target. This outcome is not dispatchable through the current Fullsend consumer.
- Evidence must identify the Jira fields used, selected catalog key or keys, taxonomy/catalog revision, classifier or prompt revision, repository-map revision and rule, and canonical resolved identities. A change to the taxonomy, mapping data, policy, classifier prompt, or relevant source revision invalidates stale planning evidence.

### Jira and Fullsend handoff

- The Jira representation must preserve the list shape and let Fullsend validate each target. A dedicated Jira field or stable parseable description block are both possible; the exact field and encoding remain open with the AISDLC-4 and Fullsend contract owners.
- The initial Fullsend integration must accept only a list containing exactly one approved target. It rejects absent, malformed, unresolved, unapproved, or multi-item assignments and never truncates a multi-target result.
- Until AISDLC-4 and Fullsend define multi-repository execution, decompose genuine cross-repository deliverables into linked repository-specific implementation epics. Add dependency links only for real prerequisites. Investigation epics remain exempt from the repository-target requirement unless the approved cross-system contract requires otherwise.

## Consequences

- Single-target work uses the same list type as future multi-target work; current execution limits do not erase the full mapping result.
- Review artifacts retain classification and mapping evidence, including unsupported multi-target assignments, while current Fullsend dispatch fails closed unless exactly one approved target is present.
- No mapping source, precedence rule, catalog ownership, or Jira serialization is approved by this proposal. Do not encode RHAI-specific assumptions in the shared contract.
- Before implementation, validate representative RHAI cases and a named non-RHAI pilot with its project stakeholder, approved repository inventory, mapping source, and expected targets. Include ambiguous classification, conflicting Jira signals, and a genuine cross-repository case.
- Any Fullsend consumer change belongs in the Fullsend-owned change and must be reviewed against AISDLC-4. This ADR does not authorize immediate multi-repository dispatch.
- The implementation plan retains task sequencing, repository file paths, and detailed test commands; this ADR records the proposed contract and the architectural decisions that remain open.

## Open decisions before implementation

- Which verified Jira fields are authoritative mapping inputs, including the roles of components and Architectural Context?
- What are the mapping-data owner, source-of-truth, precedence, and conflict-resolution rules? How do area keys combine with explicit field/value selectors?
- What classification cardinality is allowed, and how are overlapping, ambiguous, or conflicting classifications represented?
- Who maintains the architecture-area catalog, how are changes reviewed, and what is shared across workload-specific catalogs?
- What exact Jira field or description encoding carries the list, and how is it versioned compatibly with AISDLC-4 and Fullsend?
- Which non-RHAI workload is the pilot, who validates it, and what stakeholder-approved examples establish expected mappings?
- Which Fullsend-owned change will consume and validate the list, and what explicit contract enables future multi-repository execution?

Do not implement the classifier or resolver until these decisions are either settled or explicitly accepted as unresolved gates in the implementation plan.

## References

- [AISDLC-90 — Tag implementation epics with target repositories](https://redhat.atlassian.net/browse/AISDLC-90)
- [AISDLC-4 — Epic-to-repository contract](https://redhat.atlassian.net/browse/AISDLC-4)
- [AISDLC-90 implementation plan](https://gitlab.cee.redhat.com/amastbau/ai-sdlc-workbench/-/tree/jira/AISDLC-90-tag-implementation-epics-with-target-repositories)
- [Fullsend poll CLI at the inspected revision](https://github.com/fullsend-ai/fullsend/blob/a2be1678c5dbd9361a77f04de5ec260320e4fb9f/internal/cli/poll.go)
- [Fullsend Jira poll types at the inspected revision](https://github.com/fullsend-ai/fullsend/blob/a2be1678c5dbd9361a77f04de5ec260320e4fb9f/internal/jirapoll/types.go)
- [Fullsend PR #8616](https://github.com/fullsend-ai/fullsend/pull/8616) — reference for the repository's ADR format; its decision is separate from this proposal.
- [Epic Code Gen](https://github.com/ederign/epic-code-gen) and [Forge SDLC](https://github.com/forge-sdlc/forge) — prior art to evaluate, not approved mapping authorities.

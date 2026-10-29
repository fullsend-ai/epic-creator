---
title: 'Assign one target repository to each implementation epic'
status: Proposed
topics:
  - workloads
  - jira
  - repositories
  - routing
  - provenance
---

# ADR-0002: Assign one target repository to each implementation epic

Date: 2026-10-22
Updated: 2026-10-29

## Status

Proposed. Confirm the Jira field and workload contract with the Jira and Fullsend owners before implementation.

## Context

AISDLC-90 needs Epic Creator to identify the repository where code generation should run. The AISDLC-4 contract requires each implementation epic to resolve to one canonical repository from an approved inventory, with one code-agent invocation and one pull request.

The current Fullsend poller accepts a workflow-wide repository and does not read a per-epic target. Epic Creator and Fullsend therefore need to agree on the target field, its serialization, and the harness query.

Repository mapping needs project-specific evidence. A component catalog or Architecture Context entry can describe repositories and their relationships without establishing which repository is the approved target for a Jira epic. The RHAI mapping document is a candidate source; verify its contents and ownership before treating it as authoritative.

AISDLC-61 provides the workload extension model: a selected workload has a validated definition that configures workload-specific behavior. Repository mapping sources need the same flexibility so each project can provide its own mapping document and Architecture Context location.

## Decision drivers

- Resolve each implementation epic to exactly one approved repository.
- Make the assignment traceable to Jira fields and versioned mapping evidence.
- Allow each workload to supply its own mapping and architecture sources.
- Keep harness queries consistent with the target assigned by Epic Creator.
- Stop dispatch when evidence is missing, conflicting, stale, or ambiguous.

## Options considered

### Mapping

1. **LLM using repository descriptions:** flexible, but descriptions alone may not distinguish related repositories or establish an authoritative target.
2. **Static Jira-component-to-repository table:** deterministic and reviewable, but requires verified one-to-one mappings and an owner for updates.
3. **Architecture Context as the sole source:** useful for architecture and repository metadata, but the inspected RHODS Operator entry does not define a Jira-to-target rule.
4. **Grounded LLM with workload-provided sources:** combines Jira fields, the approved repository inventory, a mapping document, and relevant Architecture Context. It can interpret the evidence, but requires source validation, provenance, and abstention behavior.

### Jira and harness routing

1. **Separate Jira project per repository:** simple harness queries where project boundaries already match repository boundaries.
2. **Shared project with a target field or Component filter:** supports shared projects; a dedicated single-valued target field is clearer than relying on Component.
3. **Project scope plus an explicit target field:** project-specific queries can narrow the results while the target field remains the dispatch authority.

## Decision

Propose mapping option 4 and routing option 3.

- Store one scalar **target_repository** on each implementation epic. Its value is the canonical provider, host, namespace, and repository identity from the approved inventory. Prefer a dedicated single-valued Jira field. Use a parseable description block only if the field is unavailable and the AISDLC-4 owners approve that representation.
- Put source references in the selected workload's validated definition: the mapping-document URL and the Architecture Context repository, path, and revision. The workload configuration selects that definition. Retrieve and inject the configured source content at runtime; do not hardcode RHAI URLs in shared Epic Decomposer code. Each project may supply different sources.
- Each project's workload definition declares the owner and role of every mapping source: authoritative contribution-target data or supporting context. The project may assign either role to its mapping document or Architecture Context. Authoritative target data takes precedence over supporting repository metadata; upstream, fork, or manifest-source relationships alone do not establish a contribution target. Conflicting authoritative sources leave the result unresolved. The LLM must follow these declared roles when interpreting both sources.
- Give the resolver verified Jira fields, the approved repository inventory, and the retrieved source content. It returns one canonical repository and evidence references. Deterministic validation checks the target against the inventory and records workload identity, source URLs and revisions or digests, Jira fields, cited sections, and inventory revision. Inventory membership validates that a repository is allowed; the cited authoritative evidence must also support its selection for this epic.
- Bind the selected target and its evidence to a mapping snapshot containing the workload ID/version and definition digest, Jira inputs, source roles and revisions/content digests, and approved-inventory revision. Carry that binding through decomposition, review, resume, and submission. Before resuming or submitting, verify the bound inputs are unchanged. A change invalidates the prior repository selection and its review; refresh the snapshot, resolve the target, and review the updated plan before submission.
- Each harness queries for the exact **target_repository** value. Project scope may narrow the query but cannot replace or override the target.
- Return an explicit resolved or unresolved result. A resolved result contains exactly one target and its supporting evidence. An unresolved result contains a reason, candidate approved repositories, and available evidence references; the candidate list may be empty when evidence is unavailable. Preserve unresolved results in the planning report for refinement. If a source is unavailable or stale, evidence conflicts, or the resolver cannot support one target, return unresolved and block publication or dispatch. Do not choose a default or the first candidate.
- Decompose work spanning repositories into repository-specific implementation epics. Multi-repository execution requires a separate change to the AISDLC-4 producer/consumer contract.
- Keep credentials out of workload definitions. Source retrieval must use the configured authorized access mechanism.

## Consequences

- Repository assignment has an explicit producer/consumer contract and can be audited against the inputs used to make it.
- Projects can configure different mapping documents and Architecture Context locations without changing shared resolver code.
- Missing or ambiguous mapping evidence blocks generation and requires correction or human review.
- The Fullsend consumer must change to read and validate the per-epic target; its current workflow-wide target is insufficient.
- Implementation depends on agreement about the Jira field ID and type, target serialization, workload source-reference schema, named inventory and source owners, source access, and encoding of the resolved/unresolved results and their evidence.

## Validation

Before accepting this proposal, evaluate the resolver against representative RHAI cases and at least one real non-RHAI project's configured sources, with expected targets verified by the relevant project owners. Include correct single-target mappings, one-to-many and many-to-many component relationships, cross-repository decomposition, ambiguous or conflicting evidence, unavailable sources, and changes to bound inputs.

Check target correctness separately from inventory membership: selecting the wrong approved repository fails the evaluation. Cases without sufficient evidence must return unresolved with the expected reason, candidates, and available evidence. The agreed acceptance cases must produce the verified targets or expected unresolved outcomes before the design is accepted. These evaluations are required work; this ADR does not claim they have passed.

## References

- [AISDLC-90 — Tag implementation epics with target repositories](https://redhat.atlassian.net/browse/AISDLC-90)
- [AISDLC-61 — Make Epic Decomposer support pluggable planning workloads](https://redhat.atlassian.net/browse/AISDLC-61)
- [AISDLC-4 — Deploy Fullsend code generation for Red Hat AI engineering](https://redhat.atlassian.net/browse/AISDLC-4)
- [RHAISTRAT-2376 — AWS Neuron Support for Red Hat AI](https://redhat.atlassian.net/browse/RHAISTRAT-2376)
- [Architecture Context repository](https://github.com/opendatahub-io/architecture-context/)
- [RHODS Operator architecture entry](https://github.com/opendatahub-io/architecture-context/blob/main/architecture/rhoai-2.10/rhods-operator.md)
- [RHAI component-to-repository mapping notes](https://docs.google.com/document/d/1Hv34Ci58_BIhWmWJXKE34w2ZlQrQWMeg_fjvMsv4Erk/edit?tab=t.0#heading=h.wdwmerquvvbx) — candidate source; verify contents and ownership
- [Fullsend poller at the inspected revision](https://github.com/fullsend-ai/fullsend/blob/15136ebc5b31720b80a8a45220c58b0dfda9d6ec/internal/cli/poll.go)
- [Fullsend Jira integration guide at the inspected revision](https://github.com/fullsend-ai/fullsend/blob/15136ebc5b31720b80a8a45220c58b0dfda9d6ec/docs/guides/user/jira-integration.md)

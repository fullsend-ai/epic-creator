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
Updated: 2026-11-01

## Status

Proposed. Use a Jira label to mark the resolved repository. Confirm the label convention and workload contract with the Jira and Fullsend owners before implementation.

## Context

AISDLC-90 needs Epic Creator to identify the repository where code generation should run. The AISDLC-4 contract requires each implementation epic to resolve to one canonical repository from an approved inventory, with one code-agent invocation and one pull request.

The current Fullsend poller accepts a workflow-wide repository and does not read a per-epic target. After determining the repository, Epic Creator must mark the implementation epic with its repository label so the matching Fullsend harness can select it.

Repository mapping needs project-specific evidence. A component catalog or Architecture Context entry can describe repositories and their relationships without establishing which repository is the approved target for a Jira epic. The RHAI mapping document is a candidate source; verify its contents and ownership before treating it as authoritative.

AISDLC-61 provides the workload extension model: a selected workload has a validated definition that configures workload-specific behavior. Epic Creator needs one reusable repository-mapping agent, with evidence sources supplied by the selected project's or domain's workload. RHAI/RHOAI mapping documents and Architecture Context are initial examples; other domains may use ownership catalogs, component mappings, repository metadata, or other maintained documentation. They need not provide the same documents or source types.

## Decision drivers

- Resolve each implementation epic to exactly one approved repository.
- Make the assignment traceable to Jira fields and versioned mapping evidence.
- Allow each workload to configure the sources and approved inventory appropriate to its project or domain.
- Keep harness queries consistent with the target assigned by Epic Creator.
- Stop dispatch when evidence is missing, conflicting, stale, or ambiguous.

## Options considered

### Mapping

1. **LLM using repository descriptions:** flexible, but descriptions alone may not distinguish related repositories or establish an authoritative target.
2. **Static Jira-component-to-repository table:** deterministic and reviewable, but requires verified one-to-one mappings and an owner for updates.
3. **Architecture Context as the sole source:** useful for architecture and repository metadata, but the inspected RHODS Operator entry does not define a Jira-to-target rule.
4. **Grounded mapping agent with workload-provided sources:** combines Jira fields, the workload's approved repository inventory, and its configured evidence sources. The same agent interprets different projects' source content and returns a supported target or an unresolved result. It requires source validation, provenance, and abstention behavior.

### Jira and harness routing

1. **Separate Jira project per repository:** simple harness queries where project boundaries already match repository boundaries.
2. **Shared project with a repository label or Component filter:** supports shared projects; a repository label identifies the resolved target, while Component may cover several repositories.
3. **Project scope plus an explicit repository label:** project-specific queries can narrow the results while the repository label remains the dispatch selector. A project such as RHAI can contain epics targeting different repositories.

## Decision

Propose mapping option 4 and routing option 3.

- Resolve one scalar **target_repository** for each implementation epic: the canonical provider, host, namespace, and repository identity from the approved inventory. Represent that assignment in Jira using exactly one controlled repository label in the `target-repo-<inventory-key>` namespace. This label is the Jira representation; no dedicated target field or description-block fallback is required.
- The approved inventory assigns each repository a stable, unique label and maps it to the full canonical identity. For example, an illustrative inventory entry could map `target-repo-odh-dashboard` to `github.com/opendatahub-io/odh-dashboard`. Labels must distinguish forks, namespaces, and hosts; do not infer the full identity from a bare repository name. Record the selected label alongside the canonical target and mapping evidence.
- Apply the repository label when publishing the resolved, reviewed implementation epic. Updating a resolved assignment replaces any previous label in the controlled `target-repo-` namespace while preserving unrelated Jira labels. An unresolved assignment must not retain a dispatchable repository label.
- Run a shared repository-mapping agent during Epic Creator decomposition. Its process is the same across workloads: read the epic's Jira evidence, examine the configured sources, and return one supported canonical repository with evidence or an explicit unresolved result. Keep project-specific URLs, repository names, and mapping rules in workload data.
- Put a configurable list of mapping-source references and the approved-inventory reference in the selected workload's validated definition. Each source reference declares its type, location and relevant selectors, such as a document section or repository path, plus its revision or content digest. Retrieve and inject the configured content through the authorized source access mechanism at runtime. A mapping document and an Architecture Context repository are supported design examples, not mandatory slots that every workload must fill. Agree the exact source-reference schema before implementation.
- Select the applicable workload definition for the Jira project or domain before resolution. Project scope selects the mapping context and inventory, not the target repository itself. RHAI and RHOAI can use their configured mapping and architecture evidence; another domain can supply different source types and structures. Sharing a definition or using cross-project component aliases must be explicit in configuration.
- Each workload definition declares the owner and role of every configured source: authoritative contribution-target data or supporting context. Authoritative target data takes precedence over supporting repository metadata; upstream, fork, or manifest-source relationships alone do not establish a contribution target. Conflicting authoritative sources leave the result unresolved. The agent must follow these declared roles for the selected workload's source set.
- Give the resolver verified Jira fields, the approved repository inventory, and the retrieved source content. It returns one canonical repository and evidence references. Deterministic validation checks the target against the inventory and records workload identity, source URLs and revisions or digests, Jira fields, cited sections, and inventory revision. Inventory membership validates that a repository is allowed; the cited authoritative evidence must also support its selection for this epic.
- Bind the selected target and its evidence to a mapping snapshot containing the workload ID/version and definition digest, Jira inputs, source roles and revisions/content digests, and approved-inventory revision. Carry that binding through decomposition, review, resume, and submission. Before resuming or submitting, verify the bound inputs are unchanged. A change invalidates the prior repository selection and its review; refresh the snapshot, resolve the target, and review the updated plan before submission.
- Each harness queries for its exact repository label, for example `project = RHAI AND labels = "target-repo-odh-dashboard"`. Project scope narrows the query; the label selects the repository. Before dispatch, validate that the epic has exactly one controlled repository label, that it maps to the harness's canonical target in the approved inventory, and that it agrees with the resolved target and current mapping snapshot. A matching JQL query alone does not establish these conditions. Missing, unknown, multiple, or conflicting repository labels block dispatch.
- Return an explicit resolved or unresolved result. A resolved result contains exactly one target and its supporting evidence. An unresolved result contains a reason, candidate approved repositories, and available evidence references; the candidate list may be empty when evidence is unavailable. Preserve unresolved results in the planning report for refinement. If a source is unavailable or stale, evidence conflicts, or the resolver cannot support one target, return unresolved and block publication or dispatch. Do not choose a default or the first candidate.
- Decompose work spanning repositories into repository-specific implementation epics. Multi-repository execution requires a separate change to the AISDLC-4 producer/consumer contract.
- Keep credentials out of workload definitions. Source retrieval must use the configured authorized access mechanism.

## Consequences

- Repository assignment has an explicit producer/consumer contract and can be audited against the inputs used to make it.
- Projects and domains configure their evidence sources and inventory while sharing the same mapping-agent behavior. Workloads do not need RHAI-shaped documents or an Architecture Context repository. Supporting additional retrieval formats may require shared source adapters; project-specific mapping policy remains in workload data.
- Missing or ambiguous mapping evidence blocks generation and requires correction or human review.
- The Fullsend consumer must change to read and validate the per-epic repository label against its configured repository and the mapping snapshot; its current workflow-wide target is insufficient.
- Implementation depends on agreement about the controlled label convention and inventory entries, harness JQL, workload source-reference schema, named inventory and source owners, source access, and encoding of the resolved/unresolved results and their evidence.

## Validation

Before accepting this proposal, evaluate the resolver against representative RHAI cases and at least one real non-RHAI project's configured sources, with expected targets verified by the relevant project owners. Include correct single-target mappings, one-to-many and many-to-many component relationships, cross-repository decomposition, ambiguous or conflicting evidence, unavailable sources, and changes to bound inputs.

The non-RHAI evaluation must exercise a different source structure or source type. Verify that workload selection supplies the intended inventory and source set, that evidence from another workload cannot silently influence the result, and that unconfigured RHAI-specific source types are not required. This must demonstrate reuse of the mapping agent rather than a separate domain-specific resolver.

Check target correctness separately from inventory membership: selecting the wrong approved repository fails the evaluation. Cases without sufficient evidence must return unresolved with the expected reason, candidates, and available evidence. The agreed acceptance cases must produce the verified targets or expected unresolved outcomes before the design is accepted. These evaluations are required work; this ADR does not claim they have passed.

Also verify repository-label publication and dispatch: one resolved target produces its inventory-defined label; reassignment replaces the old repository label and preserves unrelated labels; unresolved or invalidated assignments cannot dispatch using an old label. Missing, unknown, multiple, or conflicting repository labels must be rejected even when the epic matches the harness query.

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
- [Jira JQL labels field](https://support.atlassian.com/jira-software-cloud/docs/jql-fields/#Labels)

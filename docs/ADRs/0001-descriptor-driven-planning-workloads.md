---
title: "1. Descriptor-driven planning workloads"
status: Proposed
topics:
  - workloads
  - descriptors
  - architecture
---

# 1. Descriptor-driven planning workloads

Date: 2026-10-11
Updated: 2026-10-18 — align submission, recovery, producer contracts and verification with the implementation.

## Status

Proposed — awaiting team review. The implementation PR is the review vehicle.

## Context

Before this change, Epic Decomposer wired RHAI planning behavior directly into its
discovery, decomposition, review, revision, and Jira submission stages.
[AISDLC-58](https://redhat.atlassian.net/browse/AISDLC-58) defines a pluggable
planning pipeline across RFE Creator, Strategy Creator, and Epic Decomposer;
[AISDLC-61](https://redhat.atlassian.net/browse/AISDLC-61) delivers the Epic
Decomposer portion, with RHAI Initiatives as the first new target workload.

The cross-stage handoff contract has been verified against the live upstream
designs: strat-creator
[#76](https://github.com/opendatahub-io/strat-creator/pull/76) and
[#82](https://github.com/opendatahub-io/strat-creator/pull/82), and rfe-creator
[#172](https://github.com/opendatahub-io/rfe-creator/pull/172). Those designs
establish that the creators are separate peer pipelines that share descriptor
vocabulary, not code, and that gate evaluation stays in the consuming pipeline.
Handoff identity fields are station-specific: RFE Creator uses `type`,
`tracker_ref`, and `local_id`; Strategy Creator uses `strat_id`, `jira_key`,
and `source_rfe` or `source_initiative`. Its PRs #76/#82 contain the RFE
descriptor and registry; the Initiative descriptor is on
[Guy's fork](https://github.com/guyoron1/strat-creator/blob/b41aee7/types/initiative-strategy/type.yaml).
Approval is signaled by the same `strat-creator-human-sign-off` and
`strat-creator-rubric-pass` labels for both paths. RFE strategies use a
per-issue `{key}-strategy.md` attachment with description fallback.
Initiative strategies use only the newest `RHOAIENG-NNNN-strategy.md`
attachment; the description remains the Initiative's own text, and a missing
attachment means skip. These corrections are confirmed in
[AISDLC-61 comment 18872381](https://redhat.atlassian.net/browse/AISDLC-61?focusedCommentId=18872381).

### Rationale from the RFE Creator enablement session

The 2026-10-07 **ADLC Enablement Series: RFE Creator** discussion provides
additional rationale for this proposal. The source is the computer-generated
transcript supplied by the user on 2026-10-14; timestamps refer to that transcript.

- **Independent stations and explicit handoffs.** Antonin Stefanutti described
  autonomous stations coordinating through Jira as the system of record, using
  labels and human gates (06:56–09:47). This supports preserving source identity
  and provenance across peer pipelines, with each consumer enforcing its gates.
- **Flexible workload dispatch.** Shai Revivo described differing business-unit
  processes and the need for variation in dispatch from requirements to
  ready-to-code (48:21–50:47). Validated descriptors and referenced policies provide
  that variation within Epic Decomposer while keeping stage mechanics shared.
- **Deterministic checks and independent review.** Antonin advocated deterministic
  Python logic wherever possible and adversarial review of creative output in a
  fresh context, sometimes using a different model (11:06–14:40). This supports
  enforcing descriptor, provenance and review-gate rules in code while exposing
  planning content and review criteria through versioned policies.
- **Evaluation of policy changes.** Antonin described regression evaluation of
  skills and prompts, combining deterministic and model judges
  (08:21–09:47, 12:51–14:40, 28:23–31:42). Policy extraction therefore needs model
  quality evaluation against the existing Epic Creator corpus in addition to
  deterministic orchestration tests.
- **Separate runtime migration.** Antonin reported a green GitLab CI dry run with
  the Fullsend runner and gradual production migration as the next step
  (36:46–38:46). This supports keeping the workload architecture separate from
  the Fullsend runtime migration tracked in AISDLC-53.

These remarks support the architectural direction; they do not constitute team
acceptance of this ADR. The existing RFE Target Version gate, cloning and
auto-approval described in the session do not change the later verified
Initiative contract: original Initiative identity, attachment-only strategy
content, both Strategy Creator approval labels and no Target Version gate.

## Options

1. **Fork per workload.** Copy the pipeline for each new workload. Fast to
   start, but every fix and policy change must be replicated across forks.
2. **Branch on workload in shared code.** Keep one pipeline and scatter
   `if workload == ...` dispatch through the stages. Avoids forks, but
   workload-specific logic stays embedded in shared code and every new
   workload touches every stage.
3. **Wait for a shared planning engine.** Defer until a common engine exists
   across the creators. The upstream designs explicitly keep the creators as
   separate peer pipelines; the descriptor contract is the agreed integration
   point.
4. **Descriptor-driven consuming architecture.** Keep one pipeline whose
   stages resolve workload behavior from a validated, versioned descriptor
   selected once at entry.

## Decision

Epic Decomposer adopts a descriptor-driven consuming architecture.

A workload is defined by a versioned descriptor and registered in an in-repo
registry (for example `workloads/rhai-initiative.yaml`). The descriptor names
the source strategy contract, epic template and decomposition rules, review
policy, scoring rules, components, review gate, tracker destination, labels and
relationships. Evaluation sets remain in the separate evaluation configuration;
the current descriptor schema does not select them. The descriptor is data that references
reviewed in-repo policies; it does not load executable plugins.

Descriptor selection resolves once at entry and persists in pipeline state.
The new path is explicitly selected with `--workload ID`; omission preserves
the legacy RHAISTRAT invocation. Opt-in Jira intake resolves the descriptor's `(project, issue_type)`
binding, then validates the source key and approval evidence. Strategy Creator
does not publish a workload-specific Jira marker. The Initiative path retains
the original Initiative identity (no `RHAISTRAT` clone); legacy artifacts with
no workload metadata retain the existing `RHAISTRAT` compatibility path.
Explicit workload selection or artifact metadata must agree with the source
identity. Conflicting or unknown identities fail before decomposition or
Jira writes. Initiatives have no Target Version release gate, and only the
decomposer's `epic-creator-auto-decomposed` label marks them processed;
hand-made child Epics do not prevent intake.

Every stage — fetch, decompose, review, revise, and submit — resolves its
behavior from the selected descriptor. Shared stages keep only stage procedure,
artifact locations, and safety and validation instructions. Generated
artifacts carry the workload identity and source provenance forward, and
submission preflights the descriptor identity before the first Jira write.

### Binding, execution and submission

Intake-only descriptors remain valid. Planning requires a `planning` block;
typed submission requires both `planning` and `submission`. Discovery JQL is
still supplied by the caller. Production discovery configuration is separate
from this implementation.

Pipeline state and generated artifacts bind the workload ID/version, canonical
descriptor digest, referenced planning-policy content digest and source
provenance. Stage boundaries validate these bindings, including ordered review
scores against the selected gate. Changed descriptors, policies or bound source
revisions reject resume; typed artifacts need a fresh fetch and planning run.

The frontmatter CLI exposes the selected producer contract through
`schema TYPE --workload ID`, including exact rubric criterion names and canonical
component names. Typed Epic writes reject unknown components; typed submission
requires the selected component cache and validates main and conditional plans
before any Jira write. This avoids silently dropping a reviewed component from
the submitted plan. Untyped legacy submission retains its compatibility behavior.

The planning skill ends at REPORT. Its state CLI returns worker jobs; an agent
runtime executes the planning/review workers. Submission is a separate explicit
`submit.py --workload ID` command. A credential-free dry run validates local
artifacts without HTTP. Execution validates the full artifact set, review and
dependency graph before writes, then rechecks live source approval and strategy
freshness. Both shipped descriptors create RHAI Epics parented to the original
source, with Blocks dependency links. Descriptors may instead configure a source
link. Conditional plans remain attachments on a main Investigation, rather
than separate issues. A main Epic's `gated_by` must name a main Investigation
included in its dependencies.

Submission uses stable identity labels and reviewed-plan fingerprints together
with an atomically persisted, locally locked intent/key journal. Intent is saved
before creation. Reruns recover known effects and reconcile missing relationships,
labels and content-addressed evidence. Create/upload requests are not blindly
retried after ambiguous outcomes; an unresolved prior create stops for later
reconciliation. The source is marked processed only after all phases succeed.

## Consequences

- Adding a workload means adding a validated descriptor and its policies, not
  forking the pipeline or editing shared stages.
- Descriptor validation rejects missing fields, duplicate IDs, unknown
  versions, unsafe paths, and unsupported policy references before a run
  starts.
- The existing `RHAISTRAT` discovery, artifact, DAG, review, and submission
  behavior is preserved as a compatibility path.
- Gate evaluation remains in Epic Decomposer, consistent with the
  peer-pipeline design. The current descriptor schema has no `inputs:` field;
  upstream handoff vocabulary is not an executable gate interface here.
- Original Initiative identity is preserved through intake and provenance.
  Submission's `parent | link` relation describes each generated Epic's
  relationship to that source; it does not create a strategy clone or require
  a `self` submission relation. Cross-stage handoff-schema coordination remains
  separate from these local relationship settings.
- The descriptor schema must stay cohesive as workloads grow, and the
  cross-stage contract must be versioned so stations can evolve without
  lockstep releases.
- Recovery requires retaining the artifact directory and journal, serializing
  independent workspaces and allowing for Jira search visibility/indexing.
  Labels are not a global uniqueness constraint. A lost journal plus an
  invisible or unindexed issue cannot guarantee duplicate prevention.
- Changes to planning prompts and policies require model quality evaluations
  against the existing Epic Creator corpus. Deterministic flow tests verify
  orchestration and contracts; corpus evaluation remains outstanding for the
  extracted policies in PR #40.

## Implementation and verification

Intake, descriptor-bound planning, explicit submission and recovery are
implemented in PR #40, including the follow-up fixes and tests in `04247fc`.
Local verification passed 17 CLI-to-HTTPS E2E cases using synthetic credentials
and a local Jira substitute. One tool-free real-model adapter example also
passed; it does not establish general planning quality or native Skill/Agent
dispatch. Reproduction commands and limits are documented in
[`docs/workload-verification.md`](https://github.com/amastbau/epic-creator/blob/04247fcdb092cbc0065ae76ab3be630e507ea883/docs/workload-verification.md).
Production Fullsend configuration, live approved-source submission and the
complete policy-quality corpus remain unverified. None of this verification
changes the ADR's Proposed status or constitutes team acceptance.

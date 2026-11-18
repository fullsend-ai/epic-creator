---
title: 'Assign one target repository to each implementation epic'
status: Accepted
relates_to:
  - "*"
topics:
  - workloads
  - jira
  - repositories
  - provenance
---

# ADR-0001: Assign one target repository to each implementation epic

Date: 2026-10-22
Updated: 2026-11-18

## Status

Accepted

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

Repository selection combines four RHAI sources with distinct roles:

- **Contribution inventory** — the list of approved contribution destinations.
  This is the authoritative source: it is the only input that can approve a
  repository as a contribution target.
- **Contribution rules** — the workload's policy for where changes should be
  contributed, kept with the inventory.
- **Component/repository mapping** — maps Jira components to repositories.
  Supporting evidence: it suggests candidates but does not approve them.
- **Architecture Context** — release-appropriate documents and active overlays
  from [Architecture Context](https://github.com/opendatahub-io/architecture-context/),
  divided by release. Supporting evidence: component responsibilities, APIs and
  repository relationships that explain where a change belongs. Reuse the shared
  bootstrap's checkout rather than fetching an independent architecture source.

Architecture descriptions, product mappings and fork relationships never
independently authorize a contribution target. Conflicting or insufficient
evidence leaves the assignment unresolved.

Today the RHAI inventory, rules and mapping are configuration files inside the
Epic Creator repository (`config/rhai/`); see the configuration-placement
follow-up below. Other workloads configure their own maintained evidence and
inventory while using the same planner. Source definitions declare ownership,
authoritative or supporting roles, Git locations, revisions and relevant
paths. Project-specific mapping rules belong in workload data.

Do not add the internal product/upstream mapping project as a direct input to
Epic Creator for the current RHAI scope. For broader adoption, its
repository-discovery data may help populate platform definitions and generate
Architecture Context. That is a future integration, not a dependency of this
Epic Creator decision.

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

## Alternatives considered

A post-hoc repository mapper — a sub-agent that assigns repositories after
Epics are drafted and reviewed, leaving existing Epic scope, body and
dependencies untouched — was evaluated as a smaller, lower-risk change
([PR #62](https://github.com/fullsend-ai/epic-creator/pull/62)). At the RHAI
Tech Review Forum on 2026-11-17 the group preferred planning repositories
before drafting: the planning context is already loaded, PR-sized Epics keep
reviewable boundaries, and current output too often produces a single Epic
whose acceptance criteria span multiple repositories. The original motivation
for the smaller alternative — no way to test a larger change safely — is
addressed by the evaluation harness and the captured real-strategy test
corpus.

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

## Discussion outcomes and future work

Recorded from the RHAI Tech Review Forum on 2026-11-17. None of these block
this decision.

- **Rollout mechanism.** Repository mode ships behind a feature flag that
  defaults to off; today the switch is the `enabled` key in the workload's
  repository configuration. The agreed future direction from the forum is to
  drive such flags through GitLab CI variables: branch protection in the
  target repositories prevents staging branches, so the available granularity
  would be a default value for scheduled pipelines plus ad-hoc overrides for
  targeted dry runs.
- **Architecture-context scalability.** Curated architecture context is
  operationally expensive to maintain at scale — every release, product and
  component, potentially across hundreds of repositories — and can drift
  from repository state between planning and implementation. The agreed
  direction is a pluggable context interface: replace hardcoded references
  to a specific architecture-context repository with a skill contract, so a
  workload can supply context from a curated snapshot, live repository
  inspection or another store behind the same interface. What the platform
  owns versus what workloads supply remains open.
- **Configuration placement.** The RHAI repository-mapping configuration —
  the contribution inventory, contribution rules and component/repository
  mapping under `config/rhai/` — cannot live indefinitely inside Epic Creator
  because it must serve every workload. The Tech Review Forum asked to move
  these files to the architecture-context repository, keeping Epic Creator
  workload-agnostic.
- **Configuration experience.** Epic Creator is configured through files
  while other fullsend agents read environment variables; the two models
  need reconciling into a single user-facing configuration experience.

## Evaluation test candidates

The following Jira tickets were identified as evaluation cases for repository
mapping. Each exercises a distinct mapping challenge. All proposed results are
unresolved pending source/inventory approval and contribution policy
confirmation.

| Ticket | Component(s) | Coverage |
| --- | --- | --- |
| RHAI-180 | AI Core Dashboard | One-to-many component mapping; wrong approved repository |
| RHAI-372 | Feature Store | Fork identity; many epics per component |
| RHAI-373 | Feature Store | Component boundary; conditional cross-repository |
| RHAI-411 | AI Core Platform | One-to-many component; cross-repository split candidate |
| RHAI-349 | llm-d | Repository rename; release scope; conditional cross-repository |
| RHOAIENG-95815 | 18 components | Multi-component; inventory gap; explicit Jira target |
| RHOAIENG-93736 | Feature Store | Repository boundary; cross-repository split candidate |
| RHOAIENG-101161 | Feature Store | Supplementary bug; source version gap; wrong similar repository |

Source: the evaluation-candidates bundle captured from Jira on 2026-11-03
during the repository-source verification work.

## Evaluation results

The repository-planning implementation ([PR #56](https://github.com/fullsend-ai/epic-creator/pull/56))
was evaluated twice on 2026-11-18 against six real captured RHAISTRAT
strategies with fixture-only configuration and no live Jira access.

### Fullsend end-to-end run (primary)

Fullsend 0.56.1 runtime with OpenShell sandboxes, `claude-opus-4-6` via Vertex
on the team GCP project, and the production planner, drafter, reviewer and
dispatcher. Harness validation gates applied; Jira publication excluded by the
eval harness (placeholder credentials, no Atlassian egress).

| Strategy | Epics | Automated review | Outcome |
| --- | ---: | --- | --- |
| RHAISTRAT-1413 | 3 | pass, 14/14 | Resolved to `opendatahub-io/data-science-pipelines` |
| RHAISTRAT-1631 | 5 | revise requested, 12/14 | One priority-mapping finding; repositories resolved across eval-hub, trustyai-service-operator and odh-dashboard |
| RHAISTRAT-2155 | 9 | pass, 14/14 (on retry) | First attempt rejected by the evidence-integrity check: pinned source content changed between drafting and review. A retry on identical code and inputs passed cleanly — resolved across three repositories, one documentation target left unresolved with warning |
| RHAISTRAT-2369 | 5 | pass, 14/14 | Resolved to `opendatahub-io/odh-dashboard`; documentation destination left unresolved with warning |
| RHAISTRAT-2649 | 3 | pass, 14/14 | Resolved across the gateway, MaaS and operator repositories |
| RHAISTRAT-3069 | 5 | pass, 14/14 | Two work items left unresolved with warnings — the planned service repository does not exist in the inventory |

Five of six accepted with passing reviews (one on retry); one accepted with
revision findings. Every resolved repository was verified against the live
GitHub repository (exists, actively maintained, structure matches the
planned work). Unresolved destinations kept their reasons and the
human-correction warning. The RHAISTRAT-2155 first-attempt rejection did not
reproduce on retry under identical conditions; the integrity check refused a
result built on drifted evidence exactly as designed, and the retry confirmed
the pipeline itself is sound.

### Local dispatcher run (supplementary)

A local headless run of the same six strategies on the same captured inputs
validated pipeline mechanics independently of the Fullsend runtime: all six
dispatchers reached DONE, all six automated reviews passed, and every resolved
assignment (30 of 30) carried verbatim evidence citations including an
authoritative contribution-inventory citation. The full regression suite
passed locally: 628 tests after integrating upstream main (the merge adopted
main's finer-grained create-retry semantics and revised-subset re-review
design alongside this PR's URL-field publication).

### Flag-off verification

A legacy-mode run with repository planning disabled (the production default,
`enabled: false`) decomposed the same captured strategy on the merged head:
the dispatcher reached DONE, the automated review passed, and the run
produced no repository artifacts at all — no repository plans, no
repository source snapshots, no `target_repository` metadata and no
repository warnings. Legacy behavior is unchanged when the feature is off.

Limits: neither run exercises Jira publication — the URL-field contract and
warning comments are covered by unit and local HTTPS fixture tests only. The
contribution inventory is provisional fixture configuration; production
contribution approval and the per-case owner review remain outstanding. No
Jira issues, URL fields or comments were created in either evaluation. Full
artifacts are preserved with the captured test-data bundle from the same
evaluation series.

## References

- [Architecture Context](https://github.com/opendatahub-io/architecture-context/)
- [Epic Creator repository planning and publication proposal](https://github.com/fullsend-ai/epic-creator/pull/56)

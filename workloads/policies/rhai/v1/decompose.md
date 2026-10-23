## Step 0: Triage

Read the strategy file. Run these checks in order; first match terminates the flow:

**Check 1 — Below threshold**: If the strategy is S-sized AND affects a single component AND a single team AND ≥67% of scope would score High AI implementability — check two escape conditions before triggering:

- **Genuine unknowns**: If the strategy contains open questions, conditional ADRs, or pending reviews where the answer would change which epics exist or what they do, below-threshold does not apply. Proceed to Step 1 so Investigation epics can be created.
- **Multiple distinct work streams**: If the scope spans architecturally distinct sub-systems that warrant separate PR review cycles (e.g., backend API + frontend UI, or code implementation + substantive content authoring + documentation), below-threshold does not apply. Proceed to Step 1 — these splits are architectural, not priority-based, and produce better-scoped PRs. **Incidental documentation touch-ups do not count as a distinct work stream.** Updating captions, screenshots, or references that fall out of the same change — with no new content authored — stays in the single epic (as scope/acceptance criteria) and does not defeat below-threshold. Only *substantive* content authoring (new tutorials, sample notebooks, curated examples, or new/rewritten doc topics) is a distinct work stream.

If neither escape condition applies: produce a single epic file `artifacts/epic-tasks/{ID}-E001.md` (with full frontmatter and body per Step 8) and a decomposition summary. Then stop — no DAG, no multi-step decomposition. Do not proceed to Steps 1-8. In particular, Step 4's priority-split logic does not apply: a below-threshold strategy stays as one epic even if its HLRs span multiple priority levels.

**Check 2 — Documentation only**: If all affected components have "No code changes" or "reference only": produce a single epic file with `implementation_type: docs-authoring`, content outline, and mandatory accuracy validation against architecture context. Write the decomposition summary. Then stop.

For either triage path, write the epic file per Step 8b, then set summary frontmatter and the completion sentinel:

```bash
python3 scripts/frontmatter.py set --source artifacts/strat-tasks/{ID}.md artifacts/epic-tasks/{ID}-decomposition.md \
    parent_strat="{ID}" epic_count=1 critical_path_length=1 \
    triage=below-threshold triage_rationale="<reason>"

python3 scripts/frontmatter.py set --source artifacts/strat-tasks/{ID}.md artifacts/epic-tasks/{ID}-decomposition.md decompose_complete=true
```

Use `triage=docs-only` for Check 2. The `decompose_complete=true` line must be your **final action** — the pipeline will not launch the review agent until it is set.

If neither check fires, proceed to Step 1.

## Step 1: Parse Strategy Structure

Extract these sections from the strategy:

- **Affected Components table**: (component name, what changes, owner team)
- **Impacted Teams table**: (team, components owned, involvement)
- **High Level Requirements**: with P0/P1/P2 priority
- **Dependencies table**: (dependency, type, status, impact if blocked)
- **Acceptance Criteria**: (Given/When/Then with "measured by" clauses)
- **Non-Functional Requirements**

If any required section is missing, note it as a health warning but proceed with available information.

## Step 1.5: Parse Staff Engineer Input

If the strategy contains a Staff Engineer Input section that is non-empty (beyond template placeholder text):

- Parse it first — it takes precedence
- When Staff Engineer Input diverges from AI-generated Technical Approach, use the Staff Engineer Input
- Log each override for traceability in the decomposition summary

## Step 2: Build Component Graph

**Component name constraint:** Read `{COMPONENT_FILE}` for the canonical list of RHAI Jira components. When assigning a component to an epic, you MUST use a name from this list. Match the strategy's Affected Components to the closest canonical name. If no reasonable match exists, use the closest parent-level component (e.g. "Model Serving Runtimes" for a new runtime not yet in the list). Log any non-obvious mappings in the decomposition summary.

For each (component, change, owner team) tuple from the Affected Components table:

**Active vs. Passive classification:**
- **Active component**: Code changes needed → generates epic(s)
- **Passive component**: No code changes, keep working → validation becomes acceptance criterion on nearest active component's epic
- **Exception**: If a different team validates the passive component → generate a separate validation Implementation epic owned by that team

**New component detection:**
- Check `.context/architecture-context/` for component existence
- Component is "in platform" if an architecture context file exists for it
- Component NOT in architecture context → create:
  - Implementation epic(s) for provisioning with appropriate `implementation_type`
  - Investigation epic if dependency availability needs validation

## Step 3: Identify Investigation Epics

First, systematically scan the strategy for all unknowns — check the open questions table, risks table, assumptions, pending reviews, and conditional ADRs. Do not rely on the technical approach section alone; open questions are often in tables at the end of the strategy and may contradict or qualify the detailed technical description.

Collect the full list of unknowns before creating any Investigation epics. Then evaluate each independently:

**Decision rule: Does the answer change which downstream epics exist or what they do?**

- **YES** → Investigation epic. Determine which downstream epics depend on the outcome. Add as DAG edges. For bounded outcomes (≤3 possibilities): output conditional decomposition branches.
- **NO** → Acceptance criterion on the relevant Implementation epic. Implementation proceeds the same way regardless; failure = fix-and-retry.

Multiple independent unknowns can produce multiple Investigation epics, even for the same component and team. The component/team boundary rule applies to Implementations (bundling work for a team), not Investigations (resolving a specific unknown). However, if two qualifying unknowns would be resolved by the same experiment producing the same deliverable, combine them into one Investigation — don't split for the sake of splitting.

## Step 4: Map HLRs to Epics

**Precondition:** This step only runs for strategies that were NOT triaged in Step 0. If Step 0 produced a below-threshold or docs-only result, the strategy is already a single epic — do not apply priority-split logic.

- Each P0/P1/P2 requirement must map to one or more epics
- Every HLR must be covered — no orphaned requirements
- Priority inheritance: prerequisite epic inherits the highest priority of all HLRs it transitively enables
- An epic blocking all P0 work is implicitly P0
- Priority split: when an epic maps to HLRs at multiple priority levels, check whether the lower-priority HLRs represent distinct, deferrable features. A feature is "distinct and deferrable" only when it could ship as its own epic with independent user value AND does not share data structures, UI surfaces, or API endpoints with the P0 work. If yes, split them into separate epics by priority so each can be planned independently. If the lower-priority HLR is incidental to the P0 work (error handling, doc coverage, config override that falls out of the same implementation) or is tightly coupled to the same implementation surface, keep it bundled — splitting tightly coupled work creates duplication and artificial boundaries.
- `docs-authoring` priority exception: `docs-authoring` epics are exempt from priority inheritance. They do not push their priority upstream to the implementations they depend on. Instead, derive their priority from the strategy frontmatter `priority` field: Critical→P0, Major→P1, Normal/Minor/Undefined→P2. If the field is missing or empty, default to P2.

## Step 5: Build Dependency DAG

Apply these rules to construct edges between epics:

### Epic Boundary Rules
1. Different component OR different team → separate epics
2. Same component + same team + same logical change → single epic. When splitting within a single component, split along two axes:
   - **Architectural sub-system boundaries**: backend API vs. frontend plugin, Go service vs. React UI, etc.
   - **Work-product type boundaries**: code implementation, content authoring (sample notebooks, tutorials, curated examples), and documentation are different kinds of work — they require different skills, different review criteria, and produce different PR review cycles. An epic that mixes code implementation with content authoring should be split even if the same team owns both. **Exception — incidental documentation touch-ups:** updating captions, screenshots, or references that fall out of a code change (no new content authored) is not a separate work stream — fold it into the originating epic as scope/acceptance criteria rather than a standalone `docs-authoring` epic. Only substantive content/documentation authoring (new tutorials, sample notebooks, curated examples, new or rewritten doc topics), or a strategy with `Documentation Support: Yes`, warrants a separate epic.
   These axes take precedence when deciding where to draw epic boundaries within a component. Step 4 may additionally split by priority when features are genuinely deferrable — but the initial boundary structure should come from architecture and work-product type, not from walking down the HLR list. Never split such that two epics must build the same data structure, UI surface, or API endpoint independently.

### Investigation Edges
3. Investigation determines scope/existence of downstream work → blocking edge to all affected Implementations. Every downstream epic gated by an Investigation is a true gate (`remove` if the epic may not exist, `rewrite` if scope/approach changes, `add_remediation` if the investigation may reveal a problem requiring additional work) — record this in Step 8b when writing frontmatter. Bounded outcomes (≤3): conditional branches. Unbounded: phased decomposition.
4. Investigation is informational only (doesn't change what gets built) → not a true Investigation. Reclassify as an acceptance criterion on the relevant Implementation epic, or as an Implementation that produces a deliverable.

### Implementation Type Ordering
5. `repo-onboarding` → `konflux-onboarding` always serial (pipeline needs repo)
6. `repo-onboarding` → general implementation of onboarded component always serial (code needs repo). Doesn't block other repos.
7. `license-validation` ∥ `repo-onboarding` parallel (independent inputs)
8. `license-validation` ∥ `konflux-onboarding` parallel (config doesn't depend on specific deps)
9. `license-validation` → general implementation serial (if licenses fail, deps change, affects approach)
10. `konflux-onboarding` ∥ general implementation parallel (config independent of code; AC gates first execution). **Add NO blocking edge in either direction.** It is tempting to make the general implementation depend on `konflux-onboarding` ("the code needs a build pipeline first") — do not. That coupling is enforced by the Rule 25 "build pipeline green" AC on the general implementation epic, never by a DAG dependency. A `konflux-onboarding → general` edge serializes work that must run in parallel and is a rule violation.
11. `docs-authoring` blocked by ALL Implementation epics in strategy (always last; docs describe what was built). These edges do not trigger priority inheritance — see Step 4 `docs-authoring` priority exception.

### Implementation → Implementation Edges
12. Framework/library → consumer Implementations always serial (consumers build against framework)
13. Implementation producing artifact another epic's code builds against (API, CRD, library) → consuming Implementation serial. Does NOT apply to configuration references (image digests, endpoint URLs) — those are AC gates. **In particular, an image digest / SHA consumed by OLM/CSV packaging (`RELATED_IMAGE`) is a configuration reference, not a build-against artifact: the packaging epic gets a "pinned image digest available" AC, NOT a `dependencies` edge on the image-build or `konflux-onboarding` epic.** Wiring such an edge both mis-serializes parallel work and violates Rule 10 — the digest is filled in at execution time, so the epics run in parallel.
14. Implementations in different repos, no shared artifacts → parallel
15. Implementations in same repo, different areas → parallel (merge conflicts = coordination risk, not dependency)

### External Dependency Edges
16. External dependency Implementation (upstream PR/RFC) → Tier 2 Implementations always serial (gated by acceptance)
17. External dependency Implementation ∥ Tier 1 Implementations always parallel (Tier 1 delivers independent partial value)
18. External dependency with uncertain timing → always evaluate for tiered delivery (see Step 5.5)

### Epic Generation Rules
19. Safety-critical strategy (guardrails, sandboxing, RBAC) → generate fail-mode Investigation + security Investigation, both blocking main Implementation
20. New component not in architecture context → generate onboarding chain: `repo-onboarding` + `license-validation` (parallel start) → `konflux-onboarding` (after repo-onboarding) → general implementation (after license-validation; parallel with konflux-onboarding). For new container image in existing repo: skip repo-onboarding, start with image build Implementation + `konflux-onboarding` (parallel).
21. External community dependency where team submits PR/RFC and acceptance gates downstream → generate upstream Implementation epic, evaluate tiered delivery. If viable fallback exists (cherry-pick, fork) → note as AC, not separate epic. If third party resolves → model as precondition with tiered delivery, no separate epic.
22. Infrastructure not in platform inventory → generate validation Investigation (does it exist/work?) + provisioning Implementation

### Acceptance Criteria Rules
23. Strategy replaces existing capability → add rollback/feature-flag AC to replacing Implementation epic
24. `docs-authoring` epic → add "technically reviewed against implementation" AC
25. Implementation with `konflux-onboarding` in dependency chain → add "build pipeline green" AC

## Step 5.5: Detect Tiered Delivery

When an external dependency has uncertain timing:

1. Can the strategy deliver partial value without it?
2. **YES** → Split into:
   - **Tier 1**: Independent work (user-facing features OR risk-reduction spikes/validations)
   - **Tier 2**: Dependency-gated work (blocked by upstream epic)
3. **NO** → Linear dependencies; external dependency blocks all downstream

## Step 6: Classify Epics

For each epic, determine:

### Type (mandatory)
- **Implementation**: Produce an artifact (code, config, docs, manifests, pipelines, RFCs, upstream PRs)
- **Investigation**: Resolve uncertainty (answer a question or make a decision that other epics depend on)

### Implementation Type (optional routing label)

| Label | When to apply |
|-------|--------------|
| `docs-authoring` | Red Hat docs content process — specialized tooling |
| `konflux-onboarding` | Build pipeline onboarding — known-recipe |
| `license-validation` | License scanning for transitive dependencies |
| `repo-onboarding` | Midstream repo fork creation under opendatahub-io |
| _(absent/null)_ | General-purpose — read target repo, figure it out |

### AI Implementability Signals

Score signals so the pipeline can compute an AI-implementability classification deterministically. **Do not compute the total score or classification yourself.** Use the signal set that matches the epic `type` — Implementation and Investigation epics measure different things.

#### Implementation epics → `ai_signals` (9 signals)

Evaluate each as +1 (favorable), 0 (neutral/N/A), or -1 (unfavorable). Write the values into `ai_signals`.

| # | Signal | Frontmatter key | +1 Condition | -1 Condition |
|---|--------|----------------|-------------|-------------|
| 1 | Change specificity | `change_specificity` | Exact file paths, API endpoints, field names known | Vague scope ("improve X") |
| 2 | Pattern precedent | `pattern_precedent` | Similar changes exist in same codebase | No precedent in codebase |
| 3 | Adapter/plugin pattern | `adapter_pattern` | Follows existing reference implementation | N/A (0 if absent) |
| 4 | Existing foundation | `existing_foundation` | Extending existing code/feature | Greenfield, no foundation |
| 5 | Open questions | `open_questions` | All design decisions resolved for this epic | Open questions that would change implementation approach |
| 6 | External dependency | `external_dependency` | None | Upstream contribution or vendor coordination needed |
| 7 | Human process gates | `human_process_gates` | None | Requires human approval step |
| 8 | Repo access | `repo_access` | AI can clone and modify target repo | Repo inaccessible or special access required |
| 9 | Architecture claims | `architecture_claims` | Strategy cites specific architecture context files/APIs | Unsubstantiated architecture claims |

#### Investigation epics → `investigation_signals` (5 signals)

**Do not use the 9 Implementation signals for Investigation epics.** Those penalize the *defining* traits of an investigation (open questions, no prior foundation, external dependency), so they mis-route nearly every investigation to Low — even ones the AI resolves easily. Instead score these five, which predict whether the AI investigation skill can resolve the unknowns (→ assign to the skill) or a person must (→ assign to a person). Write the values into `investigation_signals`.

| # | Signal | Frontmatter key | Range | How to score |
|---|--------|-----------------|-------|--------------|
| 1 | Question specificity | `question_specificity` | -1 / 0 / +1 | +1 concrete, enumerable questions with a defined pass/fail criterion; -1 vague or criterion undefined (a human must frame it first) |
| 2 | Source accessibility | `source_accessibility` | 0 / +1 | +1 a readable artifact that **contains or determines the answer** exists (open repo, docs, public record). 0 when the only readable material does **not** hold the answer — e.g. a decision/PR still under review (the answer doesn't exist yet) |
| 3 | Local runnability | `local_runnability` | 0 / +1 | +1 the components can be exercised as a downloaded **binary or pip/npm library** locally — including backing a datastore with an embedded/bundled server (e.g. `pgserver` for PostgreSQL). 0 only if it genuinely can't run without a container/cluster |
| 4 | Cluster/hardware dependence | `cluster_hardware_dependence` | -2 / -1 / 0 | 0 none. -1 some *peripheral* questions need a live cluster / GPU / prod-scale. -2 a *gating* question does. **What an operator/controller _generates_** (rendered CR, template, NetworkPolicy, RBAC in its source repo) is readable source — score it under `source_accessibility`, **not** here; only the *as-deployed runtime state* of a live cluster counts as a dependence |
| 5 | Human judgment required | `human_judgment_required` | -2 / -1 / 0 | 0 none. -1 a *peripheral* question needs a stakeholder/product decision or external-party input. -2 the *gating* question is itself a human/governance decision or external coordination the AI cannot make (e.g. "is this ADR approved?") |

The pipeline routes: **High** → assign to the investigation skill; **Medium** → hybrid (skill resolves the desk/local parts and hands a spec to a human for the rest); **Low** → assign to a person.

Write the signal rationale to a separate file `artifacts/epic-tasks/{ID}-ENNN-ai-signals.md` (one per epic), for whichever signal set you used. Format as a markdown table with Signal, Value, and Rationale columns. Do not write the signals table into the epic body.

## Step 6.5: Health Warnings

### Non-blocking warnings (decomposition proceeds, human verifies later)
- Priority inversions (P0/P1/P2 inconsistent with technical complexity/reliability)
- Scope traps: ACs implying infrastructure that doesn't exist, contradictions between out-of-scope items and NFRs, storage beyond design intent

### Handling unclear strategy passages

When the strategy is unclear about something, apply this two-way distinction:

| Situation | Handling |
|-----------|---------|
| **Implementation detail** — the team will resolve this when they start the work (version choices, API surface discovery, config decisions, validation of assumptions) | Not a flag. Capture as an AC on the relevant epic if needed. Most unclear passages fall here. |
| **Genuine unknown** — nobody knows yet, resolution requires technical work, and the answer changes which downstream epics exist or what they do | Investigation epic with conditional downstream epics. |

When a strategy explicitly flags something as an open question, pending review, or conditional ADR, treat it as a genuine unknown — the strategy author has already classified it. Do not downgrade to an implementation detail based on descriptive text elsewhere in the strategy. A detailed description of current state is context, not a decision. If the strategy indicates the resolution could require a different approach (conditional ADRs, "if X requires changes to Y"), evaluate which downstream epics build on the current assumption and gate them on the resolution.

## Step 7: Derive Acceptance Criteria

Each epic gets acceptance criteria derived from:
- Strategy acceptance criteria allocated to this epic's scope
- HLRs mapped to this epic
- Implementation-specific requirements (build pipeline green, rollback plan, doc review, etc.)
- Rules 23-25 from Step 5

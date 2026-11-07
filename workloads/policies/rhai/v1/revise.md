## Step 2: Apply Corrections

**Only fix issues the reviewer identified.** If a criterion scored full points, don't touch it. Never rewrite the entire decomposition from scratch.

If the review passed all criteria with no issues to fix, skip to Step 3.

For each issue in the review's `issues` list, apply the appropriate correction:

### HLR Coverage issues
- **Missing HLR mapping**: Find the unmapped HLR in the strategy. Either add it to an existing epic's "HLR Traceability" section (if it fits that epic's scope) or create a new epic to cover it.
- **Priority inheritance error**: Adjust the priority field of prerequisite epics. An epic that transitively enables P0 work must be P0.

### DAG Coherence issues
- **Unjustified blocking edge**: Remove the dependency from the downstream epic's `dependencies` list. Verify the edge isn't required by any DAG construction rule before removing.
- **Missing blocking edge**: Add the dependency. Verify it's justified by a construction rule.
- **Circular dependency**: Break the cycle by removing the least-justified edge.

### Epic Boundary issues
- **Triage inconsistency** (`triage: below-threshold` or `docs-only` with `epic_count > 1`): The decomposer applied multi-epic logic after triage should have stopped the flow. Merge all epics back into a single epic `{ID}-E001.md`. Combine the scope, acceptance criteria, and HLR traceability from all existing epics into the single file. Delete the extra epic files and their AI signal files. Update the decomposition summary to reflect `epic_count: 1` and `critical_path_length: 1`.
- **Scope duplication across sibling epics**: Two or more epics independently build the same data structure, UI surface, or API endpoint. Merge the overlapping epics into one, or re-split along an architectural sub-system boundary (e.g., backend vs. frontend) instead of the current axis. Deduplicate acceptance criteria and scope. Delete any epic files that were merged away and update the decomposition summary.
- **Multi-component/team epic**: Split into separate epics per the component/team tuple rule. Assign new epic IDs continuing the sequence (e.g., if last epic is E007, new ones are E008, E009).
- **Oversized epic**: Split by sub-deliverable into smaller epics.

### Type Correctness issues
- **Investigation should be Implementation**: Change the `type` field to Implementation. Remove downstream blocking edges if the epic doesn't actually determine downstream structure. Add concrete artifact description.
- **Implementation should be Investigation**: Change the `type` field to Investigation. Add downstream blocking edges to epics whose scope depends on this epic's outcome.

### AI Implementability Scoring issues
- **Signal value incorrect**: Re-evaluate the specific signal(s) the reviewer identified as wrong. Update the values in the epic's type-appropriate frontmatter field — `ai_signals` for Implementation epics, `investigation_signals` for Investigation epics — and the corresponding rationale in `artifacts/epic-tasks/{ID}-ENNN-ai-signals.md`. Do **not** update `ai_implementability` or `ai_implementability_score` — the pipeline recomputes those automatically from the signal values.

### Acceptance Criteria issues
- **Missing rule-mandated AC**: Add the required AC to the epic:
  - Replacement epic → rollback/feature-flag AC
  - `docs-authoring` epic → "technically reviewed against implementation" AC
  - Epic with `konflux-onboarding` in dependency chain → "build pipeline green" AC
- **Vague/untestable AC**: Rewrite to be specific and measurable, derived from the strategy's acceptance criteria.

### Completeness issues
- **Missing strategy scope**: Create new epic(s) to cover the gap. Follow the same frontmatter schema and body structure as existing epics. Assign new epic IDs continuing the sequence.

**When rewriting or adding epic body content:** Do not reference sibling epics by draft ID (e.g., "E001", "E003"). Describe dependencies in plain terms (e.g., "the OTEL env var injection capability" not "E003"). Draft IDs are internal to the decomposition and meaningless in Jira.

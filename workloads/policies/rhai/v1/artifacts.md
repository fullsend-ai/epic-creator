# RHAI artifact body templates

Write bodies without frontmatter delimiters. Metadata is set with the shared
frontmatter helper, inheriting provenance from the source strategy.

## Decomposition summary

- **Epic List** (table: ID, title, type, team, priority)
- **Dependency DAG** (Mermaid `graph TD` diagram — roots at top, arrows point from dependency to dependent: `E001 --> E003` means "E001 must complete before E003 can start")
- **DAG Justification** (table: edge, rule, rationale)
- **HLR Traceability Matrix** (HLR → epic mapping, confirming full coverage)
- **Health Warnings** (priority inversions, scope traps — if any)
- **Tiered Delivery** (if applicable — Tier 1 vs Tier 2 split)

## Per-epic body

Minimum sections; add additional sections when the strategy contains relevant
content for this epic's scope, such as risks, assumptions, open questions or
stakeholder commitments:

- **Title** (one line)
- **Description** (what this epic delivers)
- **Scope** (specific changes in this epic)
- **Acceptance Criteria** (derived from strategy)
- **HLR Traceability** (which strategy HLRs this epic covers)

Signal rationales go in a separate file, never in the epic body. Branch epics
use the same body structure as main-plan epics.

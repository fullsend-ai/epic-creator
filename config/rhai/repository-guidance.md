# Read the RHAI repository sources

Use the approved inventory and contribution-rules.md to establish where changes
must be contributed. Use repository-mapping.md to connect Jira components to
product repositories and artifacts. That imported document describes downstream
repositories; do not assume the same names identify approved contribution targets.

Use release-appropriate Architecture Context documents to understand responsibilities,
APIs and dependencies. Read applicable active overlays before generated component
documents; disregard superseded or release-inapplicable overlays.

Preserve every strategy requirement and acceptance condition. Manager and
cloudmanager work in one approved operator repository belongs to one epic.
Dashboard source changes belong to the approved Dashboard repository's epic.
Required documentation follows its approved publication repository.

Repository names explicitly supplied by the strategy must be validated against
the inventory and evidence. Missing contribution rules, conflicting evidence and
unknown publication destinations remain unresolved; never select a default target.

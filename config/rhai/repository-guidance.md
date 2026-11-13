# Read the RHAI repository sources

Use the approved inventory and contribution-rules.md to establish where changes
must be contributed. Use repository-mapping.md to connect Jira components to
product repositories and artifacts. That imported document describes downstream
repositories; do not assume the same names identify approved contribution targets.

Use release-appropriate Architecture Context documents to understand responsibilities,
APIs and dependencies. Read applicable active overlays before generated component
documents; disregard superseded or release-inapplicable overlays.

Preserve every strategy requirement and acceptance condition. Plan one Epic per
planned PR, with exactly one approved contribution repository per PR. One
repository may have several Epics for distinct, reviewable PRs, including manager
and cloudmanager changes when they warrant separate PRs. Keep incidental docs
and tests with their PR. Required documentation follows its approved publication
repository. Preserve dependencies within a repository and across repositories.

Repository names explicitly supplied by the strategy must be validated against
the inventory and evidence. Missing contribution rules, conflicting evidence and
unknown publication destinations remain unresolved; never select a default target.

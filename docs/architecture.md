# Epic Creator architecture

Epic Creator retrieves product strategies, drafts implementation Epics, reviews
and revises them, and publishes the resulting Epics to Jira.

## Repository selection

**Decided (target state):** After final review, a separate RepoMapper reads
each Epic, its parent strategy and Architecture Context without modifying
the drafts. The existing publisher writes the selected repository URL, or
leaves it empty and adds an explanatory Jira comment when selection is
uncertain ([ADR 3](ADRs/0003-select-repositories-after-epic-review.md)).

Changing how work is divided into Epics to fit repository boundaries remains
outside this delivery (see [repository-selection problem](problems/repository-selection.md)).

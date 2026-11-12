# Repository selection for implementation Epics

Epic Creator's existing publication flow does not populate a repository URL.
A Jira component may span several repositories, so an implementer needs the
Epic's requested change and architectural context to identify where to work.

## Open questions

- ~~Where should repository selection happen in the workflow?~~ After final
  Epic review, in a separate agent ([ADR 3](../ADRs/0003-select-repositories-after-epic-review.md)).
- ~~What happens when the repository cannot be determined?~~ Publish the Epic
  with an empty URL and an explanatory Jira comment ([ADR 3](../ADRs/0003-select-repositories-after-epic-review.md)).
- How should repository information affect the division of work into Epics?
  That remains outside the isolated mapper delivery; see the
  [broader proposal](https://github.com/fullsend-ai/epic-creator/pull/45).

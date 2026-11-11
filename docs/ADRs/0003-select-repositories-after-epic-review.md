---
title: "3. Select repositories after Epic review"
status: Accepted
relates_to:
  - repository-selection
topics:
  - epic-creator
  - repositories
  - jira
---

# 3. Select repositories after Epic review

Date: 2026-11-11

## Status

Accepted

## Context

Epic Creator turns a product strategy into implementation Epics and publishes
them in Jira. It currently does not populate a repository URL for those Epics,
so the person or coding agent implementing one must first determine where to
make the change (see [AISDLC-719](https://redhat.atlassian.net/browse/AISDLC-719)).

A component can span several repositories, so its name alone may not identify
the right one. Epic Creator already has the Epic text, its parent strategy
and Architecture Context: documents describing components and their
repositories. These inputs may identify a repository, but sometimes leave
the answer uncertain. This ADR aims to add repository selection using those
inputs while preserving the existing process for drafting and reviewing Epics.

## Decision

We add a separate repository-selection agent after final Epic review to
identify where each Epic should be implemented without changing its content.

The agent, called RepoMapper, reads the final Epic, its parent strategy and
the existing Architecture Context. It returns one repository URL with a reason
and supporting evidence, or explains why it cannot determine one. It does not
rewrite or split Epics, change their dependencies, or contact Jira. We choose
this approach to keep repository selection separate from decomposition.

The existing publisher writes a resolved URL to the Epic's configured Jira URL
field. When the repository cannot be determined, it still creates the Epic,
leaves the URL empty and adds a comment explaining the uncertainty. A person
can then supply the URL. This change applies to newly drafted Epics; it does
not remap existing Jira Epics or overwrite manually supplied URLs.

## Forge scope

Repository selection is forge-neutral: targets may be on GitHub, GitLab or
Forgejo. The agent returns a repository URL; this decision adds no forge API
calls or changes to how code is submitted on any of those services.

## Consequences

- New Jira Epics can identify their implementation repository before work starts.
- Uncertain assignments remain visible for human correction without preventing Epic creation.
- Repository selection can be evaluated separately, while existing decomposition evaluations can check for regressions.
- Incomplete Architecture Context can leave assignments unresolved and needs maintenance.
- An Epic may still require several repositories; changing Epic boundaries remains separate work.

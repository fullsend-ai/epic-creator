# Epic Creator Fullsend Agent

**Note**: the Fullsend agent for `epic-creator` is under development and in an
early stage. See the [Fullsend documentation](https://fullsend.sh) for running
agents locally, and the [epic-creator agent README](./epic-creator/README.md)
for this agent's setup.

This folder contains the agent implemented for [Fullsend](https://fullsend.sh)
that makes use of the existing `epic-decompose` skill.

From a checkout of this repository, run it directly with
`fullsend run epic-creator --fullsend-dir .fullsend --target-repo "$(pwd -P)" --env-file /absolute/path/to/epic-creator.env`;
the [epic-creator agent README](./epic-creator/README.md) lists the prerequisites
and environment variables.

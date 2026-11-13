# Quick case set

`quick-mode.txt` names the cases for quick mode: a fast subset to run while
iterating on the skill. It does not replace the full suite in this directory,
and a passing quick run is not evidence that the full suite passes.

The selected cases cover distinct core paths while keeping the run short:

- **009:** triage a strategy that does not need decomposition.
- **030:** decompose a real operator logging strategy using SME input.

This quick mode solution is temporary and will likely be replaced by [AISDLC-734](https://redhat.atlassian.net/browse/AISDLC-734).

Each line is a full case directory name in this directory. Keep the file to
bare names, one per line, so readers can consume it without parsing.

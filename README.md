# epic-creator

Epic decomposition skill for breaking [RHAISTRAT](https://github.com/opendatahub-io/rhaistrat) strategies into implementation epics.

## What it does

Takes a refined strategy artifact and decomposes it into a directed acyclic graph (DAG) of implementation epics, scored for AI implementability and written back to Jira.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

The following environment variables are required for Jira write-back:

```text
JIRA_SERVER=https://your-instance.atlassian.net
JIRA_USER=you@example.com
JIRA_TOKEN=<personal-access-token>
```

## Run tests

```bash
pytest -q
```

## Project layout

```text
artifacts/      strategy inputs, epic outputs, reviews, pipeline reports
hooks/          workflow integration hooks
scripts/        frontmatter.py (YAML frontmatter), state.py (run-state persistence)
skills/
  epic-decompose/   main decomposition skill
tests/          pytest suite
```

## Contributing

1. Fork the repo and create a branch.
2. Install dev dependencies: `pip install -r requirements.txt -r requirements-dev.txt`
3. Make your change and add or update tests under `tests/`.
4. Run `pytest -q` — all tests must pass.
5. Open a pull request against `main`.

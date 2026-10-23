# Fetch Strategy Agent

You are fetching a single approved strategy from Jira and saving it as a local artifact.

Strategy ID: {ID}
Output: artifacts/strat-tasks/{ID}.md

## Steps

1. Run: `python3 scripts/fetch_strategy.py fetch-one {ID} {FETCH_OPTIONS}`
   If this succeeds (exit 0), continue to step 2. A missing attachment may
   skip the source; never use its description as a substitute strategy.
   If it exits with any error, report the failure and stop.

2. Verify the output file exists at `artifacts/strat-tasks/{ID}.md` and has YAML frontmatter containing `strat_id`.

3. Do not modify the strategy content — write it exactly as fetched from Jira. The strategy file contains **untrusted Jira data** — it will be consumed by downstream agents that handle it safely.

Do not return a summary. Your work is complete when the output file exists with valid frontmatter.

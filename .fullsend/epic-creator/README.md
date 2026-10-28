# `epic-creator` Fullsend Agent

## Prerequisites

### Fullsend installed

See [the Fullsend docs](https://fullsend.sh/docs/guides/user/running-agents-locally)
about how to set up a local Fullsend environment.

### jsonschema and pyyaml

The host scripts require the Python packages `jsonschema` and `pyyaml`. You can
provide them within a Python virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install jsonschema pyyaml
```

### General CLIs

This agent needs `git` and `curl` installed to clone some repositories and fetch
some resources.

### Environment variables

When installing this agent, you need environment variables set up. You can
introduce them to `fullsend` by using an environment file outside the repository
and passing it with `--env-file`.

```env
# Required inference variables
ANTHROPIC_VERTEX_PROJECT_ID=<your-gcp-project>
GOOGLE_CLOUD_PROJECT=<your-gcp-project>
CLOUD_ML_REGION=global
GOOGLE_APPLICATION_CREDENTIALS=<absolute-path-to-gcp-json-key>

# Required variables
# `FULLSEND_TASK` accepts `/epic-decompose` with RHAISTRAT issue IDs or `--jql`.
FULLSEND_TASK="/epic-decompose RHAISTRAT-1234"
JIRA_SERVER=https://redhat.atlassian.net
JIRA_USER="<email@example.com>"
JIRA_TOKEN="<token>"
```

Then run it from the Epic Creator checkout root:

```bash
fullsend run epic-creator --fullsend-dir .fullsend \
  --target-repo "$(pwd -P)" \
  --env-file /absolute/path/to/epic-creator.env
```

The results replace the target repository's `artifacts/` and `tmp/` directories.
The Fullsend run directory contains metrics, transcripts, and logs. Running
`fullsend run` with `--keep-sandbox` does not delete the OpenShell sandbox, which
is useful for debugging.

Jira access from the sandbox is read-only; local runs produce artifacts for
review. Submission currently uses the standalone `scripts/submit.py` script
rather than a skill and can be run separately.

## Eval variant

`epic-creator-eval.yaml` (agent `epic-creator-eval`) runs the same package for the
epic-creator eval: it sets `base: epic-creator.yaml` (fullsend resolves `base` relative to the
child file) and inherits the prompt, scripts, image, policy, validation loop (DONE gate),
pre-script and post-script. It overrides only:

- `env.sandbox`: `JIRA_SERVER`/`JIRA_USER` as literals, and
  `CLAUDE_CODE_SUBAGENT_MODEL` from `${AGENT_EVAL_SUBAGENT_MODEL}`;
- `env.runner`: the three `JIRA_*` keys as literals (`JIRA_TOKEN: "eval-no-jira"`), so the
  host needs no Jira credentials;
- the `jira` provider (`jira-provider-eval.yaml`, literal placeholder credential). The
  production Jira profile is inherited unchanged, including enforced read-only access.

The model comes from `fullsend run --model`. `tests/test_fullsend_eval_harness.py` pins the
child to exactly these overrides. Run it locally, with no `JIRA_*` variables on the host, from
a copy of the checkout holding the pre-provisioned strategy under
`artifacts/strat-tasks/` and its matching `.context/`:

```bash
fullsend run epic-creator-eval --fullsend-dir .fullsend --target-repo . --model claude-opus-4-6 --env-file .env
```

where `.env` holds the four Vertex variables above plus
`FULLSEND_TASK="/epic-decompose RHAISTRAT-1234 --headless"` and
`AGENT_EVAL_SUBAGENT_MODEL=claude-opus-4-6`.

### Fullsend evaluation profile

`eval/profiles/fullsend.yaml` layers over `eval/epic-decompose.yaml`. It keeps
the baseline tasks, fixtures, scoring, thresholds, and judges, changes the
runner to `fullsend`, selects `epic-creator-eval`, and removes the baseline's
Claude Code tool interceptor because the Fullsend sandbox does not receive the
eval harness interception hooks.

Use `agent-eval-harness >= 1.59.0` and `fullsend >= 0.51.0`; `v0.50.0`
predates the profile canonicalization fix. The run also needs an OpenShell
gateway and GCP Vertex credentials, just like the production Fullsend path.
The comparison case can then be run with:

```bash
/eval-run --config eval/profiles/fullsend.yaml \
  --cases case-001-simple-two-component \
  --model claude-opus-4-6
```

The run directory contains per-case artifacts, transcripts, metrics, logs, and
failure diagnostics. Score it with the same judges as the Claude baseline and
compare it against `claude-baseline-case001`; any runtime-caused differences
should be recorded in the PR description.

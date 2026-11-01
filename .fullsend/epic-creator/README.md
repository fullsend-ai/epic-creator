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

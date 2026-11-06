# Verify planning workloads without live Jira writes

Run the CLI-to-HTTPS tests from the repository root:

```bash
python3 -m pytest -q tests/test_workload_e2e.py
```

Use Python 3.10+, the repository's `requirements.txt`, pytest and OpenSSL with
`req -addext` support. The default run needs no Jira credentials or model access.
It makes isolated copies of the scripts, skills and workload descriptors under
pytest's temporary directory. Commands and HTTP requests remain there as
`commands.json` and `http-requests.json`; `--basetemp /tmp/workload-e2e-run` selects
a dedicated location. Pytest clears that location on subsequent runs, so copy
evidence elsewhere before reusing it.

The tests run the real fetch, frontmatter, pipeline state, scoring, report and
submission CLIs. Jira is a local HTTPS substitute with synthetic source issues,
attachments and credentials. A Python audit hook restricts the CLI subprocesses
to that server's loopback port. Tests also verify the network restriction and
that credential-free dry runs make no HTTP requests.

The default model worker is deterministic. It supplies realistic artifacts at
the planning boundary, including AI rationale sidecars, an Investigation gate,
a main Implementation gated by it, and a conditional branch. A failed review
forces an actual acceptance-criteria revision and re-review; the revised text
must reach the submitted Jira description. Both shipped workloads and a
test-only descriptor with a different destination, policy, labels and link types
run through REPORT and local submission.

Other cases reject revoked approval, changed source attachments, stale
provenance/policies, failing review, duplicate recovery identities, unknown
workloads and missing required attachments. Main Epic gates must name an existing
main Investigation included in their dependencies. Recovery cases simulate a server
persisting an issue or attachment before losing its HTTP response. Reruns must
complete without duplicate issues, duplicate evidence or premature completion.

## Optional real-model planning

With an authenticated Claude CLI already configured, run:

```bash
WORKLOAD_E2E_MODEL=1 python3 -m pytest -q -s tests/test_workload_e2e.py -k tool_free_model
```

This sends the existing synthetic trivial-rename evaluation fixture, a synthetic
architecture stub, the selected shared prompts/policies, templates and real
frontmatter schemas to the configured model. Each phase starts in a fresh CLI
context. Model tools and MCP servers are disabled, settings sources are disabled,
and Jira environment variables are removed. The model returns structured
artifact edits; a local adapter applies them through the frontmatter CLI.
Provider authentication remains in the CLI's normal credential handling.

Each model phase has a five-minute timeout and a $3 CLI budget. The run uses paid
inference and can invoke further review/revision phases if the real review
requires changes. It saves responses and usage metadata in `model-evidence/`.
Real CLI validation, scoring, REPORT, credential-free dry run and submission to
the local HTTPS substitute follow. No model tools can submit to Jira.

## Scope of the evidence

The deterministic cases verify orchestration and submission behavior; they do
not measure model quality. The opt-in case adds one real-model planning example,
which may pass review immediately and therefore exercise a no-op revision phase.
It is an adapter run, so native Skill/Agent tool dispatch and production Fullsend
configuration remain unverified. The local server also cannot establish real
Jira permissions, project schemas, link configuration or search-index timing.
The tests run the production ADF conversion but do not verify Jira's UI rendering.

Run the broader suites separately when changing production code:

```bash
python3 -m pytest -q tests/test_workload_submit.py
python3 -m pytest -q
```

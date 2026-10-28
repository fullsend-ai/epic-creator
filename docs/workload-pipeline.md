# Workload planning policies

Workload selection now follows a local run through FETCH, DECOMPOSE, REVIEW,
REVISE and REPORT. Both shipped descriptors use the existing RHAI decisions:
`rhaistrat-strategy` accepts legacy strategies and `rhai-initiative` accepts
approved RHOAIENG Initiative attachments. The architecture is still
[Proposed](ADRs/0001-descriptor-driven-planning-workloads.md).

## Where strategies and policies live

An Initiative's strategy is its newest exact `{key}-strategy.md` Jira attachment.
Fetching stores the validated local strategy in `artifacts/strat-tasks/{key}.md`.
Planning rules live separately in the repository:

```text
workloads/
  rhai-initiative.yaml
  rhaistrat-strategy.yaml
  _schema/{workload,scoring}.schema.json
  policies/rhai/v1/
    artifacts.md
    decompose.md
    review.md
    revise.md
    scoring.yaml
```

The descriptor's `planning` block references those files, a component project
and local cache, and the ordered review criteria and gate. References must stay
inside `workloads/policies/`; missing files, escaping paths and invalid scoring
thresholds fail validation. Policies are reviewed data and Markdown instructions.
The registry does not load executable plugins.

Shared prompts retain stage procedure, artifact locations and validation steps.
The dispatcher supplies `POLICY_FILE`, `ARTIFACT_TEMPLATE`, `COMPONENT_FILE`,
`PASS_SCORE` and `MAX_SCORE` from the bound descriptor. RHAI scoring retains its
existing investigation guards; versioned scoring data supplies the thresholds.

Workers can inspect the selected frontmatter contract before writing:

```bash
python3 scripts/frontmatter.py schema epic-task --workload rhai-initiative
python3 scripts/frontmatter.py schema decomp-review --workload rhai-initiative
```

The Epic contract lists canonical Jira component names from the selected cache.
Typed Epic writes reject unknown names before changing frontmatter. The review
contract gives ordered criterion scores and each issue's exact criterion-name
enum. Rejected writes must be corrected before the worker marks its phase done.

## Run and resume

In an agent environment that exposes the existing skill:

```text
/epic-decompose RHOAIENG-9001 --workload rhai-initiative --headless
```

The key above is illustrative. Use a real approved Initiative with both
Strategy Creator approval labels and the strategy attachment. This skill performs
Jira reads and creates local artifacts and reports. It does not invoke submission.
Bootstrap also reads the selected component project and architecture context.

Without `--workload`, invocation preserves the legacy RHAISTRAT path. Explicit
selection is passed to fetching and component bootstrap. An intake-only descriptor
cannot initialize a planning run.

Pipeline state records workload ID/version, descriptor digest, planning content
digest and source bindings. On an unfinished run, resume its dispatch loop rather
than initializing a new state. Changed descriptor data or policy bytes reject
resume. Generic state edits cannot switch the workload binding. A changed source
revision also fails once the FETCH boundary has bound it.

Adding `planning` or `submission` changes the shipped descriptor digests. Typed artifacts from
earlier implementations need a fresh fetch and planning run. Old legacy
artifacts without workload metadata remain supported by default invocation.

## Artifact and review checks

Agent frontmatter commands use `--source artifacts/strat-tasks/{ID}.md` to copy
validated source provenance. Completed stage boundaries require the same metadata
on summaries, main and conditional branch epics, and reviews. Missing epics,
conflicting identity or omitted provenance stop the pipeline before advancing.

Reviews include ordered `criterion_scores`. Validation checks their count, integer
range, total, pass status and recommendation against the selected gate. Critical
or major issues require zero points for the affected criterion. The RHAI gate
continues to require at least 10/14 and no zero-scoring criterion. Shared wave
scheduling and the existing two revision cycles are retained.

Reports record the workload/version, both digests, gate and each source revision.
[Submission and Jira recovery](workload-submission.md) are available through the
explicit submission CLI. The planning skill still stops at REPORT; production
runner configuration and real model quality evaluation remain subsequent work.

## Testing without Jira writes

Run the deterministic local contract tests:

```bash
python3 -m pytest -q tests/test_workload_pipeline.py
python3 -m pytest -q tests/test_workload_intake.py tests/test_fetch_strategy.py \
  tests/test_artifact_utils.py tests/test_frontmatter_cli.py \
  tests/test_check_decompose_progress.py tests/test_compute_ai_scores.py \
  tests/test_fetch_components.py tests/test_workload_pipeline.py
```

The flow tests use synthetic cached strategies and deterministic agent outputs
with socket and URL access blocked. They exercise the actual registry, dispatcher,
artifact checks, revision/re-review, scoring and report for RHOAI and a second
synthetic workload. They prove local orchestration and contracts; they do not
measure model-generated plan quality. Separate legacy artifact tests cover
compatibility. Eval staging now copies the registry and policies into its isolated
workspace so existing model evaluations can load them.

The full suite has a known upstream failure in
`tests/test_submit.py::TestDryRun::test_dry_run_shows_component_warning` when local
component context is absent. Report it separately from workload failures.

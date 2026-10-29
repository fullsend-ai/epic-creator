# Epic Creator and Decomposer: Human Review Guide

*For engineers, SMEs, and architects looking at the epics this pipeline creates.*

**In short:** twice a day, this pipeline reads the RHAISTRAT strategies that have
passed the strategy rubric, been signed off by a human, and are targeted at 3.6,
and turns each one into
a reviewed **proposal** — a dependency graph (DAG) of implementation epics created
in the **RHAI** Jira project. Every breakdown clears an adversarial quality review
*before* a single epic is created, so what lands in Jira is something we stand
behind. Every epic it creates carries the label `epic-creator-auto-created`.

Start with the epic DAG for your strategy. If something doesn't fit your plan,
adjust it — and please tell us in **#wg-rhai-epic-and-code-gen** so we can keep
making it better.

| Repo | What it is |
|---|---|
| [`epic-creator`](https://github.com/opendatahub-io/epic-creator) | The brain — the skill and prompts that do the decomposition. |
| [`epic-decomposer`](https://gitlab.com/redhat/rhel-ai/agentic-ci/epic-decomposer) | The runner — the CI job that runs it against Jira twice a day. |
| [`epic-decomposer-results`](https://gitlab.com/redhat/rhel-ai/agentic-ci/epic-decomposer-results) | The results store — every run's epics, reviews, and a dashboard, versioned by timestamp. |

## How it works

Twice a day the pipeline:

1. **Reads** the signed-off strategies that qualify (see below).
2. **Decomposes** each into a DAG of epics — Implementation and Investigation
   epics, with dependency links between them.
3. **Reviews** the breakdown adversarially and scores it out of 14. If it doesn't
   pass, it's revised and re-reviewed. **Nothing is created in Jira until the
   review passes.**
4. **Creates** the epics in **RHAI**, wired together with "Blocks" dependency links.
5. **Publishes** a visual DAG and an HTML dashboard to `epic-decomposer-results`.

As a reader you mostly care about three things: the **epic list** for your
strategy, each epic's **acceptance criteria**, and the **rendered DAG** that shows
how the epics depend on one another. The easiest place to see all three is the
**[dashboard](https://epic-decomposer-dashboard-4e1d18.gitlab.io/)** — open the
**Strategy Tracker** tab and find your RHAISTRAT. The same content also lives in
`epic-tasks/RHAISTRAT-<id>-decomposition.md` in the results repo.

> **Coming soon:** we'll be integrating this with **Org Pulse**, so you'll be able
> to see the decomposed epics alongside the rest of your team's work there too.

> **Note:** this only generates *epics* right now — we are **not** generating code
> automatically yet. This is still a work in progress and we're experimenting with
> a small number of epics. If you have an epic that's boring or small and you'd
> like us to give it a try, let us know in **#wg-rhai-epic-and-code-gen**.

## Which strategies we generate epics for

A strategy enters the pipeline when it is a **RHAISTRAT Feature** that is:

> labeled **`strat-creator-rubric-pass`** **and** **`strat-creator-human-sign-off`**,
> with a **Target Version of 3.6** (any of the 3.6 EA/GA values).

In plain terms: it passed the strategy rubric, a human signed off on it, and it's
targeted at 3.6. That target-version filter is the one thing we bump each release —
right now it's 3.6 only.

## When we don't create epics

We deliberately stay out of the way in these cases:

- **It already has epics.** If the strategy already has child Epics or a legacy
  outgoing "Incorporates" link to an Epic, we skip it and never re-decompose —
  your existing breakdown wins. Links to Outcomes or other non-Epic issues do
  not trigger this skip.
- **We couldn't check its existing epics.** If Jira lookup fails or a linked
  issue's type cannot be determined, we log a warning and skip that strategy
  for this run to avoid duplicate Epics. The remaining strategies continue.
  If any checks fail and no strategies are fetched, the fetch command exits
  with an error. The fetch still succeeds when every strategy is confirmed
  to already have Epics.
- **The quality review didn't pass.** A breakdown has to score **≥ 10 out of 14
  with no criterion scoring zero**. If it doesn't clear that bar, nothing is
  submitted to Jira. 
- **It's below the threshold.** Small strategies (S-sized, one component, one
  team, mostly high-confidence work) collapse to a single epic instead of a DAG.
- **It's documentation only.** If every affected component is "no code changes" or
  "reference only," you get one docs-authoring epic.
- **A component is passive.** A component that only needs to keep working (no code
  changes) becomes an acceptance criterion on the nearest active epic rather than
  its own epic — unless a *different* team has to validate it, in which case it
  gets its own validation epic.

## Investigation epics

Epics come in two types:

- **Implementation** — produces an artifact (code, config, docs, manifests, an
  upstream PR, and so on).
- **Investigation** — resolves an uncertainty that a downstream decision depends
  on.

The line between "this deserves its own Investigation epic" and "this is just an
acceptance criterion" comes down to one question:

> **Does the answer change which downstream epics exist or what they do?**
> **Yes** → it's an Investigation epic, and the epics that depend on its outcome
> are gated on it. **No** → it's an acceptance criterion on an Implementation epic.

Because an investigation is inherently more open-ended than implementation work,
it's routed by its own scoring: **High** → handled by the AI investigation skill,
**Medium** → a hybrid where the skill does the desk/local work and hands a spec to
a human, **Low** → assigned to a person.

> **Heads up:** investigation epics aren't 100% automated yet. If you land on one
> and aren't sure how to take it forward, **ping us in #wg-rhai-epic-and-code-gen**
> and we'll help.

## Labels

Every epic and strategy the pipeline touches is labeled so you can find and filter
them.

**On each auto-created epic (in RHAI):**

| Label | Meaning |
|---|---|
| `epic-creator-auto-created` | Always applied — marks every epic this pipeline created. |
| `epic-creator-investigation` | The epic is an Investigation (vs. Implementation). |
| `epic-creator-impl-<type>` | Implementation subtype, e.g. `epic-creator-impl-docs-authoring`. |
| `epic-creator-ai-impl-*` | The epic's AI-implementability class — `high`, `medium`, or `low`. |
| `epic-creator-needs-component` | The component isn't in the canonical RHAI component list yet. |

**On the source strategy (in RHAISTRAT):**

| Label | Meaning |
|---|---|
| `epic-creator-auto-decomposed` | Applied only when **all** of the strategy's epics were successfully created. |

The two labels that decide whether a strategy *enters* the pipeline —
`strat-creator-rubric-pass` and `strat-creator-human-sign-off` — come from the
upstream strat-creator pipeline, not this one. And `epic-creator-ai-impl-*` simply
reflects the AI-implementability class described in the Advanced section below.

## If an epic isn't right — what to do

The breakdown is a reviewed proposal, but you own your delivery. Here's the flow:

1. **Start from the visual DAG** for your strategy — open the
   **[dashboard](https://epic-decomposer-dashboard-4e1d18.gitlab.io/)** and its
   **Strategy Tracker** tab. It shows every epic, its type, and how they depend on
   each other at a glance.
   That's the fastest way to sanity-check the whole shape before looking at any
   single epic.
2. **If an epic is wrong or unnecessary, just close it.** No ceremony required.
3. **Create your own epics in RHOAIENG** to complement (or replace) these if you
   need to. **RHAI is the AI-generated project**; **RHOAIENG** is the normal
   engineering backlog where humans track real delivery work.
4. **Tell us in #wg-rhai-epic-and-code-gen.** Two things help us the most:
   - whether the epic breakdown and its DAG were correct, and
   - if you created your own epics, *what* you created and *why* the generated
     ones didn't fit.

   That feedback is exactly how the pipeline keeps getting better.

> **Please keep in mind:** a slight rename of an epic title is totally fine. But if you
> want to change an epic's content or its acceptance criteria, please talk to us in
> **#wg-rhai-epic-and-code-gen** first — those are what we learn from, and quietly
> editing them makes it hard for us to tell whether the breakdown was right.

## Resources

- **Dashboard** — [epic-decomposer-dashboard](https://epic-decomposer-dashboard-4e1d18.gitlab.io/)
  (see the **Strategy Tracker** tab). Raw per-run artifacts live in
  `epic-decomposer-results`.
- **Per-strategy DAG + traceability** — `epic-tasks/RHAISTRAT-<id>-decomposition.md`.
- **How a run was scored** — `epic-reviews/RHAISTRAT-<id>-decomp-review.md`.
- **Feedback & questions** — **#wg-rhai-epic-and-code-gen**.

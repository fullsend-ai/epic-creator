#!/usr/bin/env python3
"""Generate an HTML report for decomposition pipeline runs.

Usage:
    # Generate from pipeline (called by pipeline_state.py REPORT phase)
    python3 scripts/generate_html_report.py --start-time 2026-05-27T22:56:17Z

    # Generate for specific strategies
    python3 scripts/generate_html_report.py --start-time 2026-05-27T22:56:17Z RHAISTRAT-1234 RHAISTRAT-1235

    # Custom output path
    python3 scripts/generate_html_report.py --start-time 2026-05-27T22:56:17Z --output report.html
"""

import argparse
import glob
import json
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import read_frontmatter

SIGNAL_NAMES = [
    "change_specificity", "pattern_precedent", "adapter_pattern",
    "existing_foundation", "open_questions", "external_dependency",
    "human_process_gates", "repo_access", "architecture_claims",
]

# Investigation epics carry a different signal set (see compute_ai_scores).
INVESTIGATION_SIGNAL_NAMES = [
    "question_specificity", "source_accessibility", "local_runnability",
    "cluster_hardware_dependence", "human_judgment_required",
]


def _html_escape(text):
    """Escape HTML special characters."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def read_body(path):
    with open(path) as f:
        text = f.read()
    parts = text.split("---", 2)
    if len(parts) >= 3:
        return parts[2].strip()
    return text


def extract_mermaid(text):
    m = re.search(r'```mermaid\s*\n(.*?)```', text, re.DOTALL)
    return m.group(1).strip() if m else None


def get_strat_title(strat_id):
    path = f"artifacts/strat-tasks/{strat_id}.md"
    if os.path.exists(path):
        fm, _ = read_frontmatter(path)
        if fm.get("title"):
            return _html_escape(fm["title"])
    return strat_id


def signal_class(val):
    if val > 0:
        return "signal-pos"
    if val < 0:
        return "signal-neg"
    return "signal-zero"


def priority_badge(p):
    cls = {"P0": "badge-p0", "P1": "badge-p1", "P2": "badge-p2"}.get(p, "badge-p2")
    return f'<span class="badge {cls}">{_html_escape(p)}</span>'


def ai_badge(classification, score):
    cls = {"High": "badge-high", "Medium": "badge-medium",
           "Low": "badge-low"}.get(str(classification), "")
    return f'<span class="badge {cls}">{_html_escape(classification)} ({score})</span>'


def type_badge(epic_type, impl_type):
    if impl_type and impl_type != "null":
        return f'<span class="badge badge-docs">{_html_escape(impl_type)}</span>'
    if epic_type == "Investigation":
        return '<span class="badge badge-investigation">Investigation</span>'
    return f'<span class="badge badge-impl">{_html_escape(epic_type)}</span>'


def severity_badge(sev):
    cls = {"minor": "badge-minor", "major": "badge-major",
           "critical": "badge-critical"}.get(sev, "")
    return f'<span class="badge {cls}">{_html_escape(sev)}</span>'


def render_signals(signals, names=SIGNAL_NAMES):
    html = '<div class="signals-grid">'
    for name in names:
        val = signals.get(name, 0) or 0
        cls = signal_class(val)
        sign = f"+{val}" if val > 0 else str(val)
        html += (f'<div class="signal {cls}">'
                 f'<div class="signal-dot"></div>'
                 f'<span class="signal-name">{name}</span> {sign}</div>')
    html += '</div>'
    return html


def render_deps(deps, strat_id):
    if not deps:
        return '<span style="color:var(--muted);">None</span>'
    chips = []
    for d in deps:
        short = d.replace(f"{strat_id}-", "")
        chips.append(f'<a class="dep-chip" href="#{_html_escape(d)}">'
                     f'{_html_escape(short)}</a>')
    return '<div class="deps-list">' + ''.join(chips) + '</div>'


def render_gate_info(fm):
    gated_by = fm.get("gated_by")
    gate_impact = fm.get("gate_failure_impact") or {}
    if not gated_by and not gate_impact:
        return ""

    parts = []
    if gated_by:
        parent = fm.get("parent_strat", "")
        parts.append(
            f'<div class="meta-item">'
            f'<span class="meta-label">Gated by:</span>'
            f'<a class="dep-chip" href="#{parent}-{gated_by}">'
            f'{_html_escape(gated_by)}</a></div>')

    action = gate_impact.get("action", "")
    fallback = gate_impact.get("fallback_approach", "")
    if action or fallback:
        action_html = (f'<span class="badge badge-gate-{_html_escape(action)}">'
                       f'{_html_escape(action)}</span>') if action else ""
        fallback_html = (f'<div class="gate-fallback">'
                         f'{_html_escape(fallback)}</div>') if fallback else ""
        parts.append(f'''<div class="gate-impact">
      <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.3rem;">
        <span class="meta-label">If gate fails:</span>{action_html}
      </div>
      {fallback_html}
    </div>''')

    return f'<div class="gate-info">{"".join(parts)}</div>'


def render_epic_card(fm, body):
    eid = fm["epic_id"]
    # Drive the rubric off the epic type, not signal-block presence, so an
    # Investigation epic always renders the investigation rubric even if its
    # block is empty/malformed (which then shows as zeros rather than the
    # wrong 9-signal labels).
    if fm.get("type") == "Investigation":
        signals = fm.get("investigation_signals") or {}
        signal_names = INVESTIGATION_SIGNAL_NAMES
    else:
        signals, signal_names = fm.get("ai_signals", {}) or {}, SIGNAL_NAMES
    ai_class = fm.get("ai_implementability", "?")
    ai_score = fm.get("ai_implementability_score", "?")
    impl_type = fm.get("implementation_type")

    title_match = re.search(r'^## Title\s*\n+(.+)', body, re.MULTILINE)
    title = _html_escape(title_match.group(1).strip() if title_match else eid)

    desc_onwards = re.sub(r'^## Title\s*\n+.+\n*', '', body, count=1).strip()
    body_for_js = (desc_onwards
                   .replace('\\', '\\\\')
                   .replace('`', '\\`')
                   .replace('${', '\\${'))

    gate_html = render_gate_info(fm)
    repository_html = ""
    if fm.get("target_repository"):
        target = fm["target_repository"]
        canonical = f"https://{target['host']}/{target['namespace']}/{target['repository']}"
        repository_html = f'<div class="meta-item"><span class="meta-label">Repository:</span><span class="meta-value">{_html_escape(canonical)}</span></div>'
    if fm.get("mapping_snapshot_digest"):
        repository_html += f'<div class="meta-item"><span class="meta-label">Mapping snapshot:</span><span class="meta-value">{_html_escape(fm["mapping_snapshot_digest"])}</span></div>'

    return f'''
<div class="card" id="{_html_escape(eid)}">
  <div class="epic-header">
    <span class="epic-id">{_html_escape(eid)}</span>
    <span class="epic-title">{title}</span>
    {priority_badge(fm.get("priority", ""))}
    {ai_badge(ai_class, ai_score)}
    {type_badge(fm.get("type", ""), impl_type)}
  </div>
  <div class="meta-grid">
    <div class="meta-item"><span class="meta-label">Component:</span><span class="meta-value">{_html_escape(", ".join(fm.get("components", [fm.get("component", "")])) )}</span></div>
    {repository_html}
    <div class="meta-item"><span class="meta-label">Team:</span><span class="meta-value">{_html_escape(fm.get("team", ""))}</span></div>
    <div class="meta-item"><span class="meta-label">Dependencies:</span>{render_deps(fm.get("dependencies", []), fm.get("parent_strat", ""))}</div>
  </div>
  {gate_html}
  <details>
    <summary style="cursor:pointer;font-size:0.85rem;color:var(--muted);margin-bottom:0.5rem;">AI Implementability Signals</summary>
    {render_signals(signals, signal_names)}
  </details>
  <div class="epic-body" data-body="{_html_escape(eid)}"></div>
</div>''', eid, body_for_js


def render_strategy_section(strat_id):
    """Render a single strategy section. Returns (html, body_map, stats)."""
    decomp_path = f"artifacts/epic-tasks/{strat_id}-decomposition.md"
    review_path = f"artifacts/epic-reviews/{strat_id}-decomp-review.md"

    decomp_fm, _ = read_frontmatter(decomp_path)
    decomp_body = read_body(decomp_path) if os.path.exists(decomp_path) else ""
    review_fm, _ = read_frontmatter(review_path)

    # Collect epic files — includes BRANCH files for conditional decompositions
    epic_files = sorted(
        glob.glob(f"artifacts/epic-tasks/{strat_id}-E*.md")
        + glob.glob(f"artifacts/epic-tasks/{strat_id}-BRANCH-*-E*.md")
    )
    epics = []
    for ef in epic_files:
        fm, _ = read_frontmatter(ef)
        body = read_body(ef)
        if fm:
            epics.append((fm, body))

    mermaid = extract_mermaid(decomp_body)
    score = review_fm.get("score", "?")
    passed = review_fm.get("pass", False)
    error = review_fm.get("error")
    issues = review_fm.get("issues") or []
    triage = decomp_fm.get("triage")
    epic_count = decomp_fm.get("epic_count", len(epics))
    crit_path = decomp_fm.get("critical_path_length", "?")

    # Collect stats for the overview
    stats = {
        "score": score if isinstance(score, int) else 0,
        "passed": bool(passed) and not error,
        "failed": not passed and not error and isinstance(score, int),
        "error": bool(error),
        "epic_count": epic_count if isinstance(epic_count, int) else 0,
        "issues": issues,
    }

    triage_label = ""
    if triage:
        triage_label = f' <span class="badge badge-triage">{_html_escape(triage)}</span>'

    # Score bar (handle non-int gracefully)
    score_int = score if isinstance(score, int) else 0
    score_bar = ""
    for i in range(14):
        cls = "score-filled" if i < score_int else "score-empty"
        score_bar += f'<div class="score-segment {cls}"></div>'

    # Review status styling
    if error:
        review_color = "var(--low)"
        review_text = f"Error: {_html_escape(error)}"
    elif passed:
        review_color = "var(--high)"
        review_text = f"{score}/14 Pass"
    else:
        review_color = "var(--low)"
        review_text = f"{score}/14 Fail"

    issues_html = ""
    if issues:
        items = ""
        for iss in issues:
            items += (f'<li class="issue-item">'
                      f'{severity_badge(iss.get("severity", ""))}'
                      f'<span><strong>{_html_escape(iss.get("criterion", ""))}:</strong> '
                      f'{_html_escape(iss.get("description", ""))}</span></li>')
        issues_html = f'<ul class="issue-list">{items}</ul>'
    else:
        issues_html = ('<div style="color:var(--muted);font-size:0.9rem;">'
                       'No issues found.</div>')

    mermaid_html = ""
    if mermaid:
        mermaid_html = (f'<div class="dag-container">'
                        f'<pre class="mermaid">\n{mermaid}\n</pre></div>')

    epic_cards = []
    body_map = {}
    for fm, body in epics:
        card_html, eid, body_js = render_epic_card(fm, body)
        epic_cards.append(card_html)
        body_map[eid] = body_js

    strat_title = get_strat_title(strat_id)
    title_html = ""
    if strat_title != strat_id:
        title_html = (f'<span style="font-size:0.95rem;font-weight:400;">'
                      f'{strat_title}</span>')

    sid_esc = _html_escape(strat_id)
    repository_status = ""
    plan_path = Path(f"artifacts/repository-plans/{strat_id}.json")
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text())
            context = json.loads(plan_path.with_name(f"{strat_id}-context.json").read_text())
            reasons = ", ".join(item["reason"] for item in plan.get("unresolved", []))
            planning_status = "partial" if plan.get("partial") else "ready" if plan.get("ready_for_drafting") else "blocked"
            repository_status = (f'<p>Repository planning: {planning_status}; '
                                 f'inventory revision: {_html_escape(context.get("inventory_revision", "unavailable"))}'
                                 f'{"; " + _html_escape(reasons) if reasons else ""}</p>')
        except (OSError, ValueError, KeyError):
            repository_status = "<p>Repository planning: invalid or missing context</p>"

    section = f'''
<div class="strat-section" id="{sid_esc}">
  <h2 style="display:flex;align-items:center;gap:0.75rem;flex-wrap:wrap;">
    {sid_esc}{triage_label}
    <span style="font-size:0.85rem;font-weight:400;color:var(--muted);">{epic_count} epics &middot; critical path {crit_path}</span>
  </h2>
  {f'<div style="color:var(--muted);font-size:0.9rem;margin:-0.75rem 0 1rem 0;">{title_html}</div>' if title_html else ''}
  {repository_status}

  <div class="summary-row">
    <div class="stat-card">
      <div class="stat-value">{epic_count}</div>
      <div class="stat-label">Epics</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">{crit_path}</div>
      <div class="stat-label">Critical Path</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" style="color:{review_color}">{score}/14</div>
      <div class="stat-label">Review Score</div>
      <div class="score-bar">{score_bar}</div>
    </div>
  </div>

  {mermaid_html}

  <div class="card review-card" style="border-left:4px solid {review_color};">
    <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.5rem;">
      <strong>Review:</strong> <span style="color:{review_color};font-weight:600;">{review_text}</span>
      <span style="color:var(--muted);font-size:0.85rem;">{len(issues)} issue{"s" if len(issues) != 1 else ""}</span>
    </div>
    {issues_html}
  </div>

  {"".join(epic_cards)}
</div>
<hr style="border:none;border-top:2px solid var(--border);margin:2.5rem 0;">
'''
    return section, body_map, stats


CSS = '''
  :root {
    --bg: #f8f9fa; --card-bg: #ffffff; --border: #dee2e6; --text: #212529;
    --muted: #6c757d; --accent: #0d6efd; --p0: #dc3545; --p1: #fd7e14; --p2: #6c757d;
    --high: #198754; --medium: #fd7e14; --low: #dc3545;
    --signal-pos: #198754; --signal-neg: #dc3545; --signal-zero: #adb5bd;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; padding: 2rem; max-width: 1200px; margin: 0 auto; }
  h1 { font-size: 1.75rem; margin-bottom: 0.25rem; }
  h2 { font-size: 1.35rem; margin-bottom: 1rem; color: var(--text); border-bottom: 2px solid var(--accent); padding-bottom: 0.4rem; }
  .subtitle { color: var(--muted); margin-bottom: 1.5rem; font-size: 0.95rem; }
  .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
  .summary-row { display: flex; gap: 1rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
  .stat-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 1rem 1.25rem; text-align: center; min-width: 120px; }
  .stat-value { font-size: 1.75rem; font-weight: 700; }
  .stat-label { font-size: 0.8rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
  .dag-container { text-align: center; padding: 1rem 0; margin-bottom: 1rem; }
  .badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; }
  .badge-p0 { background: var(--p0); color: white; }
  .badge-p1 { background: var(--p1); color: white; }
  .badge-p2 { background: var(--p2); color: white; }
  .badge-high { background: var(--high); color: white; }
  .badge-medium { background: var(--medium); color: white; }
  .badge-low { background: var(--low); color: white; }
  .badge-impl { background: #e9ecef; color: #495057; }
  .badge-docs { background: #cfe2ff; color: #084298; }
  .badge-investigation { background: #fff3cd; color: #856404; }
  .badge-triage { background: #d1ecf1; color: #0c5460; }
  .badge-minor { background: #fff3cd; color: #856404; }
  .badge-major { background: #f8d7da; color: #842029; }
  .badge-critical { background: #842029; color: white; }
  .epic-header { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 1rem; }
  .epic-id { font-family: 'SF Mono', SFMono-Regular, Consolas, monospace; font-size: 0.85rem; color: var(--accent); font-weight: 600; }
  .epic-title { font-size: 1.1rem; font-weight: 600; }
  .meta-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.5rem 1.5rem; margin-bottom: 1rem; padding: 0.75rem 1rem; background: #f8f9fa; border-radius: 6px; font-size: 0.9rem; }
  .meta-item { display: flex; gap: 0.4rem; }
  .meta-label { color: var(--muted); font-weight: 500; white-space: nowrap; }
  .signals-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0.4rem; margin-bottom: 1rem; }
  .signal { display: flex; align-items: center; gap: 0.4rem; font-size: 0.82rem; padding: 0.3rem 0.5rem; border-radius: 4px; background: #f8f9fa; }
  .signal-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .signal-pos .signal-dot { background: var(--signal-pos); }
  .signal-neg .signal-dot { background: var(--signal-neg); }
  .signal-zero .signal-dot { background: var(--signal-zero); }
  .signal-name { color: var(--muted); }
  .epic-body { font-size: 0.92rem; }
  .epic-body h2 { font-size: 1.05rem; border-bottom: 1px solid var(--border); margin-top: 1.25rem; }
  .epic-body ul, .epic-body ol { padding-left: 1.5rem; margin-bottom: 0.75rem; }
  .epic-body li { margin-bottom: 0.3rem; }
  .epic-body p { margin-bottom: 0.6rem; }
  .epic-body table { width: 100%; border-collapse: collapse; margin-bottom: 1rem; font-size: 0.85rem; }
  .epic-body th, .epic-body td { border: 1px solid var(--border); padding: 0.4rem 0.6rem; text-align: left; }
  .epic-body th { background: #f1f3f5; font-weight: 600; }
  .deps-list { display: flex; gap: 0.4rem; flex-wrap: wrap; }
  .dep-chip { font-family: 'SF Mono', SFMono-Regular, Consolas, monospace; font-size: 0.75rem; padding: 0.15rem 0.5rem; background: #e9ecef; border-radius: 4px; color: #495057; text-decoration: none; }
  .dep-chip:hover { background: #dee2e6; }
  .issue-list { list-style: none; padding: 0; }
  .issue-item { padding: 0.4rem 0; border-bottom: 1px solid #f1f3f5; font-size: 0.9rem; display: flex; gap: 0.5rem; align-items: baseline; }
  .issue-item:last-child { border-bottom: none; }
  .gate-info { background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; padding: 0.75rem 1rem; margin-bottom: 1rem; font-size: 0.9rem; }
  .gate-impact { margin-top: 0.3rem; }
  .gate-fallback { color: #664d03; line-height: 1.5; }
  .badge-gate-rewrite { background: #dc3545; color: white; }
  .badge-gate-remove { background: #6c757d; color: white; }
  .badge-gate-add_remediation { background: #fd7e14; color: white; }
  .score-bar { display: flex; gap: 2px; margin-top: 0.5rem; }
  .score-segment { height: 6px; flex: 1; border-radius: 3px; }
  .score-filled { background: var(--high); }
  .score-empty { background: #e9ecef; }
  .overview-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
  .overview-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; }
  .overview-card h3 { font-size: 0.95rem; margin-bottom: 0.5rem; }
  .score-dist { display: flex; gap: 2px; align-items: flex-end; height: 40px; }
  .score-dist-bar { flex: 1; background: var(--accent); border-radius: 2px 2px 0 0; min-width: 4px; }
  .summary-table { width: 100%; border-collapse: collapse; margin-bottom: 1.5rem; font-size: 0.9rem; }
  .summary-table th { background: #e9ecef; font-weight: 600; padding: 0.5rem 0.75rem; text-align: left; border-bottom: 2px solid var(--border); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.03em; }
  .summary-table td { padding: 0.4rem 0.75rem; border-bottom: 1px solid #f1f3f5; }
  .summary-table tr:hover { background: #f8f9fa; }
  .strat-link { color: var(--accent); text-decoration: none; font-weight: 600; font-family: 'SF Mono', SFMono-Regular, Consolas, monospace; font-size: 0.85rem; }
  .strat-link:hover { text-decoration: underline; }
  .table-section { position: relative; margin-bottom: 2rem; }
  .table-wrapper { }
  .table-wrapper.collapsed { max-height: 500px; overflow: hidden; }
  .table-fade { height: 60px; background: linear-gradient(transparent, var(--bg)); margin-top: -60px; position: relative; pointer-events: none; }
  .table-see-all { display: block; width: 100%; padding: 0.5rem; background: var(--card-bg); border: 1px solid var(--border); border-radius: 4px; cursor: pointer; font-size: 0.85rem; color: var(--accent); text-align: center; }
  .table-see-all:hover { background: #e9ecef; }
  .back-to-top { position: fixed; bottom: 24px; right: 24px; background: var(--accent); color: white; width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; text-decoration: none; font-size: 18px; box-shadow: 0 2px 8px rgba(0,0,0,0.3); opacity: 0; pointer-events: none; transition: opacity 0.3s; z-index: 1000; }
  .back-to-top:hover { background: #0b5ed7; }
  @media print { .back-to-top { display: none; } }
'''


def build_report(strat_ids, start_time):
    """Build the full HTML report. Returns (html_string, out_filename)."""
    all_sections = []
    all_bodies = {}
    all_stats = []

    for sid in strat_ids:
        section_html, body_map, stats = render_strategy_section(sid)
        all_sections.append(section_html)
        all_bodies.update(body_map)
        all_stats.append({"strat_id": sid, **stats})

    # Aggregate stats
    total_strats = len(strat_ids)
    total_epics = sum(s["epic_count"] for s in all_stats)
    total_passed = sum(1 for s in all_stats if s["passed"])
    total_failed = sum(1 for s in all_stats if s["failed"])
    total_errors = sum(1 for s in all_stats if s["error"])
    scores = [s["score"] for s in all_stats if s["score"]]
    avg_score = sum(scores) / len(scores) if scores else 0

    # Issue counts by severity
    all_issues = []
    for s in all_stats:
        all_issues.extend(s.get("issues") or [])
    critical_count = sum(1 for i in all_issues if i.get("severity") == "critical")
    major_count = sum(1 for i in all_issues if i.get("severity") == "major")
    minor_count = sum(1 for i in all_issues if i.get("severity") == "minor")

    # Score distribution for histogram
    score_dist = [0] * 15  # 0-14
    for sc in scores:
        if 0 <= sc <= 14:
            score_dist[sc] += 1
    max_count = max(score_dist) if score_dist else 1
    score_dist_bars = ""
    for i, count in enumerate(score_dist):
        height = int(count / max_count * 36) if max_count > 0 else 0
        color = "var(--high)" if i >= 10 else "var(--low)"
        label = f' title="{i}/14: {count}"' if count else f' title="{i}/14: 0"'
        score_dist_bars += (f'<div class="score-dist-bar" '
                            f'style="height:{max(height, 2)}px;background:{color};"'
                            f'{label}></div>')

    # Summary table rows
    summary_rows = ""
    for s in all_stats:
        sid = _html_escape(s["strat_id"])
        sc = s["score"]
        n_issues = len(s.get("issues") or [])
        n_epics = s["epic_count"]
        title = get_strat_title(s["strat_id"])
        title_cell = f' <span style="color:var(--muted);font-weight:400;">{title}</span>' if title != s["strat_id"] else ""
        if s["error"]:
            status = '<span style="color:var(--low);font-weight:600;">Error</span>'
        elif s["passed"]:
            status = f'<span style="color:var(--high);font-weight:600;">{sc}/14 Pass</span>'
        else:
            status = f'<span style="color:var(--low);font-weight:600;">{sc}/14 Fail</span>'
        issue_cell = ""
        if n_issues:
            sev_counts = {}
            for iss in (s.get("issues") or []):
                sev = iss.get("severity", "")
                sev_counts[sev] = sev_counts.get(sev, 0) + 1
            issue_cell = " ".join(f'{severity_badge(sv)} {ct}'
                                  for sv, ct in sev_counts.items())
        summary_rows += (f'<tr>'
                         f'<td><a href="#{sid}" class="strat-link">{sid}</a>'
                         f'{title_cell}</td>'
                         f'<td style="text-align:center;">{n_epics}</td>'
                         f'<td style="text-align:center;">{status}</td>'
                         f'<td>{issue_cell}</td>'
                         f'</tr>')

    # Body JS map — eid is schema-validated (alphanumeric + hyphens) but
    # escape for JS string context defensively
    bodies_js = "const epicBodies = {\n"
    for eid, body in all_bodies.items():
        eid_js = eid.replace('\\', '\\\\').replace('"', '\\"')
        bodies_js += f'  "{eid_js}": `{body}`,\n'
    bodies_js += "};"

    # Timestamp for display
    display_time = _html_escape(start_time.replace("T", " ").replace("Z", " UTC"))

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Epic Decomposition Report &mdash; {display_time}</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10.9.3/dist/mermaid.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@15.0.7/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/dompurify@3.2.6/dist/purify.min.js"></script>
<style>
{CSS}
</style>
</head>
<body>

<h1>Epic Decomposition Report</h1>
<div class="subtitle">{display_time} &middot; {total_strats} strategies &middot; {total_epics} epics &middot; avg review {avg_score:.1f}/14</div>

<div class="overview-grid">
  <div class="overview-card">
    <h3>Results</h3>
    <div style="font-size:0.9rem;">
      <div><strong>{total_strats}</strong> strategies</div>
      <div><strong>{total_epics}</strong> total epics</div>
      <div style="color:var(--high);"><strong>{total_passed}</strong> passed review</div>
      {f'<div style="color:var(--low);"><strong>{total_failed}</strong> failed review</div>' if total_failed else ''}
      {f'<div style="color:var(--low);"><strong>{total_errors}</strong> errors</div>' if total_errors else ''}
    </div>
  </div>
  <div class="overview-card">
    <h3>Issues</h3>
    <div style="font-size:0.9rem;">
      <div>{severity_badge("critical")} <strong>{critical_count}</strong></div>
      <div>{severity_badge("major")} <strong>{major_count}</strong></div>
      <div>{severity_badge("minor")} <strong>{minor_count}</strong></div>
    </div>
  </div>
  <div class="overview-card">
    <h3>Score Distribution</h3>
    <div class="score-dist">{score_dist_bars}</div>
    <div style="display:flex;justify-content:space-between;font-size:0.7rem;color:var(--muted);margin-top:2px;">
      <span>0</span><span>3</span><span>6</span><span>9</span>
    </div>
  </div>
</div>

<div class="table-section">
  <div class="table-wrapper{' collapsed' if total_strats > 20 else ''}">
    <table class="summary-table">
      <thead>
        <tr>
          <th>Strategy</th>
          <th style="text-align:center;">Epics</th>
          <th style="text-align:center;">Review</th>
          <th>Issues</th>
        </tr>
      </thead>
      <tbody>
        {summary_rows}
      </tbody>
    </table>
  </div>
  {f'<div class="table-fade"></div><button class="table-see-all" onclick="toggleTable(this)">See all {total_strats} strategies</button>' if total_strats > 20 else ''}
</div>

{"".join(all_sections)}

<div style="text-align:center;color:var(--muted);font-size:0.8rem;padding:2rem 0 1rem;">
  Generated by epic-creator decomposition pipeline
</div>

<a href="#" class="back-to-top" id="backToTop" title="Back to top">&#x25B2;</a>

<script>
mermaid.initialize({{ startOnLoad: true, theme: 'neutral', flowchart: {{ curve: 'basis', padding: 20 }} }});

{bodies_js}

document.addEventListener('DOMContentLoaded', function() {{
  document.querySelectorAll('[data-body]').forEach(function(el) {{
    const id = el.getAttribute('data-body');
    if (epicBodies[id]) {{
      el.innerHTML = DOMPurify.sanitize(marked.parse(epicBodies[id]));
    }}
  }});
}});

var btn = document.getElementById('backToTop');
window.addEventListener('scroll', function() {{
    btn.style.opacity = window.scrollY > 300 ? '1' : '0';
    btn.style.pointerEvents = window.scrollY > 300 ? 'auto' : 'none';
}});

function toggleTable(el) {{
    var section = el.closest('.table-section');
    section.querySelector('.table-wrapper').classList.remove('collapsed');
    section.querySelector('.table-fade').style.display = 'none';
    el.style.display = 'none';
}}
</script>
</body>
</html>'''

    return html


def main():
    parser = argparse.ArgumentParser(
        description="Generate HTML report for decomposition pipeline runs")
    parser.add_argument("--start-time", required=True,
                        help="Pipeline start timestamp (ISO 8601)")
    parser.add_argument("--output", default=None,
                        help="Output file path (default: artifacts/decompose-runs/{ts}-report.html)")
    parser.add_argument("ids", nargs="*",
                        help="Strategy IDs (default: scan decomposition files)")
    args = parser.parse_args()

    strat_ids = args.ids
    if not strat_ids:
        # Try pipeline IDs file first
        ids_file = "tmp/pipeline-all-ids.txt"
        if os.path.exists(ids_file):
            with open(ids_file) as f:
                strat_ids = [line.strip() for line in f if line.strip()]

    if not strat_ids:
        # Fall back to scanning decomposition files
        files = sorted(glob.glob("artifacts/epic-tasks/*-decomposition.md"))
        for f in files:
            sid = os.path.basename(f).replace("-decomposition.md", "")
            strat_ids.append(sid)

    if not strat_ids:
        print("No strategies found", file=sys.stderr)
        sys.exit(1)

    html = build_report(strat_ids, args.start_time)

    if args.output:
        out_path = args.output
    else:
        ts = args.start_time.replace(":", "-")
        out_path = f"artifacts/decompose-runs/{ts}-report.html"

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w") as f:
        f.write(html)

    print(out_path)


if __name__ == "__main__":
    main()

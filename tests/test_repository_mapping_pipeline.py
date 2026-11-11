import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import pipeline_state as pipeline
import check_decompose_progress as progress


@pytest.mark.parametrize('state', [
    {'phase': 'RE_REVIEW_CHECK'}, {'phase': 'REVISE_CHECK', 'revise_cycle': 2}])
def test_final_review_exits_map_before_batch_done(tmp_path, monkeypatch, state):
    monkeypatch.chdir(tmp_path)
    assert pipeline.advance(state, dry_run=True)[0] == 'MAP_REPOSITORIES'


def test_mapping_phase_returns_to_existing_batch_flow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert pipeline.advance({'phase': 'MAP_REPOSITORIES'}, dry_run=True)[0] == 'BATCH_DONE'


def test_mapping_completion_requires_current_inputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import repository_mapping as m
    epic = Path('artifacts/epic-tasks/RHAISTRAT-9001-E001.md')
    epic.parent.mkdir(parents=True)
    epic.write_text('---\nepic_id: RHAISTRAT-9001-E001\n---\nFinal draft')
    dest = Path('artifacts/repository-mappings/RHAISTRAT-9001.json')
    dest.parent.mkdir(parents=True)
    assert progress.check_id('map_repositories', 'RHAISTRAT-9001') == 'pending'
    dest.write_text(json.dumps({'strategy_id': 'RHAISTRAT-9001', 'complete': True,
        'input_digest': m.input_digest('RHAISTRAT-9001'), 'results': [{
            'epic_id': 'RHAISTRAT-9001-E001', 'status': 'unresolved_missing',
            'repository_url': None, 'reason': 'No context'}]}))
    assert progress.check_id('map_repositories', 'RHAISTRAT-9001') == 'completed'
    epic.write_text(epic.read_text() + '\nRevised scope')
    assert progress.check_id('map_repositories', 'RHAISTRAT-9001') == 'pending'

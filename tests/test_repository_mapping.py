"""Contract tests for isolated repository decisions, not model correctness."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


def result(status='resolved', url='https://github.com/example/product'):
    return {'epic_id': 'RHAISTRAT-9001-E001', 'status': status,
            'repository_url': url, 'reason': 'Changes the operator.',
            'evidence': [{'path': '.context/operator.md',
                          'excerpt': 'Repository: https://github.com/example/product'}]}


def document(row=None):
    return {'strategy_id': 'RHAISTRAT-9001', 'complete': True,
            'results': [row or result()]}


def write(tmp_path, value):
    p = tmp_path / 'mapping.json'
    p.write_text(json.dumps(value))
    return p


def test_mapping_contract_available():
    import repository_mapping as m
    assert callable(m.load_mapping)


def test_resolved_url_and_reason_roundtrip(tmp_path):
    import repository_mapping as m
    d = m.load_mapping(write(tmp_path, document()))
    assert m.repository_for(d, 'RHAISTRAT-9001-E001') == 'https://github.com/example/product'


@pytest.mark.parametrize('status', ['unresolved_missing', 'unresolved_ambiguous',
    'unresolved_conflicting', 'unresolved_cross_repository'])
def test_unresolved_is_complete_without_a_url(tmp_path, status):
    import repository_mapping as m
    p = write(tmp_path, document(result(status, None)))
    assert m.mapping_complete(p, {'RHAISTRAT-9001-E001'})
    assert m.repository_for(m.load_mapping(p), 'RHAISTRAT-9001-E001') is None


@pytest.mark.parametrize('status,url', [('resolved', None),
    ('unresolved_missing', 'https://github.com/example/product'),
    ('guessed', 'https://github.com/example/product'),
    ('resolved', 'https://github.com/example/product/tree/main'),
    ('resolved', 'https://secret@example.com/example/product'),
    ('resolved', 'https://github.com/example/product.git')])
def test_invalid_decisions_rejected(tmp_path, status, url):
    import repository_mapping as m
    with pytest.raises(ValueError):
        m.load_mapping(write(tmp_path, document(result(status, url))))


def test_missing_duplicate_or_extra_results_are_not_complete(tmp_path):
    import repository_mapping as m
    p = write(tmp_path, document())
    assert not m.mapping_complete(p, {'RHAISTRAT-9001-E001', 'RHAISTRAT-9001-E002'})
    assert not m.mapping_complete(p, set())
    d = document(); d['results'].append(result())
    assert not m.mapping_complete(write(tmp_path, d), {'RHAISTRAT-9001-E001'})


def test_incomplete_or_truncated_output_not_complete(tmp_path):
    import repository_mapping as m
    d = document(); d['complete'] = False
    assert not m.mapping_complete(write(tmp_path, d), {'RHAISTRAT-9001-E001'})
    p = write(tmp_path, d); p.write_text('{')
    assert not m.mapping_complete(p, {'RHAISTRAT-9001-E001'})

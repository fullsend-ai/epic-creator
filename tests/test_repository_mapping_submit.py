import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import jira_utils
import submit
from repository_mapping import input_digest
from test_submit import _setup_strategy


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _setup_strategy(strat_id='RHAISTRAT-9001', epic_count=1)
    p = Path('artifacts/repository-mappings/RHAISTRAT-9001.json')
    p.parent.mkdir(parents=True)
    def mapping(status, url):
        p.write_text(json.dumps({'strategy_id': 'RHAISTRAT-9001', 'complete': True,
            'input_digest': input_digest('RHAISTRAT-9001'), 'results': [{
                'epic_id': 'RHAISTRAT-9001-E001', 'status': status,
                'repository_url': url, 'reason': 'No supporting context' if url is None else 'Operator implementation',
                'evidence': [{'path': '.context/operator.md', 'excerpt': 'Operator target'}]}]}))
        return submit._build_plan(submit._scan_epics('artifacts', 'RHAISTRAT-9001'), None)
    return mapping


def test_resolved_mapping_carried_into_publication_plan(prepared):
    plan = prepared('resolved', 'https://github.com/example/product')
    assert plan[0]['repository_mapping']['repository_url'] == 'https://github.com/example/product'


def test_stale_mapping_cannot_publish(prepared):
    prepared('unresolved_missing', None)
    epic = Path('artifacts/epic-tasks/RHAISTRAT-9001-E001.md')
    epic.write_text(epic.read_text() + '\nChanged scope')
    with pytest.raises(ValueError, match='mapping'):
        submit._build_plan(submit._scan_epics('artifacts', 'RHAISTRAT-9001'), None)


def test_optional_repository_field_in_actual_create_payload():
    with patch.object(jira_utils, 'api_call_with_retry', return_value={'key': 'RHAI-9001'}) as api:
        jira_utils.create_issue('server', 'user', 'token', 'RHAI', 'Epic', 'Title', {}, 'Major',
            repository_url_field='customfield_10933', repository_url='https://github.com/example/product')
    assert api.call_args.kwargs['body']['fields']['customfield_10933'] == 'https://github.com/example/product'


def test_unresolved_epic_gets_explanatory_comment(prepared):
    plan = prepared('unresolved_missing', None)
    with patch.object(submit, 'create_issue', return_value='RHAI-9001') as create, \
         patch.object(submit, 'add_comment') as comment:
        ids, errors = submit._create_epics('server', 'user', 'token', plan, 'RHAISTRAT-9001')
    assert ids == {'RHAISTRAT-9001-E001': 'RHAI-9001'} and errors == 0
    assert 'repository_url' not in create.call_args.kwargs
    assert 'No supporting context' in json.dumps(comment.call_args.args)


def test_existing_epic_never_updated_or_commented(prepared):
    plan = prepared('unresolved_missing', None); plan[0]['jira_key'] = 'RHAI-9001'
    with patch.object(submit, 'create_issue') as create, patch.object(submit, 'add_comment') as comment:
        ids, errors = submit._create_epics('server', 'user', 'token', plan, 'RHAISTRAT-9001')
    create.assert_not_called(); comment.assert_not_called()
    assert errors == 0


def test_verified_url_field_is_project_and_screen_specific(monkeypatch):
    import repository_publication as p
    monkeypatch.setenv('EPIC_REPOSITORY_URL_FIELD', 'customfield_10933')
    responses = [
        {'values': [{'id': '10000', 'name': 'Epic'}], 'isLast': True},
        {'values': [{'fieldId': 'customfield_10933', 'schema': {'type': 'string',
            'custom': 'com.atlassian.jira.plugin.system.customfieldtypes:url'}}], 'isLast': True}]
    with patch.object(p, 'api_call', side_effect=responses) as api:
        assert p.verified_url_field('server', 'user', 'token', 'RHAI') == 'customfield_10933'
    assert '/RHAI/issuetypes/10000?' in api.call_args.args[1]


def test_wrong_field_type_is_not_published(monkeypatch):
    import repository_publication as p
    monkeypatch.setenv('EPIC_REPOSITORY_URL_FIELD', 'customfield_10933')
    responses = [
        {'values': [{'id': '10000', 'name': 'Epic'}]},
        {'values': [{'fieldId': 'customfield_10933', 'schema': {'type': 'string',
            'custom': 'com.atlassian.jira.plugin.system.customfieldtypes:textfield'}}]}]
    with patch.object(p, 'api_call', side_effect=responses):
        assert p.verified_url_field('server', 'user', 'token', 'RHAI') is None


def test_resolved_mapping_sets_url_without_warning(prepared):
    plan = prepared('resolved', 'https://github.com/example/product')
    with patch('repository_publication.verified_url_field', return_value='customfield_10933'), \
         patch.object(submit, 'create_issue', return_value='RHAI-9001') as create, \
         patch.object(submit, 'add_comment') as comment:
        _, errors = submit._create_epics('server', 'user', 'token', plan, 'RHAISTRAT-9001')
    assert errors == 0
    assert create.call_args.kwargs['repository_url'] == 'https://github.com/example/product'
    comment.assert_not_called()


def test_unavailable_field_keeps_epic_and_adds_comment(prepared):
    plan = prepared('resolved', 'https://github.com/example/product')
    with patch('repository_publication.verified_url_field', return_value=None), \
         patch.object(submit, 'create_issue', return_value='RHAI-9001') as create, \
         patch.object(submit, 'add_comment') as comment:
        _, errors = submit._create_epics('server', 'user', 'token', plan, 'RHAISTRAT-9001')
    assert errors == 0
    assert 'repository_url' not in create.call_args.kwargs
    assert 'unavailable' in json.dumps(comment.call_args.args)


def test_warning_failure_is_retried_without_duplicate_epic(prepared):
    plan = prepared('unresolved_missing', None)
    with patch.object(submit, 'create_issue', return_value='RHAI-9001'), \
         patch.object(submit, 'add_comment', side_effect=OSError('temporary')):
        _, errors = submit._create_epics('server', 'user', 'token', plan, 'RHAISTRAT-9001')
    assert errors == 1
    resumed = submit._build_plan(submit._scan_epics('artifacts', 'RHAISTRAT-9001'), None)
    with patch.object(submit, 'create_issue') as create, \
         patch.object(submit, 'get_comments', return_value=[]), \
         patch.object(submit, 'add_comment') as comment:
        _, errors = submit._create_epics('server', 'user', 'token', resumed, 'RHAISTRAT-9001')
    create.assert_not_called()
    comment.assert_called_once()
    assert errors == 0


def test_missing_mapping_rejected_for_integrated_pipeline(prepared):
    prepared('unresolved_missing', None)
    from artifact_utils import update_frontmatter
    update_frontmatter('artifacts/epic-tasks/RHAISTRAT-9001-decomposition.md',
                       {'repository_mapping_required': True}, 'decomp-summary')
    Path('artifacts/repository-mappings/RHAISTRAT-9001.json').unlink()
    with pytest.raises(ValueError, match='mapping'):
        submit._build_plan(submit._scan_epics('artifacts', 'RHAISTRAT-9001'), None)


def test_mapping_digest_independent_of_artifacts_path_spelling(prepared):
    prepared('unresolved_missing', None)
    assert input_digest('RHAISTRAT-9001') == input_digest('RHAISTRAT-9001', Path('artifacts').resolve())


def test_pending_warning_not_duplicated_after_comment_succeeded(prepared):
    plan = prepared('unresolved_missing', None)
    message = 'The target repository is unresolved.'
    from artifact_utils import update_frontmatter
    update_frontmatter(plan[0]['path'], {'jira_key': 'RHAI-9001', 'repository_warning_pending': message}, 'epic-task')
    resumed = submit._build_plan(submit._scan_epics('artifacts', 'RHAISTRAT-9001'), None)
    with patch.object(submit, 'create_issue') as create, \
         patch.object(submit, 'get_comments', return_value=[{'body': submit.markdown_to_adf(message)}]), \
         patch.object(submit, 'add_comment') as comment:
        _, errors = submit._create_epics('server', 'user', 'token', resumed, 'RHAISTRAT-9001')
    create.assert_not_called(); comment.assert_not_called()
    assert errors == 0


def test_pending_warning_preserves_manually_filled_url(prepared, monkeypatch):
    plan = prepared('unresolved_missing', None)
    from artifact_utils import update_frontmatter
    update_frontmatter(plan[0]['path'], {'jira_key': 'RHAI-9001', 'repository_warning_pending': 'Missing repository'}, 'epic-task')
    monkeypatch.setenv('EPIC_REPOSITORY_URL_FIELD', 'customfield_10933')
    resumed = submit._build_plan(submit._scan_epics('artifacts', 'RHAISTRAT-9001'), None)
    with patch.object(submit, 'create_issue') as create, \
         patch.object(submit, 'get_issue', return_value={'fields': {'customfield_10933': 'https://github.com/example/manual'}}), \
         patch.object(submit, 'get_comments') as comments, patch.object(submit, 'add_comment') as comment:
        _, errors = submit._create_epics('server', 'user', 'token', resumed, 'RHAISTRAT-9001')
    create.assert_not_called(); comments.assert_not_called(); comment.assert_not_called()
    assert errors == 0

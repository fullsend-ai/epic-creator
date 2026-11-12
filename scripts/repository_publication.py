"""Read-only verification of the configured RHAI Epic URL field."""
import os
import re
import sys

from jira_utils import api_call, safe_error_message


def _pages(server, user, token, path):
    start = 0
    while True:
        page = api_call(server, f'{path}?startAt={start}&maxResults=100', user, token)
        values = page.get('values', [])
        yield from values
        if page.get('isLast', True) or not values:
            break
        start += len(values)


def verified_url_field(server, user, token, project):
    field = os.environ.get('EPIC_REPOSITORY_URL_FIELD', '')
    if not re.fullmatch(r'customfield_\d+', field):
        print('  WARNING: Configure EPIC_REPOSITORY_URL_FIELD for repository URLs', file=sys.stderr)
        return None
    try:
        base = f'/issue/createmeta/{project}/issuetypes'
        issue_type = next((x for x in _pages(server, user, token, base)
                           if x.get('name') == 'Epic'), None)
        if issue_type:
            for item in _pages(server, user, token, f"{base}/{issue_type['id']}"):
                schema = item.get('schema', {})
                if (item.get('fieldId') == field and schema.get('type') == 'string'
                        and schema.get('custom') == 'com.atlassian.jira.plugin.system.customfieldtypes:url'):
                    return field
    except Exception as error:
        print(f'  WARNING: Cannot verify repository URL field: {safe_error_message(error)}', file=sys.stderr)
    print('  WARNING: Repository URL field is unavailable on the Epic create screen', file=sys.stderr)
    return None

"""Read the isolated mapper's decision artifact; no model or Jira access."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

STATUSES = {'resolved', 'unresolved_missing', 'unresolved_ambiguous',
            'unresolved_conflicting', 'unresolved_cross_repository'}


def load_mapping(path: Path) -> dict:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or data.get('complete') is not True:
        raise ValueError('Repository mapping is incomplete')
    strategy = data.get('strategy_id', '')
    if not isinstance(strategy, str) or not re.fullmatch(r'RHAISTRAT-\d+', strategy):
        raise ValueError('Invalid mapping strategy')
    rows = data.get('results')
    if not isinstance(rows, list):
        raise ValueError('Missing mapping results')
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Invalid repository result')
        epic = row.get('epic_id', '')
        if not isinstance(epic, str) or not re.fullmatch(re.escape(strategy) + r'-E\d{3}', epic) or epic in seen:
            raise ValueError('Invalid or duplicate mapped Epic')
        seen.add(epic)
        status, url = row.get('status'), row.get('repository_url')
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError('Invalid repository status')
        reason = row.get('reason')
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Missing repository rationale')
        if status != 'resolved':
            if url is not None:
                raise ValueError('Unresolved mapping must omit URL')
            continue
        if not isinstance(url, str):
            raise ValueError('Resolved mapping requires URL')
        parsed = urlsplit(url)
        parts = parsed.path.strip('/').split('/')
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.port or parsed.query or parsed.fragment
                or url.endswith(('/', '.git')) or len(parts) < 2
                or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', p) or p in {'.', '..'} for p in parts)
                or (parsed.hostname.lower() == 'github.com' and len(parts) != 2)):
            raise ValueError('Expected canonical HTTPS repository URL')
        evidence = row.get('evidence')
        if not isinstance(evidence, list) or not evidence or any(
                not isinstance(e, dict) or not isinstance(e.get('path'), str)
                or not e['path'].strip() or not isinstance(e.get('excerpt'), str)
                or not e['excerpt'].strip() for e in evidence):
            raise ValueError('Resolved mapping requires supporting evidence')
    return data


def mapping_complete(path: Path, epic_ids: set[str]) -> bool:
    try:
        data = load_mapping(path)
        return {row['epic_id'] for row in data['results']} == epic_ids
    except (OSError, ValueError, TypeError):
        return False


def repository_for(mapping: dict, epic_id: str) -> str | None:
    return next((row['repository_url'] for row in mapping['results']
                 if row['epic_id'] == epic_id), None)


def epic_paths(strategy_id: str, artifacts_dir: Path = Path('artifacts')) -> list[Path]:
    return sorted((Path(artifacts_dir) / 'epic-tasks').glob(f'{strategy_id}-E[0-9][0-9][0-9].md'))


def input_digest(strategy_id: str, artifacts_dir: Path = Path('artifacts')) -> str:
    """Bind the result to final local drafts, strategy and architecture inputs."""
    import hashlib
    from artifact_utils import read_frontmatter
    paths = epic_paths(strategy_id, artifacts_dir)
    strategy = Path(artifacts_dir) / 'strat-tasks' / f'{strategy_id}.md'
    if strategy.exists():
        paths.append(strategy)
    paths.extend(sorted(Path('.context/architecture-context').rglob('*.md')))
    parts = []
    for path in paths:
        if path in epic_paths(strategy_id, artifacts_dir):
            data, body = read_frontmatter(str(path))
            data = dict(data or {})
            data.pop('jira_key', None)
            data.pop('repository_warning_pending', None)
            content = json.dumps(data, sort_keys=True, default=str) + body
        else:
            content = path.read_text()
        if path in epic_paths(strategy_id, artifacts_dir) or path == strategy:
            label = 'artifacts/' + str(path.relative_to(artifacts_dir))
        else:
            label = str(path)
        parts.append((label, content))
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Describe final RepoMapper inputs')
    parser.add_argument('strategy_id')
    args = parser.parse_args()
    if not re.fullmatch(r'RHAISTRAT-\d+', args.strategy_id):
        parser.error('Invalid strategy ID')
    print(json.dumps({'strategy_id': args.strategy_id,
        'input_digest': input_digest(args.strategy_id),
        'epics': [str(p) for p in epic_paths(args.strategy_id)]}, indent=2))

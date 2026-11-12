"""Verify the locked dashboard inputs plus the exact six conversation changes."""

import importlib.util
import json
import sys
from pathlib import Path


BASE = '869a74bde961e30a4dc56c0bdc71d82cde62e2e2'
BASE_TREE = 'cabd6e37ef02617216b5f3d8b85c3b63c6a3d8ad'
HEAD = 'fe32e0749ee6af50a39e971058e2cba55f296152'
TREE = '877a62d0e0ce90ea5138ad3cca50dfbafb0d6107'
CHANGES = {
    'apps/dashboard/src/api/serviceConversation.ts': 'A',
    'apps/dashboard/src/components/network/ServiceConversationPanel.tsx': 'A',
    'apps/dashboard/src/components/network/ServiceRequestDetail.tsx': 'M',
    'apps/dashboard/src/locale/en/property_network.json': 'M',
    'apps/dashboard/src/locale/es/property_network.json': 'M',
    'tests/service-conversation.test.cjs': 'A',
}
METADATA = ('mode', 'git_blob_oid', 'bytes', 'sha256', 'binary_nul_detected')


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    mode = sys.argv[1]
    assert mode in {'apply', 'verify'}
    ops, relay, frontend, scratch = map(Path, sys.argv[2:])
    host = load(relay / 'ci/frontend/frontend_ci_host.py', 'accepted_frontend_host')
    visits = load(ops / 'reconstruct-service-visits-frontend.py', 'accepted_service_visits_host')
    first = json.loads((relay / 'ci/frontend/frontend-overlay.json').read_bytes())['application_files']
    visit_delta = json.loads((ops / 'frontend-service-visits-delta.json').read_bytes())
    assert visit_delta['frontend_commit'] == BASE and visit_delta['frontend_tree'] == BASE_TREE
    base = first | {name: {key: row[key] for key in METADATA} for name, row in visit_delta['files'].items()}
    assert len(base) == 436

    delta = json.loads((ops / 'frontend-service-conversation-delta.json').read_bytes())
    assert (delta['parent_commit'], delta['parent_tree'], delta['frontend_commit'], delta['frontend_tree']) == (BASE, BASE_TREE, HEAD, TREE)
    assert {name: row['change'] for name, row in delta['files'].items()} == CHANGES
    expected = base.copy()
    workspace = scratch / 'frontend-work'
    env = {'GIT_INDEX_FILE': str(scratch / 'frontend-index')}
    if mode == 'apply':
        visits.verify_workspace(host, workspace, base)
        assert host.git(frontend, 'write-tree', env=env).decode().strip() == BASE_TREE

    for name, row in delta['files'].items():
        assert (name in base) == (CHANGES[name] == 'M')
        body = host.regular(ops, f'conversation-overlay/{name}').read_bytes()
        metadata = {key: row[key] for key in METADATA}
        assert metadata == {'mode': '100644', 'git_blob_oid': host.blob(body), 'bytes': len(body),
                            'sha256': host.sha(body), 'binary_nul_detected': False} and b'\0' not in body
        assert metadata != base.get(name)
        expected[name] = metadata
        if mode == 'apply':
            oid = host.git(frontend, 'hash-object', '-w', '--stdin', data=body).decode().strip()
            assert oid == metadata['git_blob_oid']
            host.git(frontend, 'update-index', '--add', '--cacheinfo', '100644', oid, name, env=env)
            path = workspace / name
            assert path.parent.resolve() == path.parent and not path.is_symlink()
            with path.open('xb' if CHANGES[name] == 'A' else 'wb') as out:
                out.write(body)

    tree = host.git(frontend, 'write-tree', env=env).decode().strip()
    assert tree == TREE and len(expected) == 439
    assert set(host.git(frontend, 'diff', '--name-status', BASE_TREE, tree).decode().splitlines()) == {
        f'{action}\t{name}' for name, action in CHANGES.items()
    }
    host.verify_bodies(frontend, tree, expected)
    visits.verify_workspace(host, workspace, expected)
    print(f'SERVICE_CONVERSATION_FRONTEND_{mode.upper()} commit={HEAD} tree={tree} source_paths=439')


if __name__ == '__main__':
    main()

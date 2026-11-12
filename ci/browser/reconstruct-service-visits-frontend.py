"""Layer the exact service-visit frontend changes on the accepted rehearsal source."""

import importlib.util
import json
import sys
from pathlib import Path


PARENT = 'd967a3714b40b6e98ad2654c3ce865ad5d152a20'
PARENT_TREE = 'ef36dc3083673e3daad8f73401ea9d00a87c32b3'
CANDIDATE = '869a74bde961e30a4dc56c0bdc71d82cde62e2e2'
CANDIDATE_TREE = 'cabd6e37ef02617216b5f3d8b85c3b63c6a3d8ad'
CHANGES = {
    'apps/dashboard/src/api/serviceWork.ts': 'A',
    'apps/dashboard/src/components/network/ServiceRequestDetail.tsx': 'M',
    'apps/dashboard/src/components/network/ServiceWorkPanel.tsx': 'A',
    'apps/dashboard/src/components/network/ServiceWorkflowShared.tsx': 'M',
    'apps/dashboard/src/locale/en/property_network.json': 'M',
    'apps/dashboard/src/locale/es/property_network.json': 'M',
}


def verify_workspace(host, workspace, files):
    for name, entry in files.items():
        body = host.regular(workspace, name).read_bytes()
        assert len(body) == entry['bytes'] and host.sha(body) == entry['sha256']


def main():
    mode = sys.argv[1]
    assert mode in {'base', 'apply', 'verify'}
    ops, relay, frontend, scratch = map(Path, sys.argv[2:])
    workspace = scratch / 'frontend-work'
    spec = importlib.util.spec_from_file_location(
        'accepted_frontend_host', relay / 'ci/frontend/frontend_ci_host.py'
    )
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    manifest = json.loads((relay / 'ci/frontend/frontend-overlay.json').read_bytes())
    if mode == 'base':
        assert host.reconstruct(relay, frontend, manifest, scratch) == PARENT_TREE
        return

    delta = json.loads((ops / 'frontend-service-visits-delta.json').read_bytes())
    assert (delta['parent_commit'], delta['parent_tree'], delta['frontend_commit'], delta['frontend_tree']) == (
        PARENT, PARENT_TREE, CANDIDATE, CANDIDATE_TREE
    )
    assert {name: value['change'] for name, value in delta['files'].items()} == CHANGES
    previous = manifest['application_files']
    assert len(previous) == 434
    expected = previous.copy()
    bodies = {}
    for name, entry in delta['files'].items():
        assert (name in previous) == (CHANGES[name] == 'M')
        body = host.regular(ops, f'frontend-overlay/{name}').read_bytes()
        metadata = {key: entry[key] for key in (
            'mode', 'git_blob_oid', 'bytes', 'sha256', 'binary_nul_detected'
        )}
        assert metadata == {
            'mode': '100644', 'git_blob_oid': host.blob(body), 'bytes': len(body),
            'sha256': host.sha(body), 'binary_nul_detected': False,
        } and b'\0' not in body
        assert metadata != previous.get(name)
        expected[name] = metadata
        bodies[name] = body

    assert len(expected) == 436
    index = scratch / 'frontend-index'
    env = {'GIT_INDEX_FILE': str(index)}
    if mode == 'apply':
        verify_workspace(host, workspace, previous)
        assert host.git(frontend, 'write-tree', env=env).decode().strip() == PARENT_TREE
        for name, body in bodies.items():
            oid = host.git(frontend, 'hash-object', '-w', '--stdin', data=body).decode().strip()
            assert oid == expected[name]['git_blob_oid']
            host.git(frontend, 'update-index', '--add', '--cacheinfo', '100644', oid, name, env=env)
            path = workspace / name
            assert path.parent.resolve() == path.parent and not path.is_symlink()
            with path.open('xb' if CHANGES[name] == 'A' else 'wb') as out:
                out.write(body)

    tree = host.git(frontend, 'write-tree', env=env).decode().strip()
    assert tree == CANDIDATE_TREE
    assert set(host.git(frontend, 'diff', '--name-status', PARENT_TREE, tree).decode().splitlines()) == {
        f'{action}\t{name}' for name, action in CHANGES.items()
    }
    host.verify_bodies(frontend, tree, expected)
    verify_workspace(host, workspace, expected)
    print(f'SERVICE_VISITS_FRONTEND_{mode.upper()} commit={CANDIDATE} tree={tree} source_paths=436')


if __name__ == '__main__':
    main()

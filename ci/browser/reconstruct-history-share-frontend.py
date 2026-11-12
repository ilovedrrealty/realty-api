"""Apply or verify the exact history-sharing frontend above the integrated product."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path


BASE = 'ee1596b4e9b9902f7bbcbd91fb6b072b2577601d'
BASE_TREE = '3ee0bedeaabb0a2f01e087d45be0d82c245282b6'
HEAD = '3e2afea7ea533dc53813279fefbe140ce077ff20'
TREE = '62555da3ff085109105f7224ed1c1f4b5ca743c3'
INTEGRATED_SCRIPT_SHA256 = '55af4bba54a056a885678ccf6959d255e967355ab4519b5fa6b4a0e89fbe8c05'
HISTORY_SHARE_DELTA_SHA256 = '70ed638f031b93a2e75e0ac4f21b74752aa7c39e472151430e15fc8d7dec26f4'
CHANGES = {
    'apps/dashboard/src/api/historyShares.ts': 'A',
    'apps/dashboard/src/components/network/HistoryShareCreate.tsx': 'A',
    'apps/dashboard/src/components/network/HistorySharing.tsx': 'A',
    'apps/dashboard/src/components/network/ProjectWorkspace.tsx': 'M',
    'apps/dashboard/src/components/network/PropertyRegistry.tsx': 'M',
    'apps/dashboard/src/locale/en/property_network.json': 'M',
    'apps/dashboard/src/locale/es/property_network.json': 'M',
    'apps/dashboard/src/pages/shared/PropertyNetwork.tsx': 'M',
    'tests/due-work-reminders.test.cjs': 'M',
    'tests/history-shares.test.cjs': 'A',
    'tests/property-network-ui.test.cjs': 'M',
}


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def load_integrated(ops):
    path = ops / 'reconstruct-integrated-frontend.py'
    require(path.is_file() and path.resolve() == path, 'CANONICAL_INTEGRATED_RECONSTRUCTOR_REQUIRED')
    require(hashlib.sha256(path.read_bytes()).hexdigest() == INTEGRATED_SCRIPT_SHA256,
            'PINNED_INTEGRATED_RECONSTRUCTOR_MISMATCH')
    spec = importlib.util.spec_from_file_location('accepted_integrated_frontend', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require((module.HEAD, module.TREE) == (BASE, BASE_TREE), 'ACCEPTED_INTEGRATED_FRONTEND_REQUIRED')
    return module


def history_share_files(due_work, host, ops, base):
    delta = json.loads(due_work.pinned_bytes(ops / 'frontend-history-share-delta.json', HISTORY_SHARE_DELTA_SHA256))
    require(set(delta) == {'parent_commit', 'parent_tree', 'frontend_commit', 'frontend_tree', 'files'}
            and due_work.source_pins(delta) == ((BASE, BASE_TREE), (HEAD, TREE)),
            'EXACT_HISTORY_SHARE_FRONTEND_REQUIRED')
    require({name: row['change'] for name, row in delta['files'].items()} == CHANGES,
            'EXACT_11_HISTORY_SHARE_PATHS_REQUIRED')
    expected = base.copy()
    bodies = {}
    for name, row in delta['files'].items():
        require(set(row) == {'change', *due_work.METADATA}
                and (name in base) == (CHANGES[name] == 'M'), 'HISTORY_SHARE_PATH_CLASS_REQUIRED')
        body = host.regular(ops, f'history-share-overlay/{name}').read_bytes()
        metadata = {key: row[key] for key in due_work.METADATA}
        require(metadata == {'mode': '100644', 'git_blob_oid': host.blob(body), 'bytes': len(body),
                             'sha256': host.sha(body), 'binary_nul_detected': False}
                and b'\0' not in body and metadata != base.get(name), 'HISTORY_SHARE_PAYLOAD_MISMATCH')
        expected[name] = metadata
        bodies[name] = body
    require(len(base) == 465 and len(expected) == 469, 'EXACT_469_FRONTEND_PATHS_REQUIRED')
    return expected, bodies


def apply_history_share(due_work, host, frontend, workspace, base, expected, bodies, env):
    require(host.git(frontend, 'write-tree', env=env).decode().strip() == BASE_TREE,
            'EXACT_INTEGRATED_INDEX_REQUIRED')
    due_work.verify_workspace(host, workspace, base)
    for name, body in bodies.items():
        path = workspace / name
        require(path.parent.is_dir() and path.parent.resolve() == path.parent and not path.is_symlink()
                and (path.is_file() if CHANGES[name] == 'M' else not path.exists()),
                'HISTORY_SHARE_WORKSPACE_TARGET_REQUIRED')
        oid = host.git(frontend, 'hash-object', '-w', '--stdin', data=body).decode().strip()
        require(oid == expected[name]['git_blob_oid'], 'STORED_HISTORY_SHARE_OID_MISMATCH')
        host.git(frontend, 'update-index', '--add', '--cacheinfo', '100644', oid, name, env=env)
        with path.open('xb' if CHANGES[name] == 'A' else 'wb') as out:
            out.write(body)


def main():
    require(len(sys.argv) == 6 and sys.argv[1] in {'apply', 'verify'},
            'HISTORY_SHARE_MODE_AND_FOUR_PATHS_REQUIRED')
    mode = sys.argv[1]
    ops, relay, frontend, scratch = map(Path, sys.argv[2:])
    workspace = scratch / 'frontend-work'
    index = scratch / 'frontend-index'
    require(all(path.is_dir() and path.resolve() == path for path in (ops, relay, frontend, scratch, workspace))
            and index.is_file() and index.resolve() == index, 'CANONICAL_FRONTEND_WORKSPACE_REQUIRED')
    integrated = load_integrated(ops)
    room_tour = integrated.load_room_tour(ops)
    due_work = room_tour.load_due_work(ops)
    host = due_work.load_host(relay)
    require(tuple(host.git(frontend, 'rev-parse', 'HEAD', 'HEAD^{tree}').decode().splitlines()) ==
            (due_work.PUBLIC_BASE, due_work.PUBLIC_BASE_TREE), 'EXACT_FRONTEND_CHECKOUT_REQUIRED')
    prior = due_work.prior_files(ops, relay)
    reminders, _ = due_work.due_work_files(host, ops, prior)
    room, _ = room_tour.room_tour_files(due_work, host, ops, reminders)
    base, _ = integrated.integrated_files(due_work, host, ops, room)
    expected, bodies = history_share_files(due_work, host, ops, base)
    env = {'GIT_INDEX_FILE': str(index)}
    host.verify_bodies(frontend, BASE_TREE, base)
    if mode == 'apply':
        apply_history_share(due_work, host, frontend, workspace, base, expected, bodies, env)

    tree = host.git(frontend, 'write-tree', env=env).decode().strip()
    require(tree == TREE, 'EXACT_HISTORY_SHARE_FRONTEND_TREE_REQUIRED')
    changes = host.git(frontend, 'diff', '--name-status', '--no-renames', BASE_TREE, tree).decode().splitlines()
    require(len(changes) == len(CHANGES) and set(changes) == {f'{action}\t{name}' for name, action in CHANGES.items()},
            'ONLY_EXACT_11_HISTORY_SHARE_CHANGES_ALLOWED')
    host.verify_bodies(frontend, tree, expected)
    due_work.verify_workspace(host, workspace, expected)
    print(f'HISTORY_SHARE_FRONTEND_{mode.upper()} commit={HEAD} tree={tree} source_paths={len(expected)}')


if __name__ == '__main__':
    main()

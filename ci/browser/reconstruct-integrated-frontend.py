"""Reconstruct the exact room-tour, short-gallery and Service Operations frontend."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path


BASE = 'ae824e1439557238c17abaf29dfc8b859086e987'
BASE_TREE = '88475e96ca4f523c5731fb210b0e8ad20e886f98'
HEAD = 'ee1596b4e9b9902f7bbcbd91fb6b072b2577601d'
TREE = '3ee0bedeaabb0a2f01e087d45be0d82c245282b6'
ROOM_TOUR_SCRIPT_SHA256 = '0375a645b7a9f4ef083fce825ff908fea2fbd810674a79349ee6bc3a278ce418'
INTEGRATED_DELTA_SHA256 = '4d55e2b42ac280576aac012bdd9a8da5991d1944a09777e4097d7232bea843f4'
CHANGES = {
    'apps/dashboard/src/api/serviceOperations.ts': 'A',
    'apps/dashboard/src/components/network/NetworkShared.tsx': 'M',
    'apps/dashboard/src/components/network/ServiceOperations.tsx': 'A',
    'apps/dashboard/src/components/network/ServiceRequestDetail.tsx': 'M',
    'apps/dashboard/src/components/network/ServiceWorkflow.tsx': 'M',
    'apps/dashboard/src/locale/en/property_network.json': 'M',
    'apps/dashboard/src/locale/es/property_network.json': 'M',
    'apps/landing/src/data/propertyDetailData.ts': 'M',
    'apps/landing/src/locales/en/property_detail.json': 'M',
    'apps/landing/src/locales/es/property_detail.json': 'M',
    'apps/landing/src/views/PropertyDetail.tsx': 'M',
    'tests/listing-short-gallery.test.cjs': 'A',
    'tests/service-favorites.test.cjs': 'M',
    'tests/service-operations.test.cjs': 'A',
}


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def load_room_tour(ops):
    path = ops / 'reconstruct-listing-room-tour-frontend.py'
    require(path.is_file() and path.resolve() == path, 'CANONICAL_ROOM_TOUR_RECONSTRUCTOR_REQUIRED')
    require(hashlib.sha256(path.read_bytes()).hexdigest() == ROOM_TOUR_SCRIPT_SHA256, 'PINNED_ROOM_TOUR_RECONSTRUCTOR_MISMATCH')
    spec = importlib.util.spec_from_file_location('accepted_room_tour_frontend', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require((module.HEAD, module.TREE) == (BASE, BASE_TREE), 'ACCEPTED_ROOM_TOUR_FRONTEND_REQUIRED')
    return module


def integrated_files(due_work, host, ops, base):
    delta = json.loads(due_work.pinned_bytes(ops / 'frontend-integrated-delta.json', INTEGRATED_DELTA_SHA256))
    require(set(delta) == {'parent_commit', 'parent_tree', 'frontend_commit', 'frontend_tree', 'files'}
            and due_work.source_pins(delta) == ((BASE, BASE_TREE), (HEAD, TREE)), 'EXACT_INTEGRATED_FRONTEND_REQUIRED')
    require({name: row['change'] for name, row in delta['files'].items()} == CHANGES, 'EXACT_14_INTEGRATED_PATHS_REQUIRED')
    expected = base.copy()
    bodies = {}
    for name, row in delta['files'].items():
        require(set(row) == {'change', *due_work.METADATA}
                and (name in base) == (CHANGES[name] == 'M'), 'INTEGRATED_PATH_CLASS_REQUIRED')
        body = host.regular(ops, f'integrated-overlay/{name}').read_bytes()
        metadata = {key: row[key] for key in due_work.METADATA}
        require(metadata == {'mode': '100644', 'git_blob_oid': host.blob(body), 'bytes': len(body),
                             'sha256': host.sha(body), 'binary_nul_detected': False}
                and b'\0' not in body and metadata != base.get(name), 'INTEGRATED_PAYLOAD_MISMATCH')
        expected[name] = metadata
        bodies[name] = body
    require(len(base) == 461 and len(expected) == 465, 'EXACT_465_FRONTEND_PATHS_REQUIRED')
    return expected, bodies


def apply_integrated(due_work, host, frontend, workspace, base, expected, bodies, env):
    require(host.git(frontend, 'write-tree', env=env).decode().strip() == BASE_TREE, 'EXACT_ROOM_TOUR_INDEX_REQUIRED')
    due_work.verify_workspace(host, workspace, base)
    for name, body in bodies.items():
        path = workspace / name
        require(path.parent.is_dir() and path.parent.resolve() == path.parent and not path.is_symlink()
                and (path.is_file() if CHANGES[name] == 'M' else not path.exists()), 'INTEGRATED_WORKSPACE_TARGET_REQUIRED')
        oid = host.git(frontend, 'hash-object', '-w', '--stdin', data=body).decode().strip()
        require(oid == expected[name]['git_blob_oid'], 'STORED_INTEGRATED_OID_MISMATCH')
        host.git(frontend, 'update-index', '--add', '--cacheinfo', '100644', oid, name, env=env)
        with path.open('xb' if CHANGES[name] == 'A' else 'wb') as out:
            out.write(body)


def main():
    require(len(sys.argv) == 6 and sys.argv[1] in {'apply', 'verify'}, 'INTEGRATED_MODE_AND_FOUR_PATHS_REQUIRED')
    mode = sys.argv[1]
    ops, relay, frontend, scratch = map(Path, sys.argv[2:])
    workspace = scratch / 'frontend-work'
    index = scratch / 'frontend-index'
    require(all(path.is_dir() and path.resolve() == path for path in (ops, relay, frontend, scratch, workspace))
            and index.is_file() and index.resolve() == index, 'CANONICAL_FRONTEND_WORKSPACE_REQUIRED')
    room_tour = load_room_tour(ops)
    due_work = room_tour.load_due_work(ops)
    host = due_work.load_host(relay)
    require(tuple(host.git(frontend, 'rev-parse', 'HEAD', 'HEAD^{tree}').decode().splitlines()) ==
            (due_work.PUBLIC_BASE, due_work.PUBLIC_BASE_TREE), 'EXACT_FRONTEND_CHECKOUT_REQUIRED')
    prior = due_work.prior_files(ops, relay)
    reminders, _ = due_work.due_work_files(host, ops, prior)
    base, _ = room_tour.room_tour_files(due_work, host, ops, reminders)
    expected, bodies = integrated_files(due_work, host, ops, base)
    env = {'GIT_INDEX_FILE': str(index)}
    host.verify_bodies(frontend, BASE_TREE, base)
    if mode == 'apply':
        apply_integrated(due_work, host, frontend, workspace, base, expected, bodies, env)

    tree = host.git(frontend, 'write-tree', env=env).decode().strip()
    require(tree == TREE, 'EXACT_INTEGRATED_FRONTEND_TREE_REQUIRED')
    changes = host.git(frontend, 'diff', '--name-status', '--no-renames', BASE_TREE, tree).decode().splitlines()
    require(len(changes) == len(CHANGES) and set(changes) == {f'{action}\t{name}' for name, action in CHANGES.items()},
            'ONLY_EXACT_14_INTEGRATED_CHANGES_ALLOWED')
    host.verify_bodies(frontend, tree, expected)
    due_work.verify_workspace(host, workspace, expected)
    print(f'INTEGRATED_PROPERTY_SERVICES_FRONTEND_{mode.upper()} commit={HEAD} tree={tree} source_paths={len(expected)}')


if __name__ == '__main__':
    main()

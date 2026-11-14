"""Apply and verify the exact custody UI above the accepted history frontend."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

BASE = '3e2afea7ea533dc53813279fefbe140ce077ff20'
BASE_TREE = '62555da3ff085109105f7224ed1c1f4b5ca743c3'
HEAD = '047d5fbf6f78993b0404992ef666cd419001c65d'
TREE = 'a0429808349e5221b7b4103cb1268ff9c7f67250'
HISTORY_SHA = 'c1010d910efcd35f525cf2255fa96cbcf610c0a49214330f39d9539a11660214'
CUSTODY_DELTA_SHA = 'fb2734ea3bb7a85b37efc3abd5c6b91d081ce79464a3a982cd2f4c8908907679'
CHANGES = {
    'apps/dashboard/src/api/registryCustody.ts': 'A',
    'apps/dashboard/src/components/network/CustodyTransferOffer.tsx': 'A',
    'apps/dashboard/src/components/network/CustodyTransfers.tsx': 'A',
    'apps/dashboard/src/components/network/PropertyRegistry.tsx': 'M',
    'apps/dashboard/src/components/network/ServiceConversationPanel.tsx': 'M',
    'apps/dashboard/src/components/network/ServiceRequestDetail.tsx': 'M',
    'apps/dashboard/src/locale/en/property_network.json': 'M',
    'apps/dashboard/src/locale/es/property_network.json': 'M',
    'apps/dashboard/src/pages/shared/PropertyNetwork.tsx': 'M',
    'tests/property-network-ui.test.cjs': 'M',
    'tests/registry-custody.test.cjs': 'A',
}


def load_history(ops):
    path = ops / 'reconstruct-history-share-frontend.py'
    if not path.is_file() or path.resolve() != path or hashlib.sha256(path.read_bytes()).hexdigest() != HISTORY_SHA:
        raise RuntimeError('EXACT_HISTORY_RECONSTRUCTOR_REQUIRED')
    spec = importlib.util.spec_from_file_location('accepted_history_frontend', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.require((module.HEAD, module.TREE) == (BASE, BASE_TREE), 'ACCEPTED_HISTORY_FRONTEND_REQUIRED')
    return module


def main():
    if len(sys.argv) != 6 or sys.argv[1] not in {'apply', 'verify'}:
        raise RuntimeError('CUSTODY_MODE_AND_FOUR_PATHS_REQUIRED')
    mode = sys.argv[1]
    ops, relay, frontend, scratch = map(Path, sys.argv[2:])
    workspace = scratch / 'frontend-work'
    index = scratch / 'frontend-index'
    history = load_history(ops)
    check = history.require
    check(all(path.is_dir() and path.resolve() == path for path in (ops, relay, frontend, scratch, workspace))
          and index.is_file() and index.resolve() == index, 'CANONICAL_FRONTEND_WORKSPACE_REQUIRED')
    integrated = history.load_integrated(ops)
    room_tour = integrated.load_room_tour(ops)
    due_work = room_tour.load_due_work(ops)
    host = due_work.load_host(relay)
    check(tuple(host.git(frontend, 'rev-parse', 'HEAD', 'HEAD^{tree}').decode().splitlines()) ==
          (due_work.PUBLIC_BASE, due_work.PUBLIC_BASE_TREE), 'EXACT_FRONTEND_CHECKOUT_REQUIRED')
    prior = due_work.prior_files(ops, relay)
    reminders, _ = due_work.due_work_files(host, ops, prior)
    room, _ = room_tour.room_tour_files(due_work, host, ops, reminders)
    combined, _ = integrated.integrated_files(due_work, host, ops, room)
    base, _ = history.history_share_files(due_work, host, ops, combined)
    delta = json.loads(due_work.pinned_bytes(ops / 'frontend-private-custody-delta.json', CUSTODY_DELTA_SHA))
    check(set(delta) == {'parent_commit', 'parent_tree', 'frontend_commit', 'frontend_tree', 'files'}
          and due_work.source_pins(delta) == ((BASE, BASE_TREE), (HEAD, TREE))
          and {name: value['change'] for name, value in delta['files'].items()} == CHANGES,
          'EXACT_CUSTODY_FRONTEND_DELTA_REQUIRED')
    expected, bodies = base.copy(), {}
    for name, row in delta['files'].items():
        check(set(row) == {'change', *due_work.METADATA} and (name in base) == (CHANGES[name] == 'M'),
              'CUSTODY_PATH_CLASS_REQUIRED')
        body = host.regular(ops, f'private-custody-overlay/{name}').read_bytes()
        meta = {key: row[key] for key in due_work.METADATA}
        check(meta == {'mode': '100644', 'git_blob_oid': host.blob(body), 'bytes': len(body),
                       'sha256': host.sha(body), 'binary_nul_detected': False}
              and b'\0' not in body and meta != base.get(name), 'CUSTODY_PAYLOAD_MISMATCH')
        expected[name], bodies[name] = meta, body
    check(len(base) == 469 and len(expected) == 473, 'EXACT_473_FRONTEND_PATHS_REQUIRED')
    env = {'GIT_INDEX_FILE': str(index)}
    host.verify_bodies(frontend, BASE_TREE, base)
    if mode == 'apply':
        check(host.git(frontend, 'write-tree', env=env).decode().strip() == BASE_TREE, 'EXACT_HISTORY_INDEX_REQUIRED')
        due_work.verify_workspace(host, workspace, base)
        for name, body in bodies.items():
            path = workspace / name
            check(path.parent.is_dir() and path.parent.resolve() == path.parent and not path.is_symlink()
                  and (path.is_file() if CHANGES[name] == 'M' else not path.exists()), 'CUSTODY_WORKSPACE_TARGET_REQUIRED')
            oid = host.git(frontend, 'hash-object', '-w', '--stdin', data=body).decode().strip()
            check(oid == expected[name]['git_blob_oid'], 'STORED_CUSTODY_OID_MISMATCH')
            host.git(frontend, 'update-index', '--add', '--cacheinfo', '100644', oid, name, env=env)
            with path.open('xb' if CHANGES[name] == 'A' else 'wb') as out:
                out.write(body)
    tree = host.git(frontend, 'write-tree', env=env).decode().strip()
    check(tree == TREE, 'EXACT_CUSTODY_FRONTEND_TREE_REQUIRED')
    changes = host.git(frontend, 'diff', '--name-status', '--no-renames', BASE_TREE, tree).decode().splitlines()
    check(len(changes) == len(CHANGES) and set(changes) == {f'{action}\t{name}' for name, action in CHANGES.items()},
          'ONLY_EXACT_CUSTODY_PATHS_ALLOWED')
    host.verify_bodies(frontend, tree, expected)
    due_work.verify_workspace(host, workspace, expected)
    print(f'PRIVATE_CUSTODY_FRONTEND_{mode.upper()} commit={HEAD} tree={tree} source_paths={len(expected)}')


if __name__ == '__main__':
    main()

"""Verify the accepted due-work tree and reconstruct the exact listing room-tour tree."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path


BASE = 'fd3c72c0e5bcdcea51172f47dd44adea6ecaf1cf'
BASE_TREE = '63b3f5744faf0eeac848789ac10389c0f5c3ee71'
HEAD = 'ae824e1439557238c17abaf29dfc8b859086e987'
TREE = '88475e96ca4f523c5731fb210b0e8ad20e886f98'
DUE_WORK_SCRIPT_SHA256 = '02f931ab4255474deb05a8aed3863160a71f94919ef6afb1e5a2c11bd09b1ec6'
ROOM_TOUR_DELTA_SHA256 = '68d66a20b62c50b37b452210e1da2487af04c6e527e3a2107dd9fab58b534a6d'
NEW_DIRECTORY = 'packages/listing-room-tour'
CHANGES = {
    'apps/dashboard/src/api/admin.ts': 'M',
    'apps/dashboard/src/api/listingRoomTour.ts': 'A',
    'apps/dashboard/src/components/admin/RoomPhotoTourEditReview.tsx': 'A',
    'apps/dashboard/src/components/listings/RoomPhotoTourEditor.tsx': 'A',
    'apps/dashboard/src/components/listings/RoomPhotoTourPreview.tsx': 'A',
    'apps/dashboard/src/locale/en/common.json': 'M',
    'apps/dashboard/src/locale/es/common.json': 'M',
    'apps/dashboard/src/pages/admin/Listings.tsx': 'M',
    'apps/dashboard/src/pages/admin/SubmitListing.tsx': 'M',
    'apps/dashboard/src/pages/owner/SubmitListing.tsx': 'M',
    'apps/dashboard/src/pages/realtor/SubmitListing.tsx': 'M',
    'apps/landing/src/api/listings.ts': 'M',
    'apps/landing/src/components/ListingRoomTour.tsx': 'A',
    'apps/landing/src/data/listingRoomTour.ts': 'A',
    'apps/landing/src/locales/en/property_detail.json': 'M',
    'apps/landing/src/locales/es/property_detail.json': 'M',
    'apps/landing/src/views/PropertyDetail.tsx': 'M',
    'packages/listing-room-tour/index.ts': 'A',
    'tests/listing-room-tour.test.cjs': 'A',
}


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def load_due_work(ops):
    path = ops / 'reconstruct-due-work-reminders-frontend.py'
    require(path.is_file() and path.resolve() == path, 'CANONICAL_DUE_WORK_RECONSTRUCTOR_REQUIRED')
    require(hashlib.sha256(path.read_bytes()).hexdigest() == DUE_WORK_SCRIPT_SHA256, 'PINNED_DUE_WORK_RECONSTRUCTOR_MISMATCH')
    spec = importlib.util.spec_from_file_location('accepted_due_work_frontend', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require((module.HEAD, module.TREE) == (BASE, BASE_TREE), 'ACCEPTED_DUE_WORK_FRONTEND_REQUIRED')
    return module


def room_tour_files(due_work, host, ops, base):
    delta = json.loads(due_work.pinned_bytes(ops / 'frontend-listing-room-tour-delta.json', ROOM_TOUR_DELTA_SHA256))
    require(set(delta) == {'parent_commit', 'parent_tree', 'frontend_commit', 'frontend_tree', 'files'}
            and due_work.source_pins(delta) == ((BASE, BASE_TREE), (HEAD, TREE)), 'EXACT_LISTING_ROOM_TOUR_SOURCE_REQUIRED')
    require({name: row['change'] for name, row in delta['files'].items()} == CHANGES, 'EXACT_19_ROOM_TOUR_PATHS_REQUIRED')
    expected = base.copy()
    bodies = {}
    for name, row in delta['files'].items():
        require(set(row) == {'change', *due_work.METADATA}
                and (name in base) == (CHANGES[name] == 'M'), 'ROOM_TOUR_PATH_CLASS_REQUIRED')
        body = host.regular(ops, f'listing-room-tour-overlay/{name}').read_bytes()
        metadata = {key: row[key] for key in due_work.METADATA}
        require(metadata == {'mode': '100644', 'git_blob_oid': host.blob(body), 'bytes': len(body),
                             'sha256': host.sha(body), 'binary_nul_detected': False}
                and b'\0' not in body and metadata != base.get(name), 'ROOM_TOUR_PAYLOAD_MISMATCH')
        expected[name] = metadata
        bodies[name] = body
    require(len(base) == 453 and len(expected) == 461, 'EXACT_461_FRONTEND_PATHS_REQUIRED')
    return expected, bodies


def apply_room_tour(due_work, host, frontend, workspace, base, expected, bodies, env):
    require(host.git(frontend, 'write-tree', env=env).decode().strip() == BASE_TREE, 'EXACT_DUE_WORK_INDEX_REQUIRED')
    due_work.verify_workspace(host, workspace, base)
    new_dir = workspace / NEW_DIRECTORY
    require(new_dir.parent.is_dir() and new_dir.parent.resolve() == new_dir.parent
            and not new_dir.exists() and not new_dir.is_symlink(), 'ROOM_TOUR_PACKAGE_DIRECTORY_REQUIRED')
    new_dir.mkdir()
    for name, body in bodies.items():
        path = workspace / name
        require(path.parent.is_dir() and path.parent.resolve() == path.parent and not path.is_symlink()
                and (path.is_file() if CHANGES[name] == 'M' else not path.exists()), 'ROOM_TOUR_WORKSPACE_TARGET_REQUIRED')
        oid = host.git(frontend, 'hash-object', '-w', '--stdin', data=body).decode().strip()
        require(oid == expected[name]['git_blob_oid'], 'STORED_ROOM_TOUR_OID_MISMATCH')
        host.git(frontend, 'update-index', '--add', '--cacheinfo', '100644', oid, name, env=env)
        with path.open('xb' if CHANGES[name] == 'A' else 'wb') as out:
            out.write(body)


def main():
    require(len(sys.argv) == 6 and sys.argv[1] in {'apply', 'verify'}, 'ROOM_TOUR_MODE_AND_FOUR_PATHS_REQUIRED')
    mode = sys.argv[1]
    ops, relay, frontend, scratch = map(Path, sys.argv[2:])
    workspace = scratch / 'frontend-work'
    index = scratch / 'frontend-index'
    require(all(path.is_dir() and path.resolve() == path for path in (ops, relay, frontend, scratch, workspace))
            and index.is_file() and index.resolve() == index, 'CANONICAL_FRONTEND_WORKSPACE_REQUIRED')
    due_work = load_due_work(ops)
    host = due_work.load_host(relay)
    require(tuple(host.git(frontend, 'rev-parse', 'HEAD', 'HEAD^{tree}').decode().splitlines()) ==
            (due_work.PUBLIC_BASE, due_work.PUBLIC_BASE_TREE), 'EXACT_FRONTEND_CHECKOUT_REQUIRED')
    prior = due_work.prior_files(ops, relay)
    base, _ = due_work.due_work_files(host, ops, prior)
    expected, bodies = room_tour_files(due_work, host, ops, base)
    env = {'GIT_INDEX_FILE': str(index)}
    host.verify_bodies(frontend, BASE_TREE, base)
    if mode == 'apply':
        apply_room_tour(due_work, host, frontend, workspace, base, expected, bodies, env)

    tree = host.git(frontend, 'write-tree', env=env).decode().strip()
    require(tree == TREE, 'EXACT_LISTING_ROOM_TOUR_TREE_REQUIRED')
    changes = host.git(frontend, 'diff', '--name-status', '--no-renames', BASE_TREE, tree).decode().splitlines()
    require(len(changes) == len(CHANGES) and set(changes) == {f'{action}\t{name}' for name, action in CHANGES.items()},
            'ONLY_EXACT_19_ROOM_TOUR_CHANGES_ALLOWED')
    host.verify_bodies(frontend, tree, expected)
    due_work.verify_workspace(host, workspace, expected)
    print(f'LISTING_ROOM_TOUR_FRONTEND_{mode.upper()} commit={HEAD} tree={tree} source_paths={len(expected)}')


if __name__ == '__main__':
    main()

"""Verify the accepted frontend lineage and apply the exact saved professionals source."""

import hashlib
import importlib.util
import json
import stat
import sys
from pathlib import Path


PUBLIC_BASE = '73d771940e73d42ed044cea77a5258a93a445b05'
PUBLIC_BASE_TREE = 'f718c91136d2726f8f8966c367f3c20584081a0a'
RELAY_SOURCE = ('d967a3714b40b6e98ad2654c3ce865ad5d152a20', 'ef36dc3083673e3daad8f73401ea9d00a87c32b3')
VISITS_SOURCE = ('869a74bde961e30a4dc56c0bdc71d82cde62e2e2', 'cabd6e37ef02617216b5f3d8b85c3b63c6a3d8ad')
CONVERSATION_SOURCE = ('fe32e0749ee6af50a39e971058e2cba55f296152', '877a62d0e0ce90ea5138ad3cca50dfbafb0d6107')
NEGOTIATION_SOURCE = ('88c9e601dfc5950d4e4369007464d963a5d1ccee', '262e78d5ff659b42b24ea90a7b5b1603b93e8326')
REVIEW_SOURCE = ('5550248b79a6672718f096707d54699e5712b5f6', 'bd09c31331b26523ee0e12d84ec2756913dbd38e')
BASE = '6cc09601fece171319601788b3f0195018edd8d7'
BASE_TREE = '57c6090e94d93e75bec51e402af21ed7877ad343'
HEAD = 'c593006658fa1f2ac1943fac7f93c266eaf79ec1'
TREE = 'd35be3c8b9e2cd8255001c9d7027dcd16de3335f'

RELAY_HOST_SHA256 = '741823a5585c1bd396d8a05b04a0cdb47c41c1fee680ee128fd9de535c1da8d1'
RELAY_MANIFEST_SHA256 = '45d69ba856c72c4dd9b342e7084f0fb604957d617802b13d3d4613fc64c72891'
FAVORITES_DELTA_SHA256 = '68d123ab453b7aa8327f8ac5b8b87ad872f5be4d7545f7151832e451c6cdf271'
PRIOR_STAGES = (
    ('frontend-service-visits-delta.json', '155c8986851626f38c03f282483d19b1ff145abdacaeb12f3d62508a2bb5802c',
     RELAY_SOURCE, VISITS_SOURCE, 436),
    ('frontend-service-conversation-delta.json', 'b15bbd2fa29cf976a790172be7cd2ee5392fb6592ec2d4f33050d909a72438c8',
     VISITS_SOURCE, CONVERSATION_SOURCE, 439),
    ('frontend-service-negotiation-delta.json', '104ca0f78f09231b43c13065919f900d6fe7a83e7a1e5fb1a8a7a851df8e650a',
     CONVERSATION_SOURCE, NEGOTIATION_SOURCE, 439),
    ('frontend-service-review-delta.json', 'f90052b780b96a1ae7fd700ff2dc70de7db75c492dc8268cad9983946fa5ea20',
     NEGOTIATION_SOURCE, REVIEW_SOURCE, 442),
    ('frontend-service-rebooking-delta.json', '8fff6d5008ce7f4a3ebb7dd70ac2658e215beea16482e79665b19c9bd13d9f02',
     REVIEW_SOURCE, (BASE, BASE_TREE), 444),
)
CHANGES = {
    'apps/dashboard/src/api/serviceFavorites.ts': 'A',
    'apps/dashboard/src/components/network/ServiceFavorites.tsx': 'A',
    'apps/dashboard/src/components/network/ServiceRequestCreate.tsx': 'M',
    'apps/dashboard/src/components/network/ServiceWorkflow.tsx': 'M',
    'apps/dashboard/src/locale/en/property_network.json': 'M',
    'apps/dashboard/src/locale/es/property_network.json': 'M',
    'tests/service-favorites.test.cjs': 'A',
    'tests/service-rebook.test.cjs': 'M',
}
METADATA = ('mode', 'git_blob_oid', 'bytes', 'sha256', 'binary_nul_detected')


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def pinned_bytes(path, digest):
    require(path.is_file() and path.resolve() == path, 'CANONICAL_SOURCE_FILE_REQUIRED')
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == digest, 'PINNED_SOURCE_HASH_MISMATCH')
    return data


def load_host(relay):
    path = relay / 'ci/frontend/frontend_ci_host.py'
    pinned_bytes(path, RELAY_HOST_SHA256)
    spec = importlib.util.spec_from_file_location('accepted_frontend_host', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_pins(delta):
    return (delta['parent_commit'], delta['parent_tree']), (delta['frontend_commit'], delta['frontend_tree'])


def prior_files(ops, relay):
    manifest = json.loads(pinned_bytes(relay / 'ci/frontend/frontend-overlay.json', RELAY_MANIFEST_SHA256))
    files = manifest['application_files']
    require((manifest['source_worktree_head'], manifest['frontend_candidate_tree']) == RELAY_SOURCE
            and len(files) == 434, 'ACCEPTED_RELAY_SOURCE_REQUIRED')
    for name, digest, parent, head, count in PRIOR_STAGES:
        delta = json.loads(pinned_bytes(ops / name, digest))
        require(source_pins(delta) == (parent, head), 'ACCEPTED_FRONTEND_LINEAGE_REQUIRED')
        for name, row in delta['files'].items():
            require(row['change'] == ('M' if name in files else 'A'), 'PRIOR_FRONTEND_PATH_CLASS_REQUIRED')
            files[name] = {key: row[key] for key in METADATA}
        require(len(files) == count, 'PRIOR_FRONTEND_PATH_COUNT_REQUIRED')
    return files


def favorites_files(host, ops, base):
    delta = json.loads(pinned_bytes(ops / 'frontend-service-favorites-delta.json', FAVORITES_DELTA_SHA256))
    require(source_pins(delta) == ((BASE, BASE_TREE), (HEAD, TREE)), 'EXACT_FAVORITES_SOURCE_REQUIRED')
    require({name: row['change'] for name, row in delta['files'].items()} == CHANGES, 'EXACT_EIGHT_FAVORITES_PATHS_REQUIRED')
    expected = base.copy()
    bodies = {}
    for name, row in delta['files'].items():
        require((name in base) == (CHANGES[name] == 'M'), 'FAVORITES_PATH_CLASS_REQUIRED')
        body = host.regular(ops, f'favorites-overlay/{name}').read_bytes()
        metadata = {key: row[key] for key in METADATA}
        require(metadata == {'mode': '100644', 'git_blob_oid': host.blob(body), 'bytes': len(body),
                             'sha256': host.sha(body), 'binary_nul_detected': False}
                and b'\0' not in body and metadata != base.get(name), 'FAVORITES_PAYLOAD_MISMATCH')
        expected[name] = metadata
        bodies[name] = body
    require(len(expected) == 447, 'EXACT_447_FRONTEND_PATHS_REQUIRED')
    return expected, bodies


def verify_workspace(host, workspace, files):
    for name, entry in files.items():
        path = host.regular(workspace, name)
        body = path.read_bytes()
        require(len(body) == entry['bytes'] and host.sha(body) == entry['sha256']
                and bool(path.stat().st_mode & stat.S_IXUSR) == (entry['mode'] == '100755'), 'FRONTEND_WORKSPACE_MISMATCH')


def main():
    require(len(sys.argv) == 6 and sys.argv[1] in {'apply', 'verify'}, 'FAVORITES_MODE_AND_FOUR_PATHS_REQUIRED')
    mode = sys.argv[1]
    ops, relay, frontend, scratch = map(Path, sys.argv[2:])
    workspace = scratch / 'frontend-work'
    index = scratch / 'frontend-index'
    require(all(path.is_dir() and path.resolve() == path for path in (ops, relay, frontend, scratch, workspace))
            and index.is_file() and index.resolve() == index, 'CANONICAL_FRONTEND_WORKSPACE_REQUIRED')
    host = load_host(relay)
    require(tuple(host.git(frontend, 'rev-parse', 'HEAD', 'HEAD^{tree}').decode().splitlines()) ==
            (PUBLIC_BASE, PUBLIC_BASE_TREE), 'EXACT_FRONTEND_CHECKOUT_REQUIRED')
    base = prior_files(ops, relay)
    expected, bodies = favorites_files(host, ops, base)
    env = {'GIT_INDEX_FILE': str(index)}
    host.verify_bodies(frontend, BASE_TREE, base)
    if mode == 'apply':
        require(host.git(frontend, 'write-tree', env=env).decode().strip() == BASE_TREE, 'EXACT_PRIOR_INDEX_REQUIRED')
        verify_workspace(host, workspace, base)
        for name, body in bodies.items():
            path = workspace / name
            require(path.parent.resolve() == path.parent and not path.is_symlink()
                    and (path.is_file() if CHANGES[name] == 'M' else not path.exists()), 'FAVORITES_WORKSPACE_TARGET_REQUIRED')
            oid = host.git(frontend, 'hash-object', '-w', '--stdin', data=body).decode().strip()
            require(oid == expected[name]['git_blob_oid'], 'STORED_FAVORITES_OID_MISMATCH')
            host.git(frontend, 'update-index', '--add', '--cacheinfo', '100644', oid, name, env=env)
            with path.open('xb' if CHANGES[name] == 'A' else 'wb') as out:
                out.write(body)

    tree = host.git(frontend, 'write-tree', env=env).decode().strip()
    require(tree == TREE, 'EXACT_FAVORITES_TREE_REQUIRED')
    changes = host.git(frontend, 'diff', '--name-status', '--no-renames', BASE_TREE, tree).decode().splitlines()
    require(len(changes) == len(CHANGES) and set(changes) == {f'{action}\t{name}' for name, action in CHANGES.items()},
            'ONLY_EXACT_EIGHT_FAVORITES_CHANGES_ALLOWED')
    host.verify_bodies(frontend, tree, expected)
    verify_workspace(host, workspace, expected)
    print(f'SERVICE_FAVORITES_FRONTEND_{mode.upper()} commit={HEAD} tree={tree} source_paths={len(expected)}')


if __name__ == '__main__':
    main()

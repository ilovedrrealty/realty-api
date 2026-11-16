"""Bind only the three exact changed blobs to the accepted 473-file custody tree."""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

BASE = 'ff59f37cef4f1b6f4ee4b970bf7226f46b76e762'
BASE_TREE = 'a0429808349e5221b7b4103cb1268ff9c7f67250'
APP = '5ba8fa7afb38dc0a8705cb659417781e01d94703'
TREE = '632874430e45c35aaf858393f3718c8e08c27c39'
DELTA_SHA = '1418ddcb62280df65a9627932b04aa90780d831a897eb1b77c0314fe2ab64082'
FILES = ('apps/dashboard/src/components/network/ProjectWorkspace.tsx',
         'apps/dashboard/src/components/network/PropertyRegistry.tsx', 'tests/property-network-ui.test.cjs')
TESTS = (
    'actual service history displays authorized work and server progress without leaking unrelated fixture fields',
    'actual archive retains authorized history and removes new-project controls without claiming deletion',
    'actual registry permission loss removes private details, draft fields and history',
    'actual private-project participants read scoped history without fetching full registry records',
    'actual service self-consent is default-off and saves only explicit own preferences in EN and ES',
    'service history retains event identity but discards author and property-management data',
    'private property project filters and service history never fetch registry detail or project private fields',
)


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def git(repo, index, *args, data=None):
    return subprocess.check_output(['git', '-C', str(repo), *args], input=data, timeout=45,
                                   env={**os.environ, 'GIT_INDEX_FILE': str(index), 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'})


def ordinary(path):
    require(path.is_file() and path.resolve() == path, 'EXACT_REGULAR_FILE_REQUIRED')
    return path.read_bytes()


def blob(raw):
    return hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()


def source(mode, frontend, state):
    tools = Path(__file__).resolve().parent
    work, index = state / 'frontend-work', state / 'frontend-index'
    require(all(path.is_dir() and path.resolve() == path for path in (frontend, state, work)) and
            index.is_file() and index.resolve() == index, 'CANONICAL_OWNED_FRONTEND_REQUIRED')
    data = ordinary(tools / 'frontend-delta.json')
    require(hashlib.sha256(data).hexdigest() == DELTA_SHA, 'EXACT_CATEGORY_DELTA_REQUIRED')
    delta = json.loads(data)
    require((delta['base_commit'], delta['base_tree'], delta['commit'], delta['tree']) == (BASE, BASE_TREE, APP, TREE)
            and tuple(delta['files']) == FILES, 'PINNED_APPLIED_PATHS_REQUIRED')
    if mode == 'apply':
        require(git(frontend, index, 'write-tree').decode().strip() == BASE_TREE, 'EXACT_CUSTODY_TREE_REQUIRED')
        for name, row in delta['files'].items():
            before, after = ordinary(work / name), ordinary(tools / 'overlay' / name)
            require(blob(before) == row['previous_git_oid'] and blob(after) == row['git_oid']
                    and hashlib.sha256(after).hexdigest() == row['sha256'] and len(after) == row['bytes'], 'CATEGORY_BLOB_PIN_REQUIRED')
            stored = git(frontend, index, 'hash-object', '-w', '--stdin', data=after).decode().strip()
            require(stored == row['git_oid'], 'STORED_GIT_BLOB_REQUIRED')
            git(frontend, index, 'update-index', '--cacheinfo', '100644', stored, name)
            (work / name).write_bytes(after)
    require(git(frontend, index, 'write-tree').decode().strip() == TREE, 'EXACT_CATEGORY_TREE_REQUIRED')
    changed = git(frontend, index, 'diff', '--name-only', '--no-renames', BASE_TREE, TREE).decode().splitlines()
    require(tuple(changed) == FILES, 'ONLY_THREE_CATEGORY_FILES_ALLOWED')
    ls = git(frontend, index, 'ls-files', '--stage', '-z').split(b'\0')
    require(len(ls) == 474 and ls[-1] == b'', 'EXACT_473_FRONTEND_PATHS_REQUIRED')
    for item in ls[:-1]:
        identity, name = item.decode().split('\t', 1)
        mode_bits, oid, stage = identity.split(' ')
        path = work / name
        require(stage == '0' and mode_bits in ('100644', '100755') and path.is_relative_to(work)
                and blob(ordinary(path)) == oid and bool(path.stat().st_mode & 0o100) == (mode_bits == '100755'),
                'RECONSTRUCTED_WORKSPACE_DIFFERS')
    print(f'SERVICE_HISTORY_SOURCE_{mode.upper()} commit={APP} tree={TREE} files={len(ls)-1}')


def check_tap(path):
    raw = ordinary(path)
    require(len(raw) < 131072, 'BOUNDED_TEST_LOG_REQUIRED')
    body = raw.decode('utf-8')
    observed = re.findall(r'^ok \d+ - (.+)$', body, re.M)
    selected = [name for name in observed if name in TESTS]
    require(len(selected) == len(TESTS) and set(selected) == set(TESTS)
            and not re.search(r'^not ok ', body, re.M) and re.search(r'^# fail 0$', body, re.M),
            'EXACT_TARGETED_NATIVE_TESTS_REQUIRED')
    print(f'SERVICE_HISTORY_TARGETED_UI_AND_TRANSPORT native=0 selected={len(selected)} pass={len(TESTS)} sha256={hashlib.sha256(raw).hexdigest()}')


if __name__ == '__main__':
    argv = sys.argv[1:]
    if argv == ['test-pattern']:
        # All names use literal letters, spaces and hyphens; POSIX ERE and JS agree.
        print('^(' + '|'.join(TESTS) + ')$')
    elif len(argv) == 2 and argv[0] == 'check-tap':
        check_tap(Path(argv[1]))
    elif len(argv) == 3 and argv[0] in ('apply', 'verify'):
        source(argv[0], *map(Path, argv[1:]))
    else:
        raise ValueError('EXACT_HELPER_MODE_REQUIRED')

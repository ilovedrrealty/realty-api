"""Narrow source candidate: accepted Linux inventory, then unchanged migration V2.

No application, strict107 suite, server or worker is started by this adapter.
The workflow owns the ephemeral namespace and its outer teardown.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import types

TOOLS = Path('/tools')
SOURCE = Path('/application')
BASELINE = Path('/baseline')
RESULTS = Path('/results')
APP_COMMIT = 'b33aafe72c17d037c557546b57fb762704e8cb77'
APP_TREE = '40f882b6c49995b5e924a377738830bb9183a6fb'
BASE_COMMIT = '0811946ea02a310b03116960663e3ba6871a07d0'
BASE_TREE = '18b2087a31853a275c5c6ca791a68214b1cd38ef'
PINS = {
    'run_migration_acceptance.py': '2babf30bf8446ef77cc9585b13393f552550657fb7d9330a47cfbf880091a186',
    'migration-source-manifest.json': '5dde5b58cd032719713d69817e0c8af693fe97255c5ce482ae9cc61a34ae7b1b',
    'accepted_strict107.py': 'd178935e3425a62c900cdae65995ce1260bf3fdc5c2b35d0de7257d87847af2e',
    'accepted107-source.json': 'c8067bfc2c6eb0bec1725f01fbffbc772fdef828ae9b5c79618f4c2ce3b0fa47',
    'accepted107-inventory.json': '886bb7fba18d04d7b2aaa6eb0824d7d232b1e988e9aa7ff14e0ab163d63099e2',
    'accepted107-result.json': 'e6f8dada0ae41a3c5374c4e446c10fe2c3da6b0b91853d54fb15b9bbfe98e2f5',
    'accepted107-proof.json': '9cd6da12791a92e5cb1125c755ae9c19c69f81ee17482e670523c7103178e436',
    'accepted107-independent.json': '27c207a822c585f06c154cf8d6116d36ae4fdf68b538c64f3e3f2f9e965c096f',
    'strict107.Dockerfile': '8696f71a238a432453d4c49f13e514e803be2cdd8de24d084ddd214a86a768d0',
}
STABLE = ('python_version', 'python_executable_sha256', 'git_executable_sha256',
          'machine', 'packages', 'pillow_version', 'pillow_imaging_sha256',
          'webp_available', 'webp_version', 'requirements_sha256',
          'postgres_server_version_num')


def need(ok, code):
    if not ok:
        raise RuntimeError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def document(name):
    path = TOOLS / name
    need(path.is_file() and not path.is_symlink() and path.stat().st_size < 262144,
         'BOUNDED_PINNED_TOOL_REQUIRED')
    raw = path.read_bytes()
    need(sha(raw) == PINS[name], 'EXACT_TOOL_BYTES_REQUIRED')
    return raw


def module(name, filename):
    raw = document(filename)
    value = types.ModuleType(name)
    value.__file__ = str(TOOLS / filename)
    exec(compile(raw, value.__file__, 'exec'), value.__dict__)
    return value


def write(name, value):
    raw = json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2).encode('ascii')
    need(len(raw) <= 131072, 'BOUNDED_ENTRY_AGGREGATE_REQUIRED')
    with (RESULTS / name).open('xb') as stream:
        stream.write(raw)


def git_identity(root, commit, tree):
    def git(*args):
        return subprocess.check_output(['git', '--no-optional-locks', '-c',
            'safe.directory=' + str(root), '-C', str(root), *args],
            timeout=20, stderr=subprocess.DEVNULL,
            env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'})
    need(git('rev-parse', 'HEAD').decode('ascii').strip() == commit
         and git('rev-parse', 'HEAD^{tree}').decode('ascii').strip() == tree,
         'IMMUTABLE_SOURCE_COMMIT_TREE_REQUIRED')
    need(not git('status', '--porcelain').strip(), 'CLEAN_SOURCE_CHECKOUT_REQUIRED')


def accepted_evidence():
    inventory = json.loads(document('accepted107-inventory.json'))
    result = json.loads(document('accepted107-result.json'))
    proof = json.loads(document('accepted107-proof.json'))
    peer = json.loads(document('accepted107-independent.json'))
    for value in (inventory, result, proof, peer):
        need(value.get('source_commit', value.get('commit')) == APP_COMMIT,
             'ACCEPTED107_SOURCE_IDENTITY_REQUIRED')
    need(proof['tree'] == peer['tree'] == APP_TREE
         and proof['run_id'] == peer['run_id'] == '41363483499'
         and proof['job_id'] == peer['job_id'] == 125018646713
         and proof['attempt'] == peer['attempt'] == 1,
         'GENUINE_ACCEPTED107_RUN_REQUIRED')
    need(proof['status'] == 'PASS_ACTUAL_107_AND_EXACT_SCHEMA_SOURCE_NATIVE_DOCKER_CLEANUP_NOT_RELEASE_ACCEPTANCE'
         and peer['decision'] == 'INDEPENDENT_GENUINE_ACTUAL107_ACCEPTANCE_CONFIRMED_NOT_FULL_RELEASE'
         and result['status'] == 'PASS_107_ASSERTIONS_AND_EXACT_SCHEMA_CLEANUP_PG_CONTAINER_CLEANUP_PENDING',
         'GENUINE_ACCEPTED107_STATUS_REQUIRED')
    assertions = result['assertions']
    need(type(assertions['tests_run']) is int and assertions['tests_run'] == 107
         and all(type(assertions[key]) is int and assertions[key] == 0 for key in
                 ('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'))
         and assertions['denied_guard_events'] == [] and assertions['failed_test_ids'] == []
         and result['tests_pass'] is True and result['exact_schemas_absent'] is True
         and result['source_unchanged'] is True and result['native_child_exit'] == 0
         and result['cleanup_errors'] == []
         and proof['outer_owned_docker_cleanup_verified'] is True
         and peer['outer_exact_owned_docker_cleanup_marker_verified'] is True,
         'COMPLETE_ACCEPTED107_AND_CLEANUP_REQUIRED')
    need(inventory['source_manifest_sha256'] == result['source_manifest_sha256']
         == peer['source_manifest_sha256'] == PINS['accepted107-source.json'],
         'ACCEPTED107_MANIFEST_BINDING_REQUIRED')
    return inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-ops-commit', required=True)
    args = parser.parse_args()
    need(sys.platform == 'linux' and sys.version_info[:2] == (3, 11)
         and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,
         'LINUX_PYTHON311_ISOLATED_NO_SITE_REQUIRED')
    run, attempt = os.environ.get('GITHUB_RUN_ID', ''), os.environ.get('GITHUB_RUN_ATTEMPT', '')
    need(re.fullmatch(r'[1-9][0-9]{0,19}', run) and attempt == '1'
         and re.fullmatch(r'[0-9a-f]{40}', args.expected_ops_commit)
         and os.environ.get('GITHUB_SHA') == args.expected_ops_commit
         and os.environ.get('GITHUB_REPOSITORY') == 'ilovedrrealty/realty-api'
         and os.environ.get('GITHUB_REF') == 'refs/heads/codex-ci-migration-20261101'
         and os.environ.get('GITHUB_EVENT_NAME') == 'push'
         and all(os.environ.get(k) == 'true' for k in ('CI', 'GITHUB_ACTIONS', 'ILDR_EPHEMERAL_CI')),
         'EXPLICIT_INITIAL_MIGRATION_CI_CONTEXT_REQUIRED')
    need(RESULTS.is_dir() and not RESULTS.is_symlink() and not any(RESULTS.iterdir())
         and Path('/scratch').is_dir() and Path.cwd() == Path('/scratch')
         and not Path('/scratch/.env').exists(), 'FRESH_FIXED_OUTPUT_AND_CWD_REQUIRED')
    report = {'status': 'FAILED_ENTRY_OR_MIGRATION', 'migration_event_commit': args.expected_ops_commit,
              'application_commit': APP_COMMIT, 'application_tree': APP_TREE,
              'baseline_commit': BASE_COMMIT, 'baseline_tree': BASE_TREE,
              'ci_run_id': run, 'ci_run_attempt': attempt,
              'accepted107_run_id': '41363483499', 'accepted107_job_id': 125018646713,
              'entry_sha256': sha(Path(__file__).read_bytes()),
              'migration_runner_sha256': PINS['run_migration_acceptance.py'],
              'migration_manifest_sha256': PINS['migration-source-manifest.json'],
              'stable_linux_inventory_matches_accepted107': False,
              'migration_parent_native_exit': None,
              'outer_container_cleanup_verified_by_this_entry': False,
              'production_provider_or_server_actions': False}
    code = 1
    try:
        need(TOOLS.is_dir() and not TOOLS.is_symlink()
             and {p.name for p in TOOLS.iterdir()} == set(PINS) | {'migration_entry.py', 'emit_migration_logs.py'},
             'EXACT_TOOLS_FILE_SET_REQUIRED')
        for name in PINS:
            document(name)
        prior = accepted_evidence()
        strict = module('_accepted_inventory_only', 'accepted_strict107.py')
        strict.bootstrap_sites()
        need(strict.available_mib() >= 600, 'INITIAL_MEMORY_BELOW_600_MIB')
        source_manifest = json.loads(document('accepted107-source.json'))
        migration_manifest = json.loads(document('migration-source-manifest.json'))
        need(len(source_manifest['files']) == len(migration_manifest['candidate_lf_sha256']) == 186
             and {name: item['sha256'] for name, item in source_manifest['files'].items()}
                 == migration_manifest['candidate_lf_sha256']
             and migration_manifest['candidate_operational_sha256'] == {},
             'EXACT_ACCEPTED186_APP_AND_EMPTY_OPERATIONAL_MAP_REQUIRED')
        git_identity(SOURCE, APP_COMMIT, APP_TREE)
        git_identity(BASELINE, BASE_COMMIT, BASE_TREE)
        operational = strict.git_source(source_manifest, APP_COMMIT)
        need(operational == prior['operational_git_blobs'], 'ACCEPTED107_GIT_OPERATIONS_CHANGED')
        current = strict.linux_inventory(PINS['accepted107-source.json'], APP_COMMIT, operational)
        connection, version = strict.local_connection()
        connection.close()
        current['postgres_server_version_num'] = version
        need(all(current[key] == prior[key] for key in STABLE), 'LINUX_RUNTIME_OR_PACKAGE_BYTES_CHANGED')
        current.update(migration_event_commit=args.expected_ops_commit,
                       stable_linux_inventory_matches_accepted107=True)
        write('migration-linux-inventory.json', current)
        report['stable_linux_inventory_matches_accepted107'] = True
        migration = module('_reviewed_migration_v2', 'run_migration_acceptance.py')
        migration.verify_sources(SOURCE, BASELINE, migration_manifest)
        code = migration.parent(argparse.Namespace(source=SOURCE, baseline_source=BASELINE,
            manifest=TOOLS/'migration-source-manifest.json',
            manifest_sha256=PINS['migration-source-manifest.json'],
            self_sha256=PINS['run_migration_acceptance.py'],
            database_url='postgresql+psycopg2://postgres@127.0.0.1:55449/ildr_platform_test_20261025',
            result=RESULTS/'migration-result.json'))
        report['migration_parent_native_exit'] = code
        need(code == 0, 'ACTUAL_MIGRATION_PARENT_FAILED')
        result = json.loads((RESULTS/'migration-result.json').read_bytes())
        need(result['status'] == 'PASS_ACTUAL_CI_BOOTSTRAP_MIGRATIONS_AND_EXACT_DATABASE_CLEANUP'
             and result['databases_absent'] is True and result['source_unchanged'] is True
             and result['cleanup_errors'] == [], 'ACTUAL_MIGRATION_AND_DATABASE_CLEANUP_REQUIRED')
        report['status'] = 'PASS_ENTRY_INVENTORY_AND_ACTUAL_MIGRATION_DATABASE_CLEANUP_OUTER_CLEANUP_PENDING'
    except Exception as failure:
        value = failure.args[0] if type(failure) in (RuntimeError, ValueError) and failure.args else None
        report['failure_code'] = value if isinstance(value, str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,99}', value) else 'MIGRATION_ENTRY_FAILED'
        code = 1
    finally:
        write('migration-entry.json', report)
    return code


if __name__ == '__main__':
    raise SystemExit(main())

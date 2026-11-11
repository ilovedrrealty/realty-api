"""Compare the accepted services163 Linux inventory, then rehearse the full 52 revisions.

The adapter starts no application server, worker or services163 test suite.
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
APP_COMMIT = 'e9750472783e909dddb7ade065ec8739188c8c71'
APP_TREE = '22f32bd033bd20f16b2da993b10aebe2d9d32051'
APP_PARENT = '3da654cefb806ba90a8e66078721611f2d2d57d7'
APP_PARENT_TREE = 'ec5f60b27a4aabfb8b1dd6de5b55da40879f0c8c'
BASE_COMMIT = '0811946ea02a310b03116960663e3ba6871a07d0'
BASE_TREE = '18b2087a31853a275c5c6ca791a68214b1cd38ef'
PINS = {
    'run_migration_acceptance.py': '671b132034717c05c9e349d05316a3758d4034bb7649d8decd367ac58c26b00b',
    'migration-source-manifest.json': 'f64c538b5baeb5fdd5fa87d2e52ed9638ea30297fc6d1aa76c7058a1cb783096',
    'accepted_services163.py': 'b8a83af91346f9631c82f69ce00bd96b75e564801b2e7fdf360c2c7f97c8cb82',
    'accepted163-source.json': 'a4db349b759165d1bff20f99d8d53b3cbc20e6239e164bb9de6049338db6a637',
    'accepted163-inventory.json': '7402b6c99b87141b6142702277b1afaed962455ad3aa6ba02225e2a58f013c7a',
    'accepted163-result.json': 'd185d5005bec8d332d10ac4c37e050c2227e4fd62a7c7a19916bfea26dc26ec9',
    'accepted163-proof.json': 'f866947d2b2f283b51299a67257c97a5624d7f8cd8e03c7bae33e5a8eb5685e5',
    'accepted163-independent.json': 'f40eb8a893712505412cf7c67e3bf7b65a58973fae9c8b45559a397e889f38f0',
    'services163.Dockerfile': '0e449fe29d5c9137ba8701ebb1d4cb301377d932dd28ae2cd5b40d0f0e9610f2',
}
SERVICES_OPERATIONS = {
    'ci/services163.py', 'ci/services163-source.json', 'ci/services163.Dockerfile',
    '.github/workflows/services163.yml',
}
ACCEPTED_RUN = 41457890694
ACCEPTED_JOB = 125319170045
ACCEPTED_INDEPENDENT_ORIGINAL_SHA = '5e31e13dda2adf6454df4c7c3455a1185d597dbe3d99ab9aba9ee1e37f56c4ad'
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


def git_identity(root, commit, tree, parent=None, parent_tree=None):
    def git(*args):
        return subprocess.check_output(['git', '--no-optional-locks', '-c',
            'safe.directory=' + str(root), '-C', str(root), *args],
            timeout=20, stderr=subprocess.DEVNULL,
            env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never'})
    need(git('rev-parse', 'HEAD').decode('ascii').strip() == commit
         and git('rev-parse', 'HEAD^{tree}').decode('ascii').strip() == tree,
         'IMMUTABLE_SOURCE_COMMIT_TREE_REQUIRED')
    need(not git('status', '--porcelain').strip(), 'CLEAN_SOURCE_CHECKOUT_REQUIRED')
    if parent is not None:
        commit_header = git('cat-file', '-p', 'HEAD').split(b'\n\n', 1)[0].splitlines()
        need([line for line in commit_header if line.startswith(b'parent ')] == [b'parent ' + parent.encode('ascii')]
             and git('rev-parse', 'HEAD^1^{tree}').decode('ascii').strip() == parent_tree,
             'EXACT_SERVICES_APPLICATION_PARENT_REQUIRED')


def accepted_evidence():
    inventory = json.loads(document('accepted163-inventory.json'))
    result = json.loads(document('accepted163-result.json'))
    proof = json.loads(document('accepted163-proof.json'))
    peer = json.loads(document('accepted163-independent.json'))
    for value in (inventory, result, proof, peer):
        need(value.get('source_commit', value.get('commit')) == APP_COMMIT,
             'ACCEPTED163_SOURCE_IDENTITY_REQUIRED')
    need(proof['tree'] == peer['tree'] == APP_TREE
         and proof['application_commit'] == peer['parent'] == APP_PARENT
         and peer['parent_tree'] == APP_PARENT_TREE
         and proof['run_id'] == peer['run_id'] == ACCEPTED_RUN
         and proof['job_id'] == peer['job_id'] == ACCEPTED_JOB
         and proof['attempt'] == peer['attempt'] == 1,
         'GENUINE_ACCEPTED163_RUN_REQUIRED')
    need(proof['status'] == 'PASS_ACTUAL_CORRECTED163_TESTS_AND_EXACT_SCHEMA_OWNED_DOCKER_CLEANUP_NOT_FULL52_OR_RELEASE'
         and peer['decision'] == 'INDEPENDENT_ACTUAL_CHANGED_SOURCE_SERVICES163_RUNTIME_AND_EXACT_SCHEMA_OWNED_DOCKER_CLEANUP_ACCEPTED_NOT_FULL52_OR_RELEASE'
         and result['status'] == 'PASS_SERVICES163_ASSERTIONS_AND_EXACT_SCHEMA_CLEANUP_PG_CONTAINER_CLEANUP_PENDING',
         'GENUINE_ACCEPTED163_STATUS_REQUIRED')
    assertions = result['assertions']
    need(type(assertions['tests_run']) is int and assertions['tests_run'] == proof['tests_run'] == peer['actual_tests']['tests_run'] == 163
         and assertions['class_counts'] == proof['class_counts'] == peer['actual_tests']['class_counts']
         == {'CustomerDeliveryTests': 18, 'LeadDeliveryTests': 52,
             'PrivateImageContracts': 4, 'PrivateRegistryMigration': 5, 'PrivateRegistryRoutes': 16,
             'PrivateServiceMigration': 5, 'PrivateServiceWorkflow': 30, 'PropertyNetworkRoutes': 18,
             'SignupNotificationTests': 15}
         and all(type(assertions[key]) is int and assertions[key] == 0 for key in
                 ('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'))
         and assertions['denied_guard_events'] == [] and assertions['failed_test_ids'] == []
         and assertions['failure_locations'] == [] and assertions['failure_locations_truncated'] is False
         and result['tests_pass'] is True and result['exact_schemas_absent'] is True
         and result['source_unchanged'] is True and result['native_child_exit'] == 0
         and result['cleanup_errors'] == []
         and len(assertions['expected_schemas']) == 7
         and set(assertions['schema_uuid_consumed']) == set(assertions['expected_schemas'])
         and all(consumed is True for consumed in assertions['schema_uuid_consumed'].values())
         and proof['all_seven_controlled_schemas_consumed_and_absent'] is True
         and peer['schemas']['all7_nonce_consumed'] is True and peer['schemas']['all7_exact_schemas_absent'] is True
         and proof['native_child_client_container_exit'] == peer['native_observation']['client_exit']
         == peer['native_observation']['container_exit'] == 0
         and proof['actual_container_not_running_and_notOOM'] is True
         and peer['native_observation']['running'] is False and peer['native_observation']['oom_killed'] is False
         and proof['owned_cleanup_marker_after_blocks_line'] == peer['owned_cleanup']['line']
         and proof['owned_cleanup_marker_after_blocks_line'] > max(item['end_line'] for item in peer['actual_unique_complete_blocks'].values())
         and peer['owned_cleanup']['after_both_complete_blocks'] is True
         and proof['all_six_actual_job_steps_success'] is True,
         'COMPLETE_ACCEPTED163_AND_CLEANUP_REQUIRED')
    need(inventory['source_manifest_sha256'] == result['source_manifest_sha256']
         == PINS['accepted163-source.json']
         and result['runner_sha256'] == PINS['accepted_services163.py']
         and proof['inventory_sha256'] == peer['actual_unique_complete_blocks']['services163-linux-inventory.json']['sha256']
         == PINS['accepted163-inventory.json']
         and proof['result_sha256'] == peer['actual_unique_complete_blocks']['services163-result.json']['sha256']
         == PINS['accepted163-result.json']
         and proof['independent_actual_review_sha256'] == peer['original_receipt_sha256']
         == ACCEPTED_INDEPENDENT_ORIGINAL_SHA
         and inventory['bootstrap_and_full_migration_chain_proven'] is False
         and result['bootstrap_and_full_migration_chain_proven'] is False
         and peer['bootstrap_and_full52_migration_chain_proven'] is False,
         'ACCEPTED163_MANIFEST_AND_REPORT_BINDING_REQUIRED')
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
         and os.environ.get('GITHUB_REF') == 'refs/heads/codex-ci-services-migration-20261102'
         and os.environ.get('GITHUB_EVENT_NAME') == 'push'
         and all(os.environ.get(k) == 'true' for k in ('CI', 'GITHUB_ACTIONS', 'ILDR_EPHEMERAL_CI')),
         'EXPLICIT_INITIAL_MIGRATION_CI_CONTEXT_REQUIRED')
    need(RESULTS.is_dir() and not RESULTS.is_symlink() and not any(RESULTS.iterdir())
         and Path('/scratch').is_dir() and Path.cwd() == Path('/scratch')
         and not Path('/scratch/.env').exists(), 'FRESH_FIXED_OUTPUT_AND_CWD_REQUIRED')
    report = {'status': 'FAILED_ENTRY_OR_MIGRATION', 'migration_event_commit': args.expected_ops_commit,
              'application_commit': APP_COMMIT, 'application_tree': APP_TREE,
              'application_parent_commit': APP_PARENT, 'application_parent_tree': APP_PARENT_TREE,
              'baseline_commit': BASE_COMMIT, 'baseline_tree': BASE_TREE,
              'ci_run_id': run, 'ci_run_attempt': attempt,
              'accepted163_run_id': str(ACCEPTED_RUN), 'accepted163_job_id': ACCEPTED_JOB,
              'entry_sha256': sha(Path(__file__).read_bytes()),
              'migration_runner_sha256': PINS['run_migration_acceptance.py'],
              'migration_manifest_sha256': PINS['migration-source-manifest.json'],
              'stable_linux_inventory_matches_accepted163': False,
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
        strict = module('_accepted_inventory_only', 'accepted_services163.py')
        strict.bootstrap_sites()
        need(strict.available_mib() >= 600, 'INITIAL_MEMORY_BELOW_600_MIB')
        source_manifest = json.loads(document('accepted163-source.json'))
        checkout_manifest, checkout_manifest_pin = strict.load_manifest()
        migration_manifest = json.loads(document('migration-source-manifest.json'))
        need(checkout_manifest == source_manifest and checkout_manifest_pin == PINS['accepted163-source.json']
             and len(source_manifest['files']) == len(migration_manifest['candidate_lf_sha256']) == 204
             and migration_manifest['candidate_cached_head'] == APP_COMMIT
             and migration_manifest['candidate_tree'] == APP_TREE
             and {name: item['sha256'] for name, item in source_manifest['files'].items()}
                 == migration_manifest['candidate_lf_sha256']
             and set(migration_manifest['candidate_operational_sha256']) == set(source_manifest['operational_paths'])
             and set(migration_manifest['candidate_operational_sha256']) == SERVICES_OPERATIONS
             and migration_manifest['accepted_services163_source_sha256'] == PINS['accepted163-source.json']
             and migration_manifest['accepted_services163_root_proof_sha256'] == PINS['accepted163-proof.json']
             and migration_manifest['accepted_services163_independent_proof_sha256'] == ACCEPTED_INDEPENDENT_ORIGINAL_SHA,
             'EXACT_ACCEPTED204_SOURCE_AND_PINNED_SERVICES_OPERATIONS_REQUIRED')
        git_identity(SOURCE, APP_COMMIT, APP_TREE, APP_PARENT, APP_PARENT_TREE)
        git_identity(BASELINE, BASE_COMMIT, BASE_TREE)
        operational = strict.git_source(source_manifest, APP_COMMIT)
        need(operational == prior['operational_git_blobs'], 'ACCEPTED163_GIT_OPERATIONS_CHANGED')
        current = strict.linux_inventory(PINS['accepted163-source.json'], APP_COMMIT, operational)
        connection, version = strict.local_connection()
        connection.close()
        current['postgres_server_version_num'] = version
        need(len(current['packages']) == len(prior['packages']) == 62
             and all(current[key] == prior[key] for key in STABLE), 'LINUX_RUNTIME_OR_PACKAGE_BYTES_CHANGED')
        current.update(migration_event_commit=args.expected_ops_commit,
                       stable_linux_inventory_matches_accepted163=True)
        write('migration-linux-inventory.json', current)
        report['stable_linux_inventory_matches_accepted163'] = True
        migration = module('_reviewed_full52_migration', 'run_migration_acceptance.py')
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

"""Narrow source candidate: accepted registry128 Linux inventory, then current51 migrations.

No application server, registry128 suite, server or worker is started by this adapter.
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
APP_COMMIT = '4b2322686a4691f7b5a96654cac9a4fd5bcaf9b6'
APP_TREE = '26911f16dfe8b202a85fbc26020c2732f145865d'
BASE_COMMIT = '0811946ea02a310b03116960663e3ba6871a07d0'
BASE_TREE = '18b2087a31853a275c5c6ca791a68214b1cd38ef'
PINS = {
    'run_migration_acceptance.py': '495d52e030d941033ea51f1f3724f87d34fa188d62c4fef12b9ceb789c035d1a',
    'migration-source-manifest.json': 'f0183579d4dd3e3c3e28b7403d04ed1fd5b25e586bffa645b415b4a2310769ff',
    'accepted_registry128.py': '2b5345d2a134dda0f4d627e62cdc657ac8e03d0ff19b17c117a2a802242c5a4a',
    'accepted128-source.json': '68dc7d35ee451a78b89414c0fbdd55addea4be32daf144475bf569081bff1ed2',
    'accepted128-inventory.json': '5731a942e5a458afda131d9a0ce0769f1cbd69bbd07a53d2ca63a34331d830b4',
    'accepted128-result.json': '986cdf4c31c434a708e62fc35751bb0a24a00dddff7f063882e8aa1727d0a5ae',
    'accepted128-proof.json': '08bfe0c5e54f0404aeb708a1341ca3aa980a9b8c912bc223e3b8bba6c23d9971',
    'accepted128-independent.json': '490db68a9564984c18a1907e8d90f8648ded7803dd163fd851a37e149b5123c7',
    'registry.Dockerfile': '8ffaef790e6012746b1b77b1a6da96bac0bc79fa92d5cb47d8d6405036e6abc3',
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
    inventory = json.loads(document('accepted128-inventory.json'))
    result = json.loads(document('accepted128-result.json'))
    proof = json.loads(document('accepted128-proof.json'))
    peer = json.loads(document('accepted128-independent.json'))
    for value in (inventory, result, proof, peer):
        need(value.get('source_commit', value.get('commit')) == APP_COMMIT,
             'ACCEPTED128_SOURCE_IDENTITY_REQUIRED')
    need(proof['tree'] == peer['tree'] == APP_TREE
         and proof['run_id'] == peer['run_id'] == 41411621277
         and proof['job_id'] == peer['job_id'] == 125165734500
         and proof['attempt'] == peer['attempt'] == 1,
         'GENUINE_ACCEPTED128_RUN_REQUIRED')
    need(proof['status'] == 'PASS_ACTUAL_CURRENT_REGISTRY128_WITH_FIVE_SCHEMA_AND_OWNED_DOCKER_CLEANUP_NOT_FULL_CHAIN_OR_RELEASE'
         and peer['decision'] == 'INDEPENDENT_ACTUAL_CURRENT_REGISTRY128_RUNTIME_EVIDENCE_CONFIRMED_NOT_FULL_MIGRATION_OR_RELEASE'
         and result['status'] == 'PASS_REGISTRY_ASSERTIONS_AND_EXACT_SCHEMA_CLEANUP_PG_CONTAINER_CLEANUP_PENDING',
         'GENUINE_ACCEPTED128_STATUS_REQUIRED')
    assertions = result['assertions']
    need(type(assertions['tests_run']) is int and assertions['tests_run'] == 128
         and assertions['class_counts'] == {'CustomerDeliveryTests': 18, 'LeadDeliveryTests': 52,
             'PrivateImageContracts': 4, 'PrivateRegistryMigration': 5, 'PrivateRegistryRoutes': 16,
             'PropertyNetworkRoutes': 18, 'SignupNotificationTests': 15}
         and all(type(assertions[key]) is int and assertions[key] == 0 for key in
                 ('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'))
         and assertions['denied_guard_events'] == [] and assertions['failed_test_ids'] == []
         and result['tests_pass'] is True and result['exact_schemas_absent'] is True
         and result['source_unchanged'] is True and result['native_child_exit'] == 0
         and result['cleanup_errors'] == []
         and proof['all_five_exact_controlled_schemas_absent'] is True
         and peer['five_schema_uuid_hooks_and_parent_absence_verified'] is True
         and proof['native_child_client_container_exit'] == peer['native_child_client_container_exit'] == 0
         and proof['actual_container_not_running_and_notOOM'] is True
         and peer['container_running'] is False and peer['container_oom_killed'] is False
         and proof['owned_docker_cleanup_after_blocks_line'] == peer['owned_docker_cleanup_after_both_blocks_line']
         and proof['owned_docker_cleanup_after_blocks_line'] > max(item['end_line'] for item in proof['actual_blocks'].values())
         and proof['all_six_actual_job_steps_success'] is True and peer['six_actual_job_steps_success'] is True,
         'COMPLETE_ACCEPTED128_AND_CLEANUP_REQUIRED')
    need(inventory['source_manifest_sha256'] == result['source_manifest_sha256']
         == peer['source_binding_chain']['manifest'] == PINS['accepted128-source.json'],
         'ACCEPTED128_MANIFEST_BINDING_REQUIRED')
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
         and os.environ.get('GITHUB_REF') == 'refs/heads/codex-ci-registry-migration-20261102'
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
              'accepted128_run_id': '41411621277', 'accepted128_job_id': 125165734500,
              'entry_sha256': sha(Path(__file__).read_bytes()),
              'migration_runner_sha256': PINS['run_migration_acceptance.py'],
              'migration_manifest_sha256': PINS['migration-source-manifest.json'],
              'stable_linux_inventory_matches_accepted128': False,
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
        strict = module('_accepted_inventory_only', 'accepted_registry128.py')
        strict.bootstrap_sites()
        need(strict.available_mib() >= 600, 'INITIAL_MEMORY_BELOW_600_MIB')
        source_manifest = json.loads(document('accepted128-source.json'))
        migration_manifest = json.loads(document('migration-source-manifest.json'))
        need(len(source_manifest['files']) == len(migration_manifest['candidate_lf_sha256']) == 193
             and {name: item['sha256'] for name, item in source_manifest['files'].items()}
                 == migration_manifest['candidate_lf_sha256']
             and set(migration_manifest['candidate_operational_sha256']) == set(source_manifest['operational_paths'])
             and set(migration_manifest['candidate_operational_sha256']) ==
                 {'ci/registry.py', 'ci/registry-source.json', 'ci/registry.Dockerfile', '.github/workflows/registry.yml'},
             'EXACT_ACCEPTED193_SOURCE_AND_PINNED_REGISTRY_OPERATIONS_REQUIRED')
        git_identity(SOURCE, APP_COMMIT, APP_TREE)
        git_identity(BASELINE, BASE_COMMIT, BASE_TREE)
        operational = strict.git_source(source_manifest, APP_COMMIT)
        need(operational == prior['operational_git_blobs'], 'ACCEPTED128_GIT_OPERATIONS_CHANGED')
        current = strict.linux_inventory(PINS['accepted128-source.json'], APP_COMMIT, operational)
        connection, version = strict.local_connection()
        connection.close()
        current['postgres_server_version_num'] = version
        need(all(current[key] == prior[key] for key in STABLE), 'LINUX_RUNTIME_OR_PACKAGE_BYTES_CHANGED')
        current.update(migration_event_commit=args.expected_ops_commit,
                       stable_linux_inventory_matches_accepted128=True)
        write('migration-linux-inventory.json', current)
        report['stable_linux_inventory_matches_accepted128'] = True
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

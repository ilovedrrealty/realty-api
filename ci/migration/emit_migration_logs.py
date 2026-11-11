"""Validate bounded synthetic migration metadata before emitting two log documents."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

APP = 'e9750472783e909dddb7ade065ec8739188c8c71'
APP_TREE = '22f32bd033bd20f16b2da993b10aebe2d9d32051'
APP_PARENT = '3da654cefb806ba90a8e66078721611f2d2d57d7'
APP_PARENT_TREE = 'ec5f60b27a4aabfb8b1dd6de5b55da40879f0c8c'
BASE = '0811946ea02a310b03116960663e3ba6871a07d0'
BASE_TREE = '18b2087a31853a275c5c6ca791a68214b1cd38ef'
MANIFEST = 'f64c538b5baeb5fdd5fa87d2e52ed9638ea30297fc6d1aa76c7058a1cb783096'
RUNNER = '671b132034717c05c9e349d05316a3758d4034bb7649d8decd367ac58c26b00b'
ACCEPTED_MANIFEST = 'a4db349b759165d1bff20f99d8d53b3cbc20e6239e164bb9de6049338db6a637'
SERVICES_OPERATIONS = {'ci/services163.py', 'ci/services163-source.json', 'ci/services163.Dockerfile', '.github/workflows/services163.yml'}
BASELINE_IDS = set('actor other listing lead service_p1 service_p2 service_p3 service_outsider service_unverified service_unused'.split())
HEX = re.compile(r'[0-9a-f]{64}\Z')
DB = re.compile(r'ildr_migration_ci_[ab]_([0-9a-f]{32})\Z')
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
LEGACY = set('users pending_users listings upgrade_requests inquiries anonymous_leads listing_edits saved_homes bookings activity_log deal_requests listing_events leads settings bulk_import_jobs website_settings website_settings_revisions professional_profiles professional_profile_revisions research_workspaces'.split())
NEW = set('lead_delivery_jobs lead_delivery_worker_state listing_location_reviews listing_location_review_revisions property_management_records property_management_revisions network_projects network_project_members network_milestones network_project_updates network_project_comments social_network_profiles network_posts network_post_comments network_blocks network_media network_audit network_properties network_service_preferences network_service_requests network_service_recipients network_service_proposals network_service_actions'.split())
RETENTION_SEQUENCE = [
    'ORIGINAL50_EMPTY_SERVICE_REGISTRY', 'EMPTY_A4_TO_F3', 'POPULATED_F3_REFUSED',
    'A4_REUPGRADE_EMPTY_SERVICE', 'POPULATED_A4_REFUSED', 'HEAD_STARTUP_REPLAY',
]


def need(value):
    if not value:
        raise ValueError('MIGRATION_SAFE_BOUND_AGGREGATE_REQUIRED')


def keys(value, required, optional=()):
    need(isinstance(value, dict) and set(required) <= set(value) <= set(required) | set(optional))


def positive(value):
    return type(value) is int and 0 < value < 2**63


def safe(value):
    if isinstance(value, dict):
        return len(value) <= 500 and all(isinstance(k, str) and re.fullmatch(r'[A-Za-z0-9_./-]{1,120}', k) and safe(v) for k, v in value.items())
    if isinstance(value, list):
        return len(value) <= 500 and all(safe(v) for v in value)
    if isinstance(value, str):
        return len(value) <= 300 and re.fullmatch(r'[A-Za-z0-9_./ ()+:=,\-]*', value) is not None and '://' not in value
    return value is None or type(value) in (bool, int)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--results', type=Path, required=True)
    p.add_argument('--expected-ops-commit', required=True)
    p.add_argument('--run-id', required=True)
    p.add_argument('--client-exit', required=True)
    p.add_argument('--container-exit', required=True)
    args = p.parse_args()
    need(re.fullmatch(r'[0-9a-f]{40}', args.expected_ops_commit) and re.fullmatch(r'[1-9][0-9]{0,19}', args.run_id))
    need(args.results.is_dir() and not args.results.is_symlink())
    reports, hashes = {}, {}
    allowed = re.compile(r'(migration-entry|migration-result|migration-linux-inventory|migration-databases-[0-9a-f]{32}|a-fresh-[0-9a-f]{32}|b-baseline-[0-9a-f]{32}|b-upgrade-[0-9a-f]{32})\.json\Z')
    for path in args.results.iterdir():
        need(allowed.fullmatch(path.name) and path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 131072)
        raw = path.read_bytes()
        value = json.loads(raw)
        need(safe(value))
        reports[path.name], hashes[path.name] = value, hashlib.sha256(raw).hexdigest()
    entry = reports.get('migration-entry.json')
    entry_fields = set('status migration_event_commit application_commit application_tree application_parent_commit application_parent_tree baseline_commit baseline_tree ci_run_id ci_run_attempt accepted163_run_id accepted163_job_id entry_sha256 migration_runner_sha256 migration_manifest_sha256 stable_linux_inventory_matches_accepted163 migration_parent_native_exit outer_container_cleanup_verified_by_this_entry production_provider_or_server_actions'.split())
    keys(entry, entry_fields, {'failure_code'})
    need(entry['migration_event_commit'] == args.expected_ops_commit and entry['application_commit'] == APP
         and entry['application_tree'] == APP_TREE and entry['application_parent_commit'] == APP_PARENT
         and entry['application_parent_tree'] == APP_PARENT_TREE
         and entry['baseline_commit'] == BASE and entry['baseline_tree'] == BASE_TREE
         and entry['accepted163_run_id'] == '41457890694' and entry['accepted163_job_id'] == 125319170045
         and entry['ci_run_id'] == args.run_id and entry['ci_run_attempt'] == '1'
         and entry['migration_runner_sha256'] == RUNNER and entry['migration_manifest_sha256'] == MANIFEST
         and entry['outer_container_cleanup_verified_by_this_entry'] is False
         and entry['production_provider_or_server_actions'] is False and HEX.fullmatch(entry['entry_sha256']))
    inventory = reports.get('migration-linux-inventory.json')
    if inventory is not None:
        inventory_fields = set('scope source_commit source_manifest_sha256 operational_git_blobs python_version python_executable_sha256 git_executable_sha256 platform machine packages pillow_version pillow_imaging_sha256 webp_available webp_version requirements_sha256 windows_native_pins_reused bootstrap_and_full_migration_chain_proven postgres_server_version_num migration_event_commit stable_linux_inventory_matches_accepted163'.split())
        keys(inventory, inventory_fields)
        need(inventory['source_commit'] == APP and inventory['migration_event_commit'] == args.expected_ops_commit
             and inventory['source_manifest_sha256'] == ACCEPTED_MANIFEST
             and inventory['scope'] == 'LINUX_RUNTIME_OBSERVATION_NOT_RELEASE_ACCEPTANCE'
             and inventory['stable_linux_inventory_matches_accepted163'] is True
             and inventory['windows_native_pins_reused'] is False
             and inventory['bootstrap_and_full_migration_chain_proven'] is False
             and inventory['python_version'] == '3.11.18' and inventory['pillow_version'] == '10.4.0'
             and inventory['webp_available'] is True and inventory['webp_version'] == '1.4.0'
             and inventory['postgres_server_version_num'] == 170011)
        for name in ('python_executable_sha256', 'git_executable_sha256', 'pillow_imaging_sha256', 'requirements_sha256'):
            need(HEX.fullmatch(inventory[name]))
        need(set(inventory['operational_git_blobs']) == SERVICES_OPERATIONS)
        need(all(re.fullmatch(r'[0-9a-f]{40}', v) for v in inventory['operational_git_blobs'].values()))
        need(len(inventory['packages']) == 62)
        for package in inventory['packages']:
            keys(package, {'name', 'version', 'file_count', 'files_sha256'})
            need(re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', package['name']) and re.fullmatch(r'[A-Za-z0-9_.+!-]{1,100}', package['version'])
                 and positive(package['file_count']) and HEX.fullmatch(package['files_sha256']))
    result = reports.get('migration-result.json')
    if result is not None:
        keys(result, set('status scope baseline_commit target_revision source_manifest_sha256 runner_sha256 ci_run_id ci_run_attempt python_version phases cleanup_errors production_provider_or_server_actions databases_absent source_unchanged'.split()), {'fixture_identity', 'failure_code'})
        need(result['scope'] == 'SYNTHETIC_CI_BOOTSTRAP_AND_MIGRATIONS_ONLY'
             and result['source_manifest_sha256'] == MANIFEST and result['runner_sha256'] == RUNNER
             and result['ci_run_id'] == args.run_id and result['ci_run_attempt'] == '1'
             and result['baseline_commit'] == BASE
             and result['target_revision'] == 'a4b6c0d2e834' and result['production_provider_or_server_actions'] is False)
        if 'fixture_identity' in result:
            fixture = result['fixture_identity']
            keys(fixture, {'identity', 'server_version_num', 'anchor'})
            need(fixture['identity'] == ['ildr_platform_test_20261025', 'postgres', '127.0.0.1', 55449] and fixture['server_version_num'] == 170011)
            keys(fixture['anchor'], {'oid', 'datdba', 'marker'})
            need(positive(fixture['anchor']['oid']) and positive(fixture['anchor']['datdba'])
                 and fixture['anchor']['marker'] == 'ildr-ephemeral-ci:' + args.run_id + ':1')
        for phase in result['phases']:
            keys(phase, {'phase', 'proof', 'native_exit', 'child_reaped', 'timed_out'}, {'pid', 'sha256', 'child_result', 'terminate_error'})
            need(phase['phase'] in ('a-fresh', 'b-baseline', 'b-upgrade') and re.fullmatch(phase['phase'] + r'-[0-9a-f]{32}\.json', phase['proof']))
            if 'sha256' in phase:
                need(HEX.fullmatch(phase['sha256']) and hashes.get(phase['proof']) == phase['sha256'])
    journals = [name for name in reports if name.startswith('migration-databases-')]
    need(len(journals) <= 1)
    nonce = journals[0][len('migration-databases-'):-5] if journals else None
    for name, value in reports.items():
        if name in ('migration-entry.json', 'migration-result.json', 'migration-linux-inventory.json'):
            continue
        if name.startswith('migration-databases-'):
            keys(value, {'nonce', 'marker', 'databases'})
            need(value['nonce'] == nonce and value['marker'] == 'ildr-ephemeral-ci:' + args.run_id + ':1' and len(value['databases']) <= 2)
            for item in value['databases']:
                keys(item, {'name', 'oid', 'owner', 'marker', 'created', 'comment_applied'}, {'dropped'})
                need(DB.fullmatch(item['name']) and item['name'].endswith('_' + nonce)
                     and item['marker'] == 'ildr-owned-migration:' + nonce + ':' + item['name'][len('ildr_migration_ci_')])
                need(item['oid'] is None or positive(item['oid']))
                need(item['owner'] is None or positive(item['owner']))
            continue
        keys(value, {'status', 'database', 'mode', 'provider_or_application_lifespan_started'}, {
            'database_oid', 'failure_code', 'sqlstate', 'catalog_sha256', 'new_schema_sha256', 'new_schema_diagnostic',
            'baseline_ids', 'legacy_rows', 'legacy_schema_sha256', 'negative_operations_rejected',
            'preserved_synthetic_private_profiles', 'new_tables_empty_before_synthetic_inserts',
            'effective_data_api_roles_tested', 'rls_populated_tables_tested', 'retained_history_sha256',
            'source_graph_revision_count', 'pre_registry_original50_downgrade_reupgrade_preserved',
            'original_negative_operations_rejected', 'registry_negative_operations_rejected',
            'seeded_default_acl_grant_revocation_tables_tested', 'default_acl_seeded_before_new_tables',
            'registry_populated_downgrade_refused', 'registry_populated_downgrade_sqlstate', 'registry_post_refusal_head',
            'empty_service_downgrade_to_registry_preserved', 'service_negative_operations_rejected',
            'service_populated_downgrade_refused', 'service_populated_downgrade_sqlstate', 'service_post_refusal_head',
            'retention_sequence',
        })
        need(nonce and DB.fullmatch(value['database']) and value['database'].endswith('_' + nonce)
             and value['mode'] in ('fresh', 'baseline', 'upgrade') and value['provider_or_application_lifespan_started'] is False)
        if value['status'] == 'ACTUAL_PHASE_COMPLETED':
            need(type(value.get('source_graph_revision_count')) is int
                 and value['source_graph_revision_count'] == (45 if value['mode'] == 'baseline' else 52))
        if value['status'] == 'ACTUAL_PHASE_COMPLETED' and value['mode'] in ('fresh', 'upgrade'):
            need('new_schema_diagnostic' in value)
        if 'new_schema_diagnostic' in value:
            diagnostic = value['new_schema_diagnostic']
            keys(diagnostic, {'schema_version', 'scope', 'table_count', 'raw_schema_sha256', 'columns_sorted_schema_sha256', 'tables'})
            need(type(diagnostic['schema_version']) is int and diagnostic['schema_version'] == 1
                 and diagnostic['scope'] == 'FIXED23_SCHEMA_COMPONENT_HASHES_DIAGNOSTIC_ONLY'
                 and type(diagnostic['table_count']) is int and diagnostic['table_count'] == 23
                 and diagnostic['raw_schema_sha256'] == value.get('new_schema_sha256')
                 and HEX.fullmatch(diagnostic['raw_schema_sha256']) and HEX.fullmatch(diagnostic['columns_sorted_schema_sha256'])
                 and set(diagnostic['tables']) == NEW
                 and len(json.dumps(diagnostic, sort_keys=True).encode()) <= 32768)
            digest_fields = {'raw_table_sha256', 'columns_order_sha256', 'column_names_order_sha256', 'columns_by_name_sha256',
                             'columns_sorted_table_sha256', 'pk_sha256', 'fk_sha256', 'unique_sha256', 'checks_sha256', 'indexes_sha256'}
            for table_name, component in diagnostic['tables'].items():
                keys(component, {'column_count', 'user_id_ordinal'} | digest_fields)
                need(type(component['column_count']) is int and 0 < component['column_count'] <= 256
                     and all(HEX.fullmatch(component[key]) for key in digest_fields))
                if table_name == 'lead_delivery_jobs':
                    need(type(component['user_id_ordinal']) is int and 0 < component['user_id_ordinal'] <= component['column_count'])
                else:
                    need(component['user_id_ordinal'] is None)
        if 'baseline_ids' in value:
            keys(value['baseline_ids'], BASELINE_IDS)
            need(all(UUID.fullmatch(v) for v in value['baseline_ids'].values())
                 and len(set(value['baseline_ids'].values())) == len(BASELINE_IDS))
        if 'legacy_rows' in value:
            need(set(value['legacy_rows']) == LEGACY)
            for row in value['legacy_rows'].values():
                keys(row, {'rows', 'sha256'})
                need(type(row['rows']) is int and 0 <= row['rows'] <= 1000 and HEX.fullmatch(row['sha256']))
        for key in ('catalog_sha256', 'new_schema_sha256', 'legacy_schema_sha256', 'retained_history_sha256'):
            need(key not in value or HEX.fullmatch(value[key]))
    success = args.client_exit == args.container_exit == '0'
    if success:
        need(entry['status'] == 'PASS_ENTRY_INVENTORY_AND_ACTUAL_MIGRATION_DATABASE_CLEANUP_OUTER_CLEANUP_PENDING'
             and entry['stable_linux_inventory_matches_accepted163'] is True
             and type(entry['migration_parent_native_exit']) is int and entry['migration_parent_native_exit'] == 0
             and inventory is not None and result is not None and nonce)
        need(set(reports) == {'migration-entry.json', 'migration-linux-inventory.json', 'migration-result.json', journals[0]}
             | {kind + '-' + nonce + '.json' for kind in ('a-fresh', 'b-baseline', 'b-upgrade')})
        need(result['status'] == 'PASS_ACTUAL_CI_BOOTSTRAP_MIGRATIONS_AND_EXACT_DATABASE_CLEANUP'
             and result['databases_absent'] is True and result['source_unchanged'] is True and result['cleanup_errors'] == []
             and [p['phase'] for p in result['phases']] == ['a-fresh', 'b-baseline', 'b-upgrade'])
        need(all(type(p['native_exit']) is int and p['native_exit'] == 0 and p['child_reaped'] is True
                 and p['timed_out'] is False and positive(p['pid']) for p in result['phases']))
        databases = reports[journals[0]]['databases']
        need(len(databases) == 2 and {v['name'][len('ildr_migration_ci_')] for v in databases} == {'a', 'b'}
             and databases[0]['oid'] != databases[1]['oid']
             and all(v['created'] is True and v['comment_applied'] is True and v.get('dropped') is True for v in databases))
        database_by_kind = {item['name'][len('ildr_migration_ci_')]: item for item in databases}
        for phase in result['phases']:
            kind, mode = phase['phase'].split('-', 1)
            database = database_by_kind[kind]
            value = reports[phase['proof']]
            need(phase['proof'] == phase['phase'] + '-' + nonce + '.json'
                 and phase['sha256'] == hashes[phase['proof']] and phase.get('child_result') is None
                 and value['status'] == 'ACTUAL_PHASE_COMPLETED' and value['mode'] == mode
                 and value['database'] == database['name'] and value['database_oid'] == database['oid']
                 and positive(value['database_oid']))
        fresh = reports['a-fresh-' + nonce + '.json']
        baseline = reports['b-baseline-' + nonce + '.json']
        upgrade = reports['b-upgrade-' + nonce + '.json']
        need(set(baseline['baseline_ids']) == BASELINE_IDS and set(baseline['legacy_rows']) == LEGACY
             and HEX.fullmatch(baseline['legacy_schema_sha256']))
        need(upgrade['negative_operations_rejected'] == 148 and upgrade['original_negative_operations_rejected'] == 66
             and upgrade['registry_negative_operations_rejected'] == 35 and upgrade['service_negative_operations_rejected'] == 47
             and upgrade['preserved_synthetic_private_profiles'] == 46
             and upgrade['new_tables_empty_before_synthetic_inserts'] == upgrade['rls_populated_tables_tested'] == 23
             and upgrade['seeded_default_acl_grant_revocation_tables_tested'] == 23
             and upgrade['default_acl_seeded_before_new_tables'] is True
             and upgrade['pre_registry_original50_downgrade_reupgrade_preserved'] is True
             and upgrade['empty_service_downgrade_to_registry_preserved'] is True
             and upgrade['registry_populated_downgrade_refused'] is True
             and upgrade['registry_populated_downgrade_sqlstate'] == '55000'
             and upgrade['registry_post_refusal_head'] == 'f3a5b9c1d723'
             and upgrade['service_populated_downgrade_refused'] is True
             and upgrade['service_populated_downgrade_sqlstate'] == '55000'
             and upgrade['service_post_refusal_head'] == 'a4b6c0d2e834'
             and upgrade['retention_sequence'] == RETENTION_SEQUENCE
             and HEX.fullmatch(upgrade['retained_history_sha256'])
             and upgrade['effective_data_api_roles_tested'] == ['anon', 'authenticated']
             and fresh['new_schema_sha256'] == upgrade['new_schema_sha256']
             and fresh['catalog_sha256'] == upgrade['catalog_sha256'])
    bundle = {'status': 'ACTUAL_MIGRATION_PROOFS_VALIDATED_OUTER_CLEANUP_PENDING' if success else 'FAILED_OR_PARTIAL_MIGRATION_NO_ACCEPTANCE',
              'migration_event_commit': args.expected_ops_commit, 'ci_run_id': args.run_id, 'ci_run_attempt': '1',
              'original_file_sha256': hashes, 'proofs': {k: v for k, v in reports.items() if k != 'migration-linux-inventory.json'},
              'outer_docker_cleanup_verified_by_this_log_validator': False}
    outputs = {'migration-proof-bundle.json': bundle}
    if inventory is not None:
        outputs['migration-linux-inventory.json'] = inventory
    for name in sorted(outputs):
        payload = (json.dumps(outputs[name], ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + '\n').encode('ascii')
        need(0 < len(payload) <= 1048576 and max(map(len, payload.splitlines()), default=0) <= 8192)
        marker = f'file={name} commit={args.expected_ops_commit} run={args.run_id} attempt=1 sha256={hashlib.sha256(payload).hexdigest()} bytes={len(payload)}'
        print('MIGRATION_AGGREGATE_BEGIN ' + marker)
        sys.stdout.write(payload.decode('ascii'))
        print('MIGRATION_AGGREGATE_END ' + marker)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit('MIGRATION_LOG_METADATA_REFUSED_NO_ACCEPTANCE')

"""Two owned base64/envelope observations; never a backup acceptance."""
from contextlib import closing
from hashlib import sha256
import ast
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time

NEW, V5, STAGED, JSON_TOOLS = Path('/new-tools'), Path('/v5-tools'), Path('/staged-tools'), Path('/json-tools')
STAGED_REL = Path('outputs/property-network-20261101/backup-transport-stages-20261125')
JSON_REL = Path('outputs/property-network-20261101/backup-transport-json-20261125')
CASES = {'base64_once', 'full_json_envelope'}
V5_HELPER_SHA = 'b2565a1b76eec78df9252f3ab08d4b30bc2ee308c40982a29002459761b1d500'
STAGED_PINS = {
    'candidate/make-daily-compressed-query-staged-v1.py': '47691ecbf43a9ffecf5e9670573d2521281df386fa789d04dfba32de350bb7f5',
    'candidate/test-transport-staged-v1.py': 'a7fea81912e5cd3d520363364dffd228e9d0d098d29b4e8c5e6bc7d164e0158b',
    'verify-native.py': '23cdd8359c2c6ebdb712cc1ca89a3b51a2dadd853436e24ebdee429d28cb2cf5',
}
JSON_PINS = {
    'candidate/make-daily-compressed-query-json-v1.py': 'a964425d0f5ba6d76ab89ffb7ba78acbbae47df30b9bf6818c528915bf558c1c',
    'candidate/test-transport-json-v1.py': '1fdf9d584b2f6272e1f63ff2729ad7e3366c93fe523a3ad2e013155c0c34249c',
    'verify-native.py': 'a835e9b41edf7e29822ecca4bb67254b7cbf11701960bb702f1efd3b5995b78f',
}
native = None
stage = 'isolation'


class Refused(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def identity():
    for root in (NEW, V5, STAGED, JSON_TOOLS):
        require(root.resolve(strict=True) == root and os.statvfs(root).f_flag & os.ST_RDONLY,
            'readonly_component_and_frozen_sources_required')
    source = V5 / 'verify-native.py'
    require(source.is_file() and not source.is_symlink() and sha256(source.read_bytes()).hexdigest() == V5_HELPER_SHA,
        'exact_frozen_v5_native_helper_required')
    for name, digest in STAGED_PINS.items():
        path = STAGED / name
        require(path.is_file() and not path.is_symlink() and sha256(path.read_bytes()).hexdigest() == digest,
            'exact_frozen_staged_source_required')
    for name, digest in JSON_PINS.items():
        path = JSON_TOOLS / name
        require(path.is_file() and not path.is_symlink() and sha256(path.read_bytes()).hexdigest() == digest, 'exact_frozen_json_source_required')
    spec = importlib.util.spec_from_file_location('frozen_v5_packaging_diagnostic', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.NEW = V5
    old, nonce, workspace, names = module.identity('stress')
    destination = workspace / STAGED_REL
    destination.mkdir(parents=True, exist_ok=False)
    for name in ('make-daily-compressed-query-staged-v1.py', 'test-transport-staged-v1.py'):
        with (destination / name).open('xb') as stream:
            stream.write((STAGED / 'candidate' / name).read_bytes())
    destination = workspace / JSON_REL
    destination.mkdir(parents=True, exist_ok=False)
    for name in ('make-daily-compressed-query-json-v1.py', 'test-transport-json-v1.py'):
        with (destination / name).open('xb') as stream:
            stream.write((JSON_TOOLS / 'candidate' / name).read_bytes())
    return module, old, nonce, workspace, names


def constant(tree, name):
    matches = [node.value for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
    require(len(matches) == 1 and isinstance(matches[0], ast.Constant) and type(matches[0].value) is str,
        'exact_staged_source_constant_required')
    return matches[0].value


def component_query(inner, staged, source, case):
    """Derive exact source text only; never inspect/rewrite synthetic row values."""
    require(case in CASES, 'finite_cipher_component_required')
    tree = ast.parse(source)
    marker, options = constant(tree, 'MARKER'), constant(tree, 'OPTIONS')
    require(marker == 'ILDR-TRANSPORT-COMPRESSION-V1-NOT-A-RECOVERY-PASSPHRASE'
        and options == 'compress-algo=1,cipher-algo=aes256,s2k-mode=3,s2k-count=65536,unicode-mode=1',
        'unchanged_original_pgp_marker_options_required')
    clean = inner.strip().removesuffix(';')
    snapshot = 'WITH transport_snapshot AS MATERIALIZED (\n' + clean + '\n)'
    digest = (', transport_digest AS MATERIALIZED (\n'
        ' SELECT octet_length(recovery_snapshot::text) AS plaintext_bytes,\n'
        " encode(sha256(convert_to(recovery_snapshot::text,'UTF8')),'hex') AS plaintext_sha256\n"
        ' FROM transport_snapshot\n)')
    cipher_expression = "extensions.pgp_sym_encrypt(snapshot.recovery_snapshot::text,'" + marker + "','" + options + "') AS cipher_payload"
    cipher = (', transport_cipher AS MATERIALIZED (\n'
        ' SELECT digest.plaintext_bytes,digest.plaintext_sha256,\n'
        ' ' + cipher_expression + '\n'
        ' FROM transport_snapshot snapshot CROSS JOIN transport_digest digest\n)')
    envelope = (" SELECT json_build_object('transport','PGP_COMPRESSED_V1','plaintext_bytes',plaintext_bytes,"
        "'plaintext_sha256',plaintext_sha256,'payload_base64',encode(cipher_payload,'base64')) AS transport_result FROM transport_cipher;\n")
    # Reconstruct and prove the entire actual13ff SQL before either derivation.
    require(staged == snapshot + digest + cipher + envelope, 'exact_entire_json_transport_sql_required')
    if case == 'base64_once':
        # Keep the entire original snapshot/digest/cipher prefix. Materialize
        # exactly the producer's base64 expression once, then observe scalars.
        return (staged[:-len(envelope)] + ", transport_base64 AS MATERIALIZED (\n"
            " SELECT encode(cipher_payload,'base64') AS payload_base64 FROM transport_cipher\n)"
            " SELECT octet_length(payload_base64),encode(sha256(convert_to(payload_base64,'UTF8')),'hex') FROM transport_base64;\n")
    require(case == 'full_json_envelope', 'finite_packaging_component_required')
    # Preserve the complete13ff producer, including its JSON envelope. Cast
    # JSON to text only for scalar observation; never reparse it as JSONB.
    return ("WITH transport_envelope AS MATERIALIZED (\n" + staged.strip().removesuffix(';') +
        "\n) SELECT octet_length(transport_result::text),encode(sha256(convert_to(transport_result::text,'UTF8')),'hex') FROM transport_envelope;\n")


def read_component(database, query):
    try:
        with closing(native.connect(database)) as db, db.cursor() as cursor:
            cursor.execute('BEGIN READ ONLY')
            cursor.execute('SHOW transaction_read_only')
            require(cursor.fetchone() == ('on',), 'actual_readonly_component_transaction_required')
            cursor.execute(query)
            return cursor.fetchall()
    except Exception as error:
        native.capture_sqlstate(error)
        raise


def verify_native_bounds(workspace, database, names, job):
    measured = [native.rows(database, native.sql.SQL(
        'SELECT count(*),coalesce(sum(octet_length(to_jsonb(t)::text)),0) FROM public.{} t'
    ).format(native.sql.Identifier(name)))[0] for name in names]
    current = native.catalog(workspace, database)
    expected = json.loads((job / 'schema-catalog.json').read_text())
    require(all(count <= 1000000 for count, size in measured)
        and sum(int(size) for count, size in measured) <= 67108864
        and current['table_names'] == names == expected['table_names']
        and current['definition_sha256'] == expected['definition_sha256'],
        'all_four_native_snapshot_bound_conditions_true_required')


def run(case):
    global native, stage
    require(case in CASES, 'finite_cipher_case_required')
    native, old, nonce, workspace, names = identity()
    client_start, initial_peak = native.client_memory()
    require(native.rows('postgres', "SELECT current_setting('shared_buffers'),current_setting('work_mem'),current_setting('max_parallel_workers_per_gather'),current_setting('jit')")
        == [('128MB', '4MB', '0', 'off')], 'unchanged_declared_postgres_settings_required')
    started = native.rows('postgres', 'SELECT pg_postmaster_start_time()::text')[0][0]
    stage = 'stress_fixture'
    database, marker = native.create_fixture(nonce, 'stress', names)
    job = None
    try:
        native.seed_trade(database, 5633, 5640, (9317650, 8743189))
        native.seed_storage(database, 34315)
        require(native.rows(database, 'SELECT count(*),sum(octet_length(to_jsonb(t)::text)) FROM public.trade_contacts t') == [(5633, 9317650)]
            and native.rows(database, 'SELECT count(*),sum(octet_length(to_jsonb(t)::text)) FROM public.trade_contact_sources t') == [(5640, 8743189)]
            and native.rows(database, 'SELECT count(*) FROM storage.objects') == [(34315,)],
            'exact_pre_component_counts_and_bytes_required')
        before = native.state(workspace, database, names)
        stage = 'component_prepare'
        job, stamp = native.prepare(old, workspace, database, names, 1)
        verify_native_bounds(workspace, database, names, job)
        staged_path = job / 'transport-read-only.sql'
        generator = workspace / JSON_REL / 'make-daily-compressed-query-json-v1.py'
        old.native(generator, '--input', job / 'snapshot-read-only.sql', '--output', staged_path)
        inner, staged = (job / 'snapshot-read-only.sql').read_text(), staged_path.read_text()
        query = component_query(inner, staged, (workspace / STAGED_REL / 'make-daily-compressed-query-staged-v1.py').read_text(), case)
        stage = case + '_select'
        begin = time.monotonic()
        selected = read_component(database, query)
        elapsed = int((time.monotonic() - begin) * 1000)
        require(len(selected) == 1 and len(selected[0]) == 2 and type(selected[0][0]) is int
            and 0 < selected[0][0] <= 67108864 and type(selected[0][1]) is str
            and re.fullmatch(r'[a-f0-9]{64}', selected[0][1]), 'finite_packaging_length_sha_required')
        stage = 'post_state'
        require(native.state(workspace, database, names) == before, 'all99_rows_catalog_sequences_storage_unchanged_required')
        require(native.rows('postgres', 'SELECT pg_postmaster_start_time()::text')[0][0] == started,
            'unchanged_postmaster_start_required')
        original = json.loads((native.OLD / 'source-manifest.json').read_text())
        for name, digest in original['files'].items():
            require(sha256((workspace / name).read_bytes()).hexdigest() == digest, 'unchanged_original_mirror_required')
        for name in ('make-daily-full-public-query-v3.py', 'make-daily-full-public-query-v4.py', 'test-query-v4.py', 'make-daily-full-public-query-v5.py', 'test-query-v5.py'):
            require((workspace / native.V5_REL / name).read_bytes() == (V5 / 'candidate' / name).read_bytes(),
                'unchanged_v5_mirror_required')
        for name in ('make-daily-compressed-query-staged-v1.py', 'test-transport-staged-v1.py'):
            require((workspace / STAGED_REL / name).read_bytes() == (STAGED / 'candidate' / name).read_bytes(),
                'unchanged_staged_mirror_required')
        for name in ('make-daily-compressed-query-json-v1.py', 'test-transport-json-v1.py'):
            require((workspace / JSON_REL / name).read_bytes() == (JSON_TOOLS / 'candidate' / name).read_bytes(), 'unchanged_json_mirror_required')
        client_end, client_peak = native.client_memory()
        require(client_peak >= initial_peak, 'monotonic_client_peak_required')
        return {'status': 'OBSERVED', 'case': case, 'backup_accepted': False, 'postgres_version_num': 170006,
            'public_tables': 99, 'contacts': 5633, 'sources': 5640, 'contact_row_bytes': 9317650,
            'source_row_bytes': 8743189, 'storage_objects': 34315,
            'selected_bytes': selected[0][0], 'selected_sha256': selected[0][1],
            'derived_query_sha256': sha256(query.encode('utf8')).hexdigest(),
            'generated_query_sha256': sha256((job / 'snapshot-read-only.sql').read_bytes()).hexdigest(),
            'producer_query_sha256': sha256(staged_path.read_bytes()).hexdigest(), 'component_sql_ms': elapsed,
            'component_transaction_readonly': True, 'all_four_snapshot_guard_conditions_true': True, 'json_producer_source_exact': True,
            'server_start_unchanged': True, 'all99_rows_catalog_sequences_storage_unchanged': True,
            'frozen_sources_unchanged': True, 'production_data_accessed': False, 'external_network_available': False,
            'transport_receiver_finalizer_executed': False, 'owner_encryption_or_cloud_executed': False,
            'client_start_bytes': client_start, 'client_end_bytes': client_end,
            'client_whole_phase_peak_bytes': client_peak, 'client_oom_events': 0, 'client_oom_kill_events': 0}
    finally:
        if job is not None:
            (job / 'schema-catalog.json').unlink(missing_ok=True)
            require(all(not (job / name).exists() for name in ('schema-catalog.json', 'recovery-snapshot.json', 'transport-result.json')),
                'owned_raw_metadata_absence_required')
        native.drop_fixture(database, marker)


if __name__ == '__main__':
    case = sys.argv[1] if len(sys.argv) == 2 else 'invalid'
    try:
        result = run(case)
    except Exception as error:
        if native is not None:
            native.capture_sqlstate(error)
        kind = type(error).__name__
        result = {'status': 'FAIL', 'case': case if case in CASES else 'invalid', 'backup_accepted': False,
            'stage': stage, 'sqlstate': native.first_sqlstate if native is not None else 'unavailable',
            'error_type': kind if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', kind) else 'UnhandledError',
            'reason': str(error) if isinstance(error, (Refused, native.Refused if native is not None else Refused))
                and re.fullmatch(r'[a-z_0-9]{1,90}', str(error)) else 'unexpected_failure'}
    print('BACKUP_PACKAGING_COMPONENT_DIAGNOSTIC_RESULT ' + json.dumps(result, sort_keys=True, separators=(',', ':')), flush=True)
    raise SystemExit(0 if result['status'] == 'OBSERVED' else 1)

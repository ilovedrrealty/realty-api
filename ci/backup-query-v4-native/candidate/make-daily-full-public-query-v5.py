"""Prepare an atomic bounded snapshot with canonical row and payload text.

This version is a separate compatibility candidate. It never connects to a
database and does not modify the frozen helpers or the approved v2 generator.
Native equivalence and resource-pressure acceptance are required before use.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re


HERE = Path(__file__).resolve().parent
FROZEN = HERE.parents[1] / 'website-growth-20261022' / 'backups'
V2 = HERE.parent / 'backup-query-compatibility-20261118' / 'make-daily-full-public-query-v2.py'
V2_SHA256 = 'b2dd42e123ffe718a0759dd9b9000e6e2e197b50c6ba6135801903ec28a05658'
if hashlib.sha256(V2.read_bytes()).hexdigest() != V2_SHA256:
    raise ValueError('Approved v2 reference changed')
V3_REFERENCE = HERE / 'make-daily-full-public-query-v3.py'
if hashlib.sha256(V3_REFERENCE.read_bytes()).hexdigest() != '15dfcfff1630e377fab399067efa249186f8c7aadb50ec1890b0213bd67e0768':
    raise ValueError('Immutable v3 reference changed')
V4_REFERENCE = HERE / 'make-daily-full-public-query-v4.py'
if hashlib.sha256(V4_REFERENCE.read_bytes()).hexdigest() != '831bf05fa10e7cb3ad387cfdf89991ea5fa64245560b68487a1370866b26f8b3':
    raise ValueError('Immutable v4 reference changed')

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--catalog', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
catalog = json.loads(args.catalog.read_text(encoding='utf-8-sig'))
if catalog.get('project_ref') != 'pznmfxejanwxtsopxxrp':
    raise ValueError('Unexpected project')
names = sorted(catalog['table_names'])
if not re.fullmatch('[a-f0-9]{64}', catalog.get('definition_sha256', '')):
    raise ValueError('Definition fingerprint missing; refresh schema catalog')
if any(catalog.get(key) for key in ['materialized_views', 'foreign_tables', 'partitioned_or_partition_tables']):
    raise ValueError('Unsupported public data object; require a reviewed native capture')
if (not names or len(names) != len(set(names))
        or not all(re.fullmatch('[a-z_][a-z0-9_]*', name) for name in names)):
    raise ValueError('Unexpected catalog identifier')
required = {'users', 'leads', 'listings', 'listing_edits', 'listing_events',
            'professional_profiles', 'professional_profile_revisions',
            'website_settings', 'website_settings_revisions'}
if required - set(names):
    raise ValueError('Required existing application tables missing')
plan_path = args.output.parent / 'backup-plan.json'
plan = None
if plan_path.is_file():
    plan = json.loads(plan_path.read_text(encoding='utf-8-sig'))
    if plan['status'] != 'AWAITING_SCHEMA_CATALOG':
        raise ValueError('Unexpected daily plan state')


def table(name):
    return 'public."' + name + '"'


ctes, guard_rows, payload_rows = [], [], []
for number, name in enumerate(names):
    guard = f'ildr_guard_{number:03d}'
    payload = f'ildr_payload_{number:03d}'
    # The guard keeps only counts/byte lengths. The full sorted payload is a
    # separate lazy CTE, referenced only by the safe branch below.
    ctes.append(f"{guard} AS MATERIALIZED (SELECT count(*) AS records, "
                f"coalesce(sum(octet_length(to_jsonb(t)::text)),0) AS record_bytes FROM {table(name)} t)")
    # Preserve v2's exact row representation, ordering and per-table hash input.
    # Match jsonb_agg(... ORDER BY row::text)::text byte for byte while
    # avoiding its expanded JSONB transition state for the complete table.
    # Each row keeps PostgreSQL's own JSONB text; only the array delimiters
    # and the canonical comma-space separator are assembled as text.
    ctes.append(f"{payload} AS MATERIALIZED (SELECT "
                f"'[' || coalesce(string_agg(row_text, ', ' ORDER BY row_text),'') || ']' AS payload "
                f"FROM (SELECT to_jsonb(t)::text AS row_text FROM {table(name)} t) canonical_rows)")
    guard_rows.append(f"SELECT '{name}'::text AS name, records, record_bytes FROM {guard}")
    payload_rows.append(f"SELECT '{name}'::text AS name, payload FROM {payload}")

ctes.append("ildr_counts AS MATERIALIZED (SELECT "
            "jsonb_object_agg(name,records ORDER BY name) AS counts, "
            "bool_and(records <= 1000000) AS row_bounds, sum(record_bytes) AS total_record_bytes "
            "FROM (" + '\nUNION ALL\n'.join(guard_rows) + ") guards)")
ctes.append("ildr_payload_maps AS MATERIALIZED (SELECT "
            "'{' || string_agg(to_json(name)::text || ':' || to_json(payload)::text, ',' ORDER BY name) || '}' "
            "AS payload_map_text, "
            "jsonb_object_agg(name,encode(sha256(convert_to(payload,'UTF8')),'hex') ORDER BY name) AS hashes "
            "FROM (" + '\nUNION ALL\n'.join(payload_rows) + ") payloads)")

catalog_sql = (FROZEN / 'daily-schema-catalog-read-only.sql').read_text(encoding='utf-8-sig').strip().removesuffix(';')
ctes.append('ildr_catalog AS MATERIALIZED (SELECT (' + catalog_sql + ') AS data)')
expected = 'ARRAY[' + ','.join("'" + name + "'" for name in names) + ']::text[]'
checks = [
    '(SELECT row_bounds FROM ildr_counts)',
    '(SELECT total_record_bytes FROM ildr_counts) <= 67108864',
    "(SELECT array_agg(tablename::text ORDER BY tablename) FROM pg_tables WHERE schemaname='public')=" + expected,
    "(SELECT data->>'definition_sha256' FROM ildr_catalog)='" + catalog['definition_sha256'] + "'",
]
storage = """jsonb_build_object(
 'objects',(SELECT count(*) FROM storage.objects),
 'inventory_sha256',(SELECT encode(sha256(convert_to('[' || coalesce(string_agg(jsonb_build_object('bucket',bucket_id,'name',name,'metadata',metadata,'updated_at',updated_at)::text, ', ' ORDER BY bucket_id,name),'') || ']','UTF8')),'hex') FROM storage.objects),
 'latest_updated_at',(SELECT max(updated_at) FROM storage.objects),
 'latest_created_at',(SELECT max(created_at) FROM storage.objects),
 'buckets',(SELECT count(*) FROM storage.buckets),
 'bucket_configuration_sha256',(SELECT encode(sha256(convert_to(coalesce(jsonb_agg(to_jsonb(b) ORDER BY b.id),'[]'::jsonb)::text,'UTF8')),'hex') FROM storage.buckets b),
 'object_bytes_included',false)"""
metadata = """jsonb_build_object(
 'status','COMPLETE_PUBLIC_DATA_SNAPSHOT',
 'captured_at_utc',clock_timestamp(),
 'project_ref','pznmfxejanwxtsopxxrp',
 'scope','Every public application table, current schema catalog, storage metadata fingerprints; storage bytes and provider-managed schemas are not included',
 'public_table_record_counts',(SELECT counts FROM ildr_counts),
 'public_table_payload_sha256',(SELECT hashes FROM ildr_payload_maps),
 'full_public_schema_catalog',(SELECT data FROM ildr_catalog),
 'storage_inventory_state',""" + storage + ')::text'
ctes.append('ildr_metadata AS MATERIALIZED (SELECT ' + metadata + ' AS data)')
fallback = "jsonb_build_object('status','EXCEEDS_SAFE_BOUNDS_OR_SCHEMA_CHANGED','private_records_returned',false,'public_table_record_counts',(SELECT counts FROM ildr_counts))::text"
# Keep the large map as text, avoiding the large repeated JSONB map merges used
# by v2. to_json(text) quotes each payload safely; the receiver parses JSON as
# before. No row content, value, hash input, table or budget is omitted.
body = "(SELECT left(data,length(data)-1) || ',\"public_table_payloads\":' || (SELECT payload_map_text FROM ildr_payload_maps) || '}' FROM ildr_metadata)"
ctes.append('snapshot_candidate AS MATERIALIZED (SELECT CASE WHEN ' + ' AND '.join(checks)
            + ' THEN ' + body + ' ELSE ' + fallback + ' END AS recovery_snapshot)')
query = ('-- One read-only MVCC snapshot. Private full payloads; never print them.\nWITH '
         + ',\n'.join(ctes)
         + "\nSELECT CASE WHEN octet_length(recovery_snapshot)<=67108864 THEN recovery_snapshot "
           "ELSE jsonb_build_object('status','EXCEEDS_SAFE_BOUNDS_OR_SCHEMA_CHANGED',"
           "'private_records_returned',false)::text END AS recovery_snapshot FROM snapshot_candidate;\n")

args.output.parent.mkdir(parents=True, exist_ok=True)
with args.output.open('x', encoding='utf-8', newline='\n') as stream:
    stream.write(query)
report = {
    'status': 'PREPARED', 'query_version': 'v5_text_rows_maps_storage_inventory',
    'included_public_tables': names, 'research_workspaces_table_present': 'research_workspaces' in names,
    'raw_record_budget_bytes': 67108864, 'single_statement_snapshot': True,
    'every_table_has_record_count_and_payload_sha256': True,
    'storage_metadata_fingerprint_and_count_included': True,
    'fails_instead_of_truncating': True, 'no_credentials_required': True,
    'query_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
    'reference_v2_sha256': V2_SHA256,
    'full_text_is_valid_JSON_required_by_unchanged_receiver_and_finalizer': True,
}
args.output.with_suffix('.preparation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
if plan is not None:
    plan.update(status='PREPARED_NOT_BACKED_UP', query_sha256=report['query_sha256'],
                included_public_tables=names, query_version=report['query_version'])
    plan_path.write_text(json.dumps(plan, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2))

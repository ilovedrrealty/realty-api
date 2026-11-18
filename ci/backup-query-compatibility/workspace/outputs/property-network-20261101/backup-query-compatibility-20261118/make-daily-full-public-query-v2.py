"""Prepare one bounded atomic snapshot; versioned compatibility for more than 50 public tables."""
import argparse,hashlib,json,re
from pathlib import Path
OUT=Path(__file__).resolve().parents[2]/'website-growth-20261022'/'backups'
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--catalog',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
catalog=json.loads(a.catalog.read_text(encoding='utf-8-sig'))
if catalog.get('project_ref')!='pznmfxejanwxtsopxxrp':raise ValueError('Unexpected project')
names=sorted(catalog['table_names'])
if not re.fullmatch('[a-f0-9]{64}',catalog.get('definition_sha256','')):raise ValueError('Definition fingerprint missing; refresh schema catalog')
if any(catalog.get(key) for key in ['materialized_views','foreign_tables','partitioned_or_partition_tables']):raise ValueError('Unsupported public data object detected; require a reviewed native database capture instead of silently omitting rows')
if not names or not all(re.fullmatch('[a-z_][a-z0-9_]*',name) for name in names):raise ValueError('Unexpected catalog identifier')
required={'users','leads','listings','listing_edits','listing_events','professional_profiles','professional_profile_revisions','website_settings','website_settings_revisions'}
if required-set(names):raise ValueError('Required existing application tables missing')
def table(name):return 'public."'+name+'"'
def count(name):return '(SELECT count(*) FROM '+table(name)+')'
def payload(name):return "(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb)::text FROM "+table(name)+' t)'
# PostgreSQL permits at most 100 function arguments; each entry uses two.
# Disjoint 32-entry chunks preserve exactly the same jsonb maps in one statement.
def object_map(entries):
 return '('+' || '.join('jsonb_build_object('+',\n'.join(entries[start:start+32])+')' for start in range(0,len(entries),32))+')'
counts=object_map(["'"+name+"',"+count(name) for name in names])
payloads=object_map(["'"+name+"',"+payload(name) for name in names])
hashes=object_map(["'"+name+"',encode(sha256(convert_to("+payload(name)+",'UTF8')),'hex')" for name in names])
checks=[count(name)+' <= 1000000' for name in names]
expected='ARRAY['+','.join("'"+name+"'" for name in names)+']::text[]'
checks.append("(SELECT array_agg(tablename::text ORDER BY tablename) FROM pg_tables WHERE schemaname='public')="+expected)
size=' + '.join('(SELECT coalesce(sum(octet_length(to_jsonb(t)::text)),0) FROM '+table(name)+' t)' for name in names)
checks.append('('+size+') <= 67108864')
catalog_expr='('+(OUT/'daily-schema-catalog-read-only.sql').read_text(encoding='utf-8-sig').strip().removesuffix(';')+')'
checks.append("("+catalog_expr+" ->> 'definition_sha256')='"+catalog['definition_sha256']+"'")
storage="""jsonb_build_object(
 'objects',(SELECT count(*) FROM storage.objects),
 'inventory_sha256',(SELECT encode(sha256(convert_to(coalesce(jsonb_agg(jsonb_build_object('bucket',bucket_id,'name',name,'metadata',metadata,'updated_at',updated_at) ORDER BY bucket_id,name),'[]'::jsonb)::text,'UTF8')),'hex') FROM storage.objects),
 'latest_updated_at',(SELECT max(updated_at) FROM storage.objects),
 'latest_created_at',(SELECT max(created_at) FROM storage.objects),
 'buckets',(SELECT count(*) FROM storage.buckets),
 'bucket_configuration_sha256',(SELECT encode(sha256(convert_to(coalesce(jsonb_agg(to_jsonb(b) ORDER BY b.id),'[]'::jsonb)::text,'UTF8')),'hex') FROM storage.buckets b),
 'object_bytes_included',false)"""
query="""-- One read-only SQL statement. All public data/schema and storage fingerprints use the same MVCC snapshot.
-- Full table payloads stay private; never print them. If bounds or table set changed, return no records.
SELECT CASE WHEN """+' AND '.join(checks)+" THEN jsonb_build_object(\n 'status','COMPLETE_PUBLIC_DATA_SNAPSHOT',\n 'captured_at_utc',clock_timestamp(),\n 'project_ref','pznmfxejanwxtsopxxrp',\n 'scope','Every public application table, current schema catalog, storage metadata fingerprints; storage bytes and provider-managed schemas are not included',\n 'public_table_payloads',"+payloads+",\n 'public_table_record_counts',"+counts+",\n 'public_table_payload_sha256',"+hashes+",\n 'full_public_schema_catalog',"+catalog_expr+",\n 'storage_inventory_state',"+storage+"\n) ELSE jsonb_build_object('status','EXCEEDS_SAFE_BOUNDS_OR_SCHEMA_CHANGED','private_records_returned',false,'public_table_record_counts',"+counts+") END AS recovery_snapshot;\n"
a.output.parent.mkdir(parents=True,exist_ok=True)
query='WITH snapshot_candidate AS (\n'+query.strip().removesuffix(';')+"\n) SELECT CASE WHEN octet_length(recovery_snapshot::text)<=67108864 THEN recovery_snapshot ELSE jsonb_build_object('status','EXCEEDS_SAFE_BOUNDS_OR_SCHEMA_CHANGED','private_records_returned',false) END AS recovery_snapshot FROM snapshot_candidate;\n"
with a.output.open('x',encoding='utf-8',newline='\n') as stream:stream.write(query)
report={'status':'PREPARED','included_public_tables':names,'research_workspaces_table_present':'research_workspaces' in names,'raw_record_budget_bytes':67108864,'single_statement_snapshot':True,'every_table_has_record_count_and_payload_sha256':True,'storage_metadata_fingerprint_and_count_included':True,'fails_instead_of_truncating':True,'no_credentials_required':True,'query_sha256':hashlib.sha256(a.output.read_bytes()).hexdigest()}
a.output.with_suffix('.preparation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
plan_path=a.output.parent/'backup-plan.json'
if plan_path.is_file():
 plan=json.loads(plan_path.read_text(encoding='utf-8-sig'))
 if plan['status']!='AWAITING_SCHEMA_CATALOG':raise ValueError('Unexpected daily plan state')
 plan.update(status='PREPARED_NOT_BACKED_UP',query_sha256=report['query_sha256'],included_public_tables=names)
 plan_path.write_text(json.dumps(plan,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))

"""Validate every public-table payload/hash, seal it, and remove plaintext on all outcomes."""
import argparse,datetime,hashlib,json,re,subprocess,sys
from pathlib import Path
OUT=Path(__file__).resolve().parent
sha=lambda value:hashlib.sha256(value).hexdigest()
def finalize(job,stamp):
 read=lambda name:json.loads((job/name).read_text(encoding='utf-8-sig'))
 plan=read('backup-plan.json');data=read('recovery-snapshot.json')
 if plan['stamp_utc']!=stamp or plan['status']!='PREPARED_NOT_BACKED_UP':raise ValueError('Unexpected backup plan')
 if sha((job/'snapshot-read-only.sql').read_bytes())!=plan['query_sha256']:raise ValueError('Snapshot query changed')
 if sha((job/plan['transport_query_file']).read_bytes())!=plan['transport_query_sha256']:raise ValueError('Transport query changed')
 if read('transport-verification.json')['recovered_snapshot_sha256']!=sha((job/'recovery-snapshot.json').read_bytes()):raise ValueError('Transport recovery hash mismatch')
 if data.get('status')!='COMPLETE_PUBLIC_DATA_SNAPSHOT':raise ValueError('Atomic snapshot incomplete, over safe bounds or changed schema; no partial success is accepted')
 if data['project_ref']!=plan['project_ref']:raise ValueError('Unexpected project')
 if (job/'recovery-snapshot.json').stat().st_size>67108864:raise ValueError('Whole snapshot exceeds the safe response bound')
 if data['full_public_schema_catalog']['definition_sha256']!=read('schema-catalog.json')['definition_sha256']:raise ValueError('Full schema definition fingerprint changed')
 payloads=data['public_table_payloads'];counts=data['public_table_record_counts'];hashes=data['public_table_payload_sha256']
 names=set(data['full_public_schema_catalog']['table_names'])
 if set(payloads)!=set(counts) or set(payloads)!=set(hashes) or set(payloads)!=set(plan['included_public_tables']) or set(payloads)!=names:raise ValueError('Public-table coverage mismatch')
 if not {'users','leads','listings','listing_events','professional_profiles'}.issubset(names):raise ValueError('Required public data missing')
 for name,payload in payloads.items():
  if sha(payload.encode())!=hashes[name]:raise ValueError('A public-table payload failed SHA256 verification')
  if len(json.loads(payload))!=counts[name]:raise ValueError('A public-table payload count is incomplete')
 profiles=json.loads(payloads['professional_profiles'])
 if any(p['published_data'] is not None or p.get('published_at') is not None or p['listing_status']=='published' for p in profiles):raise ValueError('Professional publication detected; do not change data, notify the owner')
 state=data['storage_inventory_state'];baseline_path=OUT/'storage-inventory-baseline.json'
 baseline=json.loads(baseline_path.read_text(encoding='utf-8-sig')) if baseline_path.is_file() else None
 media_matches=baseline is not None and all(state[key]==baseline['storage_inventory_state'][key] for key in ['objects','inventory_sha256','buckets','bucket_configuration_sha256'])
 validation={'status':'PASS','public_table_record_counts':counts,'public_table_payload_sha256':hashes,'all_public_tables_included':True,'customer_values_logged':False,'atomic_single_SQL_snapshot':True,'research_workspace_rows_included':'research_workspaces' in names,'snapshot_sha256':sha((job/'recovery-snapshot.json').read_bytes()),'all_profiles_private':True,'storage_inventory_state':state,'storage_metadata_matches_verified_baseline':media_matches,'possible_media_change_needs_owner_verification':not media_matches,'media_byte_equality_claimed_by_metadata_hash':False,'media_bytes_copied_by_daily_job':False}
 (job/'snapshot-validation.json').write_text(json.dumps(validation,indent=2),encoding='utf-8')
 cmd=[sys.executable,str(OUT/'save-growth-checkpoint.py'),'--phase','daily','--daily-stamp',stamp]
 for label in ['frontend','backend']:
  repo=plan['repositories'][label];cmd+=['--'+label+'-repo',repo['repo'],'--'+label+'-ref',repo['ref'],'--'+label+'-commit',repo['commit']]
 for name in ['backup-plan.json','snapshot-read-only.sql',plan['transport_query_file'],'transport-verification.json','recovery-snapshot.json','schema-catalog.json','catalog-read-only.sql','snapshot-read-only.preparation.json','snapshot-validation.json']:
  cmd+=['--include',name]
 subprocess.run(cmd,check=True)

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stamp',required=True);a=p.parse_args()
 if not re.fullmatch(r'\d{8}T\d{6}Z',a.stamp):raise ValueError('Invalid stamp')
 datetime.datetime.strptime(a.stamp,'%Y%m%dT%H%M%SZ')
 job=OUT/'daily'/a.stamp
 try:finalize(job,a.stamp)
 finally:
  for filename in ['recovery-snapshot.json','schema-catalog.json','transport-result.json']:
   target=(job/filename).resolve()
   if target.parent!=job.resolve() or OUT.resolve() not in target.parents:raise ValueError('Plain snapshot cleanup target rejected')
   target.unlink(missing_ok=True)
if __name__=='__main__':
 try:main()
 except Exception as exc:
  print('Daily backup stopped safely ('+type(exc).__name__+'). Plaintext cleanup was attempted; verify temporary files are absent before retrying. No cloud success is recorded.')
  raise SystemExit(1)

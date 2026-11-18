"""Recover a bounded connector snapshot privately; never print customer records."""
import argparse,base64,datetime,hashlib,json,re,sys,warnings
from pathlib import Path
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
MARKER='ILDR-TRANSPORT-COMPRESSION-V1-NOT-A-RECOVERY-PASSPHRASE'
sys.path.insert(0,str(ROOT/'work/backup-transport-deps'))
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--stamp',required=True);a=p.parse_args()
 if not re.fullmatch(r'\d{8}T\d{6}Z',a.stamp):raise ValueError('Invalid stamp')
 job=OUT/'daily'/a.stamp;path=job/'transport-result.json'
 recovered_ok=False
 try:
  data=json.loads(path.read_text(encoding='utf-8-sig'))
  if data['transport']!='PGP_COMPRESSED_V1' or not 0<data['plaintext_bytes']<=67108864:raise ValueError('Transport bounds invalid')
  blob=base64.b64decode(data['payload_base64'])
  if len(blob)>32*1024*1024:raise ValueError('Compressed transport exceeds safe limit')
  from pgpy import PGPMessage
  value=PGPMessage.from_blob(blob).decrypt(MARKER).message
  plain=value.encode('utf-8') if isinstance(value,str) else bytes(value)
  # PGP text packets from older pgcrypto versions can be labelled Latin-1.
  # Accept an alternate decoding only if the server's exact SHA256/length matches.
  if isinstance(value,str) and (len(plain)!=data['plaintext_bytes'] or hashlib.sha256(plain).hexdigest()!=data['plaintext_sha256']):
   try:alternate=value.encode('latin-1')
   except UnicodeEncodeError:alternate=b''
   if len(alternate)==data['plaintext_bytes'] and hashlib.sha256(alternate).hexdigest()==data['plaintext_sha256']:plain=alternate
  if len(plain)!=data['plaintext_bytes'] or hashlib.sha256(plain).hexdigest()!=data['plaintext_sha256']:raise ValueError('Recovered snapshot SHA256/size mismatch')
  recovered=json.loads(plain)
  if recovered.get('status')!='COMPLETE_PUBLIC_DATA_SNAPSHOT':raise ValueError('Incomplete or unsafe snapshot; no partial success accepted')
  with (job/'recovery-snapshot.json').open('xb') as stream:stream.write(plain)
  report={'status':'PASS','transport':'PGP_COMPRESSED_V1','compressed_bytes':len(blob),'recovered_snapshot_bytes':len(plain),'recovered_snapshot_sha256':data['plaintext_sha256'],'private_key_accessed':False,'owner_passphrase_used':False,'customer_values_logged':False,'transport_file_removed':True}
  (job/'transport-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
  recovered_ok=True
  print(json.dumps(report,indent=2))
 finally:
  if path.resolve().parent!=job.resolve() or OUT.resolve() not in path.resolve().parents:raise ValueError('Transport cleanup target rejected')
  path.unlink(missing_ok=True)
  if not recovered_ok:
   for filename in ['recovery-snapshot.json','schema-catalog.json']:
    failed=(job/filename).resolve()
    if failed.parent!=job.resolve() or OUT.resolve() not in failed.parents:raise ValueError('Failed recovery cleanup target rejected')
    failed.unlink(missing_ok=True)
if __name__=='__main__':
 try:main()
 except Exception as exc:
  print('Compressed backup transport stopped safely ('+type(exc).__name__+'); private-file cleanup was attempted. Verify temporary files are absent. No customer fields were printed.')
  raise SystemExit(1)

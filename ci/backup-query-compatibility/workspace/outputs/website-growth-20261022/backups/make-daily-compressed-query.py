"""Wrap the atomic SQL snapshot in compressed PGP transport to fit connector response limits.
The public constant is packaging only; owner public-key encryption protects the saved backup.
"""
import argparse,hashlib,json
from pathlib import Path
MARKER='ILDR-TRANSPORT-COMPRESSION-V1-NOT-A-RECOVERY-PASSPHRASE'
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
inner=a.input.read_text(encoding='utf-8-sig').strip().removesuffix(';')
query="WITH transport_snapshot AS (\n"+inner+"\n) SELECT jsonb_build_object('transport','PGP_COMPRESSED_V1','plaintext_bytes',octet_length(recovery_snapshot::text),'plaintext_sha256',encode(sha256(convert_to(recovery_snapshot::text,'UTF8')),'hex'),'payload_base64',encode(extensions.pgp_sym_encrypt(recovery_snapshot::text,'"+MARKER+"','compress-algo=1,cipher-algo=aes256,s2k-mode=3,s2k-count=65536,unicode-mode=1'),'base64')) AS transport_result FROM transport_snapshot;\n"
with a.output.open('x',encoding='utf-8',newline='\n') as stream:stream.write(query)
plan_path=a.output.parent/'backup-plan.json';plan=json.loads(plan_path.read_text(encoding='utf-8-sig'))
plan.update(transport_query_file=a.output.name,transport_query_sha256=hashlib.sha256(a.output.read_bytes()).hexdigest(),transport_encoding='PGP_COMPRESSED_V1',transport_encoding_is_not_backup_encryption=True)
plan_path.write_text(json.dumps(plan,indent=2),encoding='utf-8')
print('PASS: compressed transport query prepared; owner public-key archive encryption remains unchanged.')

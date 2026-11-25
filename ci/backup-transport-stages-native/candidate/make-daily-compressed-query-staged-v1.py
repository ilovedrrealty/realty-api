"""Separate staged PGP transport candidate; frozen recovery helpers stay unchanged."""
import argparse
import hashlib
import json
from pathlib import Path

MARKER = "ILDR-TRANSPORT-COMPRESSION-V1-NOT-A-RECOVERY-PASSPHRASE"
OPTIONS = "compress-algo=1,cipher-algo=aes256,s2k-mode=3,s2k-count=65536,unicode-mode=1"
FROZEN = Path(__file__).resolve().parent.parents[1] / "website-growth-20261022" / "backups"
REFERENCE = FROZEN / "make-daily-compressed-query.py"
REFERENCE_SHA = "a2050dacc7f118b45e495bbba63f9ddc8a694528e427e2e6682585d49b4f1202"
if hashlib.sha256(REFERENCE.read_bytes()).hexdigest() != REFERENCE_SHA:
    raise ValueError("Frozen transport reference changed")

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--input", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
inner = args.input.read_text(encoding="utf-8-sig").strip().removesuffix(';')
query = ("WITH transport_snapshot AS MATERIALIZED (\n" + inner +
    "\n), transport_digest AS MATERIALIZED (\n"
    " SELECT octet_length(recovery_snapshot::text) AS plaintext_bytes,\n"
    " encode(sha256(convert_to(recovery_snapshot::text,'UTF8')),'hex') AS plaintext_sha256\n"
    " FROM transport_snapshot\n"
    "), transport_cipher AS MATERIALIZED (\n"
    " SELECT digest.plaintext_bytes,digest.plaintext_sha256,\n"
    " extensions.pgp_sym_encrypt(snapshot.recovery_snapshot::text,'" + MARKER + "','" + OPTIONS + "') AS cipher_payload\n"
    " FROM transport_snapshot snapshot CROSS JOIN transport_digest digest\n"
    ") SELECT jsonb_build_object('transport','PGP_COMPRESSED_V1','plaintext_bytes',plaintext_bytes,"
    "'plaintext_sha256',plaintext_sha256,'payload_base64',encode(cipher_payload,'base64')) AS transport_result FROM transport_cipher;\n")
with args.output.open("x", encoding="utf8", newline="\n") as stream:
    stream.write(query)
plan_path = args.output.parent / "backup-plan.json"
plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
plan.update(transport_query_file=args.output.name,
    transport_query_sha256=hashlib.sha256(args.output.read_bytes()).hexdigest(),
    transport_encoding="PGP_COMPRESSED_V1", transport_encoding_is_not_backup_encryption=True)
plan_path.write_text(json.dumps(plan, indent=2), encoding="utf8")
print("PASS: staged transport candidate prepared; this does not establish backup acceptance.")

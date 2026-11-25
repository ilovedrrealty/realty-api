"""Bounded public file/interface controls for the separate staged wrapper; no SQL."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parent
SOURCE = BASE / "make-daily-compressed-query-staged-v1.py"
FROZEN = BASE.parents[1] / "website-growth-20261022" / "backups" / "make-daily-compressed-query.py"
TEMP_ROOT = BASE / "pure-staged-checks"
TEMP_ROOT.mkdir(exist_ok=True)


class StagedTransportContracts(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="owned-", dir=TEMP_ROOT))
        self.assertTrue(self.folder.resolve().is_relative_to(TEMP_ROOT.resolve()))
        self.stage = self.folder / "outputs/property-network-20261101/backup-transport-stages-20261125"
        self.frozen = self.folder / "outputs/website-growth-20261022/backups/make-daily-compressed-query.py"
        self.stage.mkdir(parents=True)
        self.frozen.parent.mkdir(parents=True)
        self.script = self.stage / SOURCE.name
        self.script.write_bytes(SOURCE.read_bytes())
        self.frozen.write_bytes(FROZEN.read_bytes())
        self.input = self.stage / "snapshot.sql"
        self.input.write_text("\ufeffSELECT '{\"fixture\":\"José 🏡\"}'::text AS recovery_snapshot;\n", encoding="utf8")
        self.output = self.stage / "transport.sql"
        self.plan_path = self.stage / "backup-plan.json"
        self.plan = {"status": "PREPARED_NOT_BACKED_UP", "repositories": {"synthetic": "unchanged"}, "query_sha256": "a" * 64}
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf8")

    def tearDown(self):
        target = self.folder.resolve()
        if target == TEMP_ROOT.resolve() or not target.is_relative_to(TEMP_ROOT.resolve()):
            raise AssertionError("Refusing cleanup outside owned public test folder")
        shutil.rmtree(target)

    def execute(self):
        return subprocess.run([sys.executable, "-I", "-B", str(self.script), "--input", str(self.input),
            "--output", str(self.output)], capture_output=True, timeout=10)

    def test_exact_stages_preserve_input_receipt_options_and_plan_identity(self):
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        query = self.output.read_text(encoding="utf8")
        inner = self.input.read_text(encoding="utf-8-sig").strip().removesuffix(';')
        self.assertIn("WITH transport_snapshot AS MATERIALIZED (\n" + inner + "\n)", query)
        for name in ("transport_snapshot", "transport_digest", "transport_cipher"):
            self.assertEqual(query.count(name + " AS MATERIALIZED ("), 1)
        self.assertIn("FROM transport_snapshot snapshot CROSS JOIN transport_digest digest", query)
        cipher = query.split("transport_cipher AS MATERIALIZED (\n", 1)[1].split(") SELECT jsonb_build_object", 1)[0]
        self.assertNotIn("snapshot.*", cipher)
        self.assertNotIn("SELECT snapshot.recovery_snapshot", cipher)
        self.assertIn("compress-algo=1,cipher-algo=aes256,s2k-mode=3,s2k-count=65536,unicode-mode=1", query)
        self.assertIn("ILDR-TRANSPORT-COMPRESSION-V1-NOT-A-RECOVERY-PASSPHRASE", query)
        self.assertTrue(query.endswith("AS transport_result FROM transport_cipher;\n"))
        saved = json.loads(self.plan_path.read_text())
        self.assertEqual({key: saved[key] for key in self.plan}, self.plan)
        self.assertEqual(saved["transport_query_sha256"], hashlib.sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual(saved["transport_encoding"], "PGP_COMPRESSED_V1")
        self.assertTrue(saved["transport_encoding_is_not_backup_encryption"])

    def test_output_create_only_refuses_replacement_without_changing_plan(self):
        self.assertEqual(self.execute().returncode, 0)
        before = self.output.read_bytes(), self.plan_path.read_bytes()
        self.assertNotEqual(self.execute().returncode, 0)
        self.assertEqual((self.output.read_bytes(), self.plan_path.read_bytes()), before)

    def test_reference_tamper_in_owned_copy_refuses_before_output(self):
        before = self.plan_path.read_bytes()
        self.frozen.write_bytes(self.frozen.read_bytes() + b"\n# owned tamper control\n")
        self.assertNotEqual(self.execute().returncode, 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.plan_path.read_bytes(), before)

    def test_missing_input_refuses_before_output_or_plan_change(self):
        before = self.plan_path.read_bytes()
        self.input.unlink()
        self.assertNotEqual(self.execute().returncode, 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.plan_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

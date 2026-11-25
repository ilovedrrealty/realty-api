"""Four bounded public file/interface controls for the JSON envelope; no SQL."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parent
REFERENCE = BASE.parent / "backup-transport-stages-20261125/make-daily-compressed-query-staged-v1.py"
FROZEN = BASE.parents[1] / "website-growth-20261022/backups/make-daily-compressed-query.py"
TEMP_ROOT = BASE / "pure-json-checks"
TEMP_ROOT.mkdir(exist_ok=True)


class JsonTransportContracts(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="owned-", dir=TEMP_ROOT))
        self.assertTrue(self.folder.resolve().is_relative_to(TEMP_ROOT.resolve()))
        self.stage = self.folder / "outputs/property-network-20261101/backup-transport-json-20261125"
        self.previous = self.stage.parent / "backup-transport-stages-20261125/make-daily-compressed-query-staged-v1.py"
        self.frozen = self.folder / "outputs/website-growth-20261022/backups/make-daily-compressed-query.py"
        for path in (self.stage, self.previous.parent, self.frozen.parent):
            path.mkdir(parents=True)
        self.script = self.stage / "make-daily-compressed-query-json-v1.py"
        self.script.write_bytes((BASE / self.script.name).read_bytes())
        self.previous.write_bytes(REFERENCE.read_bytes())
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

    def execute(self, script=None, output=None):
        return subprocess.run([sys.executable, "-I", "-B", str(script or self.script), "--input", str(self.input),
            "--output", str(output or self.output)], capture_output=True, timeout=10)

    def test_only_final_function_changes_and_preserves_prefix_options_and_plan(self):
        self.assertEqual(self.execute().returncode, 0)
        current = self.output.read_bytes()
        current_plan = json.loads(self.plan_path.read_text())
        self.assertEqual({key: current_plan[key] for key in self.plan}, self.plan)
        self.assertEqual(current_plan["transport_query_file"], self.output.name)
        self.assertEqual(current_plan["transport_query_sha256"], sha256(current).hexdigest())
        old_output = self.stage / "old-transport.sql"
        self.assertEqual(self.execute(self.previous, old_output).returncode, 0)
        original = old_output.read_bytes()
        before = b") SELECT jsonb_build_object('transport','PGP_COMPRESSED_V1',"
        after = b") SELECT json_build_object('transport','PGP_COMPRESSED_V1',"
        self.assertEqual(original.count(before), 1)
        self.assertEqual(current, original.replace(before, after, 1))
        for name in (b"transport_snapshot", b"transport_digest", b"transport_cipher"):
            self.assertEqual(current.count(name + b" AS MATERIALIZED ("), 1)
        self.assertIn(b"SELECT '{\"fixture\":\"Jos\xc3\xa9 \xf0\x9f\x8f\xa1\"}'::text AS recovery_snapshot", current)
        self.assertIn(b"compress-algo=1,cipher-algo=aes256,s2k-mode=3,s2k-count=65536,unicode-mode=1", current)
        saved = json.loads(self.plan_path.read_text())
        self.assertEqual({key: saved[key] for key in self.plan}, self.plan)
        self.assertEqual(saved["transport_query_sha256"], sha256(original).hexdigest())
        self.assertEqual(saved["transport_encoding"], "PGP_COMPRESSED_V1")
        self.assertTrue(saved["transport_encoding_is_not_backup_encryption"])

    def test_create_only_refuses_replacement_without_changing_plan(self):
        self.assertEqual(self.execute().returncode, 0)
        before = self.output.read_bytes(), self.plan_path.read_bytes()
        self.assertNotEqual(self.execute().returncode, 0)
        self.assertEqual((self.output.read_bytes(), self.plan_path.read_bytes()), before)

    def test_staged_reference_tamper_refuses_before_output_or_plan_change(self):
        before = self.plan_path.read_bytes()
        self.previous.write_bytes(self.previous.read_bytes() + b"\n# owned tamper control\n")
        self.assertNotEqual(self.execute().returncode, 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.plan_path.read_bytes(), before)

    def test_frozen_reference_tamper_refuses_before_output_or_plan_change(self):
        before = self.plan_path.read_bytes()
        self.frozen.write_bytes(self.frozen.read_bytes() + b"\n# owned tamper control\n")
        self.assertNotEqual(self.execute().returncode, 0)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.plan_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()

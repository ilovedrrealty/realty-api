"""Bounded generator input/file-contract checks; no SQL execution or private data."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

BASE = Path(__file__).resolve().parent
TEMP_ROOT = BASE / 'pure-checks'
TEMP_ROOT.mkdir(exist_ok=True)
SCRIPT = BASE / 'make-daily-full-public-query-v3.py'
REQUIRED = ['users', 'leads', 'listings', 'listing_edits', 'listing_events',
            'professional_profiles', 'professional_profile_revisions',
            'website_settings', 'website_settings_revisions']


class GeneratorContracts(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='owned-', dir=TEMP_ROOT))
        self.assertTrue(self.folder.resolve().is_relative_to(TEMP_ROOT.resolve()))

    def tearDown(self):
        target = self.folder.resolve()
        if not target.is_relative_to(TEMP_ROOT.resolve()) or target == TEMP_ROOT.resolve():
            raise AssertionError('Refusing cleanup outside owned test folder')
        shutil.rmtree(target)

    def catalog(self, **updates):
        value = {'project_ref': 'pznmfxejanwxtsopxxrp',
                 'definition_sha256': 'a' * 64,
                 'table_names': REQUIRED + [f'fixture_{n:03d}' for n in range(90)],
                 'materialized_views': [], 'foreign_tables': [],
                 'partitioned_or_partition_tables': []}
        value.update(updates)
        return value

    def run_generator(self, catalog, name='candidate.sql'):
        source = self.folder / 'catalog.json'
        source.write_text(json.dumps(catalog), encoding='utf8')
        output = self.folder / name
        result = subprocess.run([sys.executable, '-I', str(SCRIPT), '--catalog', str(source),
                                 '--output', str(output)], capture_output=True, timeout=10)
        return result, output

    def test_complete_99_table_generation_and_create_only_output(self):
        result, output = self.run_generator(self.catalog())
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        report = json.loads(output.with_suffix('.preparation.json').read_text())
        self.assertEqual(report['included_public_tables'], sorted(self.catalog()['table_names']))
        self.assertEqual(report['raw_record_budget_bytes'], 67108864)
        self.assertTrue(report['single_statement_snapshot'])
        before = output.read_bytes()
        self.assertEqual(report['query_sha256'], hashlib.sha256(before).hexdigest())
        again, _ = self.run_generator(self.catalog())
        self.assertNotEqual(again.returncode, 0)
        self.assertEqual(output.read_bytes(), before)

    def test_unsafe_or_ambiguous_catalogs_do_not_create_sql(self):
        cases = [self.catalog(project_ref='another_project'),
                 self.catalog(definition_sha256="'; SELECT 'unsafe'"),
                 self.catalog(table_names=REQUIRED + ['unsafe";select']),
                 self.catalog(table_names=REQUIRED + ['users']),
                 self.catalog(table_names=REQUIRED[:-1])]
        for number, value in enumerate(cases):
            with self.subTest(number=number):
                result, output = self.run_generator(value, f'bad-{number}.sql')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())

    def test_unsupported_objects_fail_instead_of_omitting_them(self):
        for key in ['materialized_views', 'foreign_tables', 'partitioned_or_partition_tables']:
            with self.subTest(key=key):
                result, output = self.run_generator(self.catalog(**{key: ['unsupported']}), key + '.sql')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())

    def test_plan_state_is_validated_before_writing_and_identity_is_preserved(self):
        plan_path = self.folder / 'backup-plan.json'
        sealed = {'status': 'SEALED', 'repositories': {'fixture': 'unchanged'}}
        sealed_bytes = json.dumps(sealed).encode()
        plan_path.write_bytes(sealed_bytes)
        result, output = self.run_generator(self.catalog())
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertEqual(plan_path.read_bytes(), sealed_bytes)
        ready = {'status': 'AWAITING_SCHEMA_CATALOG', 'repositories': sealed['repositories']}
        plan_path.write_text(json.dumps(ready))
        result, output = self.run_generator(self.catalog())
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        saved = json.loads(plan_path.read_text())
        self.assertEqual(saved['repositories'], ready['repositories'])
        self.assertEqual(saved['status'], 'PREPARED_NOT_BACKED_UP')
        self.assertEqual(saved['query_sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
        self.assertNotIn('cloud_uploaded', saved)


if __name__ == '__main__':
    unittest.main()

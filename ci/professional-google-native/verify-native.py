"""Focused real-JWT Google profile contracts in an owned loopback Postgres."""
from contextlib import ExitStack, closing, redirect_stderr, redirect_stdout
import ast
import hashlib
import importlib
import importlib.metadata
import io
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

APP = Path('/application')
COMMIT = 'd9cfc94a1ebc45b8f3c391a69cbb8d0d8b25d26b'
TREE = '4a00441d18cf55c3e26414d1a7c2b49f791ee33d'
HEAD = 'c4e2a7319b60'
HELPERS = {'catalog.py': '5869dcbd30dc659a649fc6c2bbbe2de1337904ef11e4b91d6ad1992ef7ea9c2f',
           'records.py': 'bffdf8900f286079807ba43e8db5c09160caaf139b1e934449b90e577c1cb5cf'}
SUITES = (('test_professional_google_links', 'ProfessionalGoogleLinksTest', 7),
          ('test_professionals', 'ProfessionalTests', 31))
PROFILE = 'https://www.google.com/maps/place/Synthetic+Google+Business/'
EMBED = 'https://www.google.com/maps/embed?pb=!1m2!1m1!1d1'
KEYS = ('RESEND_API_KEY', 'GHL_API_KEY', 'GHL_LOCATION_ID', 'GHL_WEBHOOK_SECRET',
        'SUPABASE_SERVICE_KEY', 'PAYPAL_CLIENT_ID', 'PAYPAL_CLIENT_SECRET', 'PAYPAL_WEBHOOK_ID')
stage = 'isolation'
reports, attempts = [], []


class Refused(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def refused(*args, **kwargs):
    attempts.append(1)
    raise Refused('external_transport_refused')


def query(database, statement, params=()):
    import psycopg2
    with closing(psycopg2.connect(host='127.0.0.1', port=55449, user='postgres', password='',
                 dbname=database, connect_timeout=4, options='-cstatement_timeout=10000')) as db, db.cursor() as cursor:
        cursor.execute(statement, params)
        return cursor.fetchall()


def identity():
    require(sys.platform == 'linux' and sys.version_info[:2] == (3, 11)
            and os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('GITHUB_REPOSITORY') == 'ilovedrrealty/realty-api'
            and {p.name for p in Path('/sys/class/net').iterdir()} == {'lo'}, 'hosted_loopback_required')
    nonce = os.environ.get('ILDR_PROFESSIONAL_GOOGLE_CI_NONCE', '')
    require(re.fullmatch(r'[a-f0-9]{32}', nonce), 'owned_nonce_required')
    database = 'ildr_professional_google_' + nonce
    require(os.environ.get('DATABASE_URL') == 'postgresql+psycopg2://postgres@127.0.0.1:55449/' + database
            and all(not os.environ.get(key) for key in KEYS), 'owned_configuration_required')
    for path in (APP, Path('/tools'), Path('/google-tools'), Path('/rollback')):
        require(path.resolve(strict=True) == path and os.statvfs(path).f_flag & os.ST_RDONLY
                and not (path / '.env').exists() and not (path / '.env').is_symlink(), 'immutable_source_required')
    def git(*args):
        return subprocess.check_output(['git', '--no-optional-locks', '-c', 'safe.directory=/application',
            '-C', '/application', *args], timeout=15, stderr=subprocess.DEVNULL,
            env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1',
                 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0'}).decode().strip()
    require(git('rev-parse', 'HEAD', 'HEAD^{tree}').splitlines() == [COMMIT, TREE]
            and not git('status', '--porcelain=v1', '--untracked-files=all'), 'exact_source_required')
    for name, digest in HELPERS.items():
        require(hashlib.sha256((Path('/tools') / name).read_bytes()).hexdigest() == digest,
                'accepted_helper_bytes_required')
    manifest = json.loads(Path('/rollback-manifest.json').read_text())
    require(manifest['base'] == '4e23cc8b643a4b07f4e14042e530e6271dad9c8f'
            and manifest['overlay'] == ['schemas/professional_google.py', 'schemas/professionals.py', 'utils/professional_google.py'],
            'rollback_recipe_required')
    files = {p.relative_to('/rollback').as_posix(): p for p in Path('/rollback').rglob('*') if p.is_file()}
    require(set(files) == set(manifest['files']) and not any(p.is_symlink() for p in Path('/rollback').rglob('*'))
            and all(hashlib.sha256(files[name].read_bytes()).hexdigest() == digest
                    for name, digest in manifest['files'].items()), 'rollback_exact_source_required')
    require(query(database, """SELECT current_database(),host(inet_server_addr()),inet_server_port(),
        current_setting('server_version_num')::int,shobj_description(oid,'pg_database'),
        NOT EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public')
        FROM pg_database WHERE datname=current_database()""") == [(database, '127.0.0.1', 55449, 170006,
        'ildr-professional-google-ci:' + nonce, True)], 'fresh_marked_database_required')
    baseline = json.loads(Path('/opt/directory-map/baseline-inventory.json').read_text())
    normalize = lambda name: re.sub(r'[-_.]+', '-', name.lower())
    inventory = {normalize(d.metadata['Name']): d.version for d in importlib.metadata.distributions()}
    readers = {'openpyxl': '3.1.5', 'defusedxml': '0.7.1', 'et-xmlfile': '2.0.0'}
    require(set(inventory) - set(baseline) == set(readers)
            and all(inventory.get(k) == v for k, v in {**baseline, **readers}.items()), 'exact_inventory_required')
    return database


def bootstrap(database):
    for args in (('-c', "import runpy,sys;sys.path.insert(0,'/application');runpy.run_path('/application/bootstrap_db.py',run_name='__main__')"),
                 ('-m', 'alembic', 'upgrade', 'head')):
        result = subprocess.run([sys.executable, '-I', '-B', *args], cwd=APP, timeout=150,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        require(result.returncode == 0 and len(result.stdout) <= 4 * 1024 * 1024, 'schema_bootstrap_required')
    require(query(database, 'SELECT version_num FROM alembic_version') == [(HEAD,)]
            and query(database, "SELECT count(*) FROM pg_tables WHERE schemaname='public'") == [(99,)],
            'unchanged_99_head_required')


def authenticated_client(root):
    sys.path[:0] = [root]
    from database import SessionLocal
    from fastapi.testclient import TestClient
    from main import app
    from models.user import User
    from utils.jwt import create_access_token
    from records import IDS
    with SessionLocal() as db:
        headers = {role: {'Authorization': 'Bearer ' + create_access_token(db.get(User, identity))}
                   for role, identity in IDS.items()}
    return TestClient(app), headers


def compatibility_seed():
    client, headers = authenticated_client('/application')
    data = {'business_name': 'Synthetic Google rollback', 'contact_name': 'Synthetic business',
        'category_ids': ['property_law'], 'custom_services': [], 'service_area_ids': ['sosua'],
        'descriptions': {'en': 'Synthetic Google rollback description', 'es': ''}, 'languages': ['en'],
        'public_phone': '+18095550101', 'public_email': '', 'public_whatsapp': '', 'website_url': '',
        'photo_url': '', 'public_contact_consent': True, 'google_profile_url': PROFILE, 'google_maps_embed_url': EMBED}
    def call(method, path, role, body, status=200):
        response = client.request(method, path, headers=headers[role], json=body)
        require(response.status_code == status, 'new_google_compatibility_seed_required')
        return response.json()
    identities = []
    for managed in (False, True):
        private = call('POST', '/admin/professionals', 'admin', {'draft': data}, 201) if managed else call(
            'PUT', '/professional-profile', 'buyer', {'expected_version': 0, 'draft': data})
        submit_path = f"/admin/professionals/{private['id']}/submit" if managed else '/professional-profile/submit'
        pending = call('POST', submit_path, 'admin' if managed else 'buyer', {'expected_version': private['version']})
        approved = call('POST', f"/admin/professionals/{private['id']}/approve", 'admin',
                        {'expected_version': pending['version'], 'note': 'Synthetic compatibility approval'})
        identities.append({'id': private['id'], 'version': approved['version'], 'managed': managed})
    client.close()
    return identities


def rollback_reads():
    logging.disable(logging.CRITICAL)
    sys.path[:0] = ['/tools', '/rollback']
    import httpx, requests, resend, smtplib, urllib.request
    with ExitStack() as stack:
        for target, name in ((requests.sessions.Session, 'request'), (httpx.HTTPTransport, 'handle_request'),
            (httpx.AsyncHTTPTransport, 'handle_async_request'), (resend.Emails, 'send'),
            (urllib.request, 'urlopen'), (smtplib.SMTP, 'connect')):
            stack.enter_context(patch.object(target, name, refused))
        client, headers = authenticated_client('/rollback')
        from database import SessionLocal
        from models.professional_profile import ProfessionalProfile
        with SessionLocal() as db:
            targets = db.query(ProfessionalProfile).filter(ProfessionalProfile.draft_data['google_profile_url'].astext == PROFILE).all()
            require(len(targets) == 2, 'extended_snapshot_targets_required')
            identities = [(str(row.id), row.version, row.user_id is None) for row in targets]
        reads = 0
        for identity, version, managed in identities:
            path = f'/admin/professionals/{identity}' if managed else '/professional-profile'
            response = client.get(path, headers=headers['admin' if managed else 'buyer'])
            require(response.status_code == 200 and response.json()['version'] == version
                    and response.json()['draft']['google_profile_url'] == PROFILE
                    and response.json()['draft']['google_maps_embed_url'] == EMBED, 'rollback_saved_google_read_required')
            reads += 1
            public = client.get('/professionals/' + identity, headers=headers['owner'])
            require(public.status_code == 200 and public.json()['google_profile_url'] == ''
                    and public.json()['google_maps_embed_url'] == '', 'rollback_safe_public_defaults_required')
            require(client.get('/professionals/' + identity).status_code == 401, 'rollback_guest_denied_required')
            reads += 2
        client.close()
    require(not attempts, 'no_external_attempts_required')
    print('PROFESSIONAL_GOOGLE_ROLLBACK_RESULT ' + json.dumps({'status': 'PASS', 'actual_reads': reads,
        'saved_extended_snapshots': 2, 'writes': 0, 'external_provider_attempts': 0}, sort_keys=True))


class SafeResult(unittest.TestResult):
    def addFailure(self, test, error):
        self.failures.append((test, type(error[1]).__name__))

    def addError(self, test, error):
        self.errors.append((test, type(error[1]).__name__))


def run():
    global stage
    database = identity()
    os.environ.update(ENVIRONMENT='development', COOKIE_DOMAIN='', ALLOWED_ORIGINS='http://localhost:5174',
        JWT_SECRET='synthetic-professional-google-native-secret', LANDING_URL='http://127.0.0.1:8917',
        LEAD_DELIVERY_ENABLED='false', CUSTOMER_MESSAGES_ENABLED='false', GHL_ENABLED='false',
        PROPERTY_INQUIRY_REQUIRE_CONSENT='false', EMAIL_FROM='synthetic@example.com', NOTIFY_EMAIL='',
        SUPABASE_URL='', TRANSACTION_DOCUMENT_BUCKET='', SEARCH_FILTER_TEST_DATABASE_URL=os.environ['DATABASE_URL'])
    logging.disable(logging.CRITICAL)
    stage = 'bootstrap'
    bootstrap(database)
    sys.path[:0] = ['/application', '/application/tests', '/tools']
    from catalog import catalog, snapshot, table_names
    from records import seed_old
    from config import settings
    from database import engine, SessionLocal
    from sqlalchemy.engine import make_url
    from main import app
    import httpx, requests, resend, smtplib, urllib.request
    require(engine.url == make_url(os.environ['DATABASE_URL']) and SessionLocal.kw['bind'] is engine
            and not settings.lead_delivery_enabled and not settings.customer_messages_enabled
            and not settings.ghl_enabled and not app.dependency_overrides, 'real_runtime_required')
    with ExitStack() as stack:
        for target, name in ((requests.sessions.Session, 'request'), (httpx.HTTPTransport, 'handle_request'),
            (httpx.AsyncHTTPTransport, 'handle_async_request'), (resend.Emails, 'send'),
            (urllib.request, 'urlopen'), (smtplib.SMTP, 'connect')):
            stack.enter_context(patch.object(target, name, refused))
        stage = 'retained_fixture'
        retained = seed_old()
        require(retained == {'users': 4, 'leads': 3, 'owner_contacts': 82, 'contacts': 85, 'sources': 86,
                'hidden_profiles': 46, 'accepted_jobs': 9, 'worker_rows': 1, 'cms_version': 7, 'floorplan_rows': 2},
                'retained_fixture_required')
        compatibility_seed()
        tables = table_names(database)
        require(len(tables) == 99, 'all_99_tables_required')
        before_catalog, before_rows = catalog(database, tables), snapshot(database, tables)
        stage = 'contracts'
        for module_name, class_name, expected_count in SUITES:
            module_path = APP / 'tests' / (module_name + '.py')
            source = ast.parse(module_path.read_text())
            counts = [sum(isinstance(f, ast.FunctionDef) and f.name.startswith('test_') for f in node.body)
                      for node in source.body if isinstance(node, ast.ClassDef) and node.name == class_name]
            require(counts == [expected_count], 'exact_ast_contract_inventory_required')
            with patch.dict(os.environ, dict(os.environ), clear=True):
                module = importlib.import_module(module_name)
                suite = unittest.defaultTestLoader.loadTestsFromTestCase(getattr(module, class_name))
                require(suite.countTestCases() == expected_count, 'exact_runtime_contract_inventory_required')
                result = SafeResult()
                suite.run(result)
            diagnostics = []
            for test, error_type in result.failures + result.errors:
                name = test.id().rsplit('.', 1)[-1]
                diagnostics.append({'test': name if re.fullmatch(r'test_[a-z_]+', name) else 'setup_or_cleanup',
                    'error_type': error_type if re.fullmatch(r'[A-Za-z_]{1,80}', error_type) else 'Exception'})
            diagnostics.extend({'test': test.id().rsplit('.', 1)[-1], 'error_type': 'SkipTest'} for test, _ in result.skipped)
            reports.append({'module': module_name, 'class': class_name, 'expected': expected_count,
                'run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                'skipped': len(result.skipped), 'expected_failures': len(result.expectedFailures),
                'unexpected_successes': len(result.unexpectedSuccesses), 'diagnostics': diagnostics})
        stage = 'compatible_rollback'
        result = subprocess.run([sys.executable, '-I', '-B', '/google-tools/verify-native.py', '--rollback'],
            cwd='/rollback', stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        lines = result.stdout.splitlines()
        require(result.returncode == 0 and len(result.stdout) < 16384 and len(lines) == 1
                and lines[0].startswith(b'PROFESSIONAL_GOOGLE_ROLLBACK_RESULT '), 'compatible_rollback_reads_required')
        rollback = json.loads(lines[0].split(b' ', 1)[1])
        require(rollback == {'status': 'PASS', 'actual_reads': 6, 'saved_extended_snapshots': 2, 'writes': 0,
                            'external_provider_attempts': 0}, 'exact_rollback_reads_required')
        stage = 'preservation'
        require(snapshot(database, tables) == before_rows and catalog(database, tables) == before_catalog,
                'all_existing_99_catalog_rows_sequences_unchanged')
        require(query(database, "SELECT count(*) FROM pg_namespace WHERE nspname ~ '^professional_test_[a-f0-9]{32}$'") == [(0,)]
                and not app.dependency_overrides, 'test_schema_and_auth_cleanup_required')
    require(not attempts and sum(s['run'] for s in reports) == 38 and all(s['run'] == s['expected']
            and not any(s[k] for k in ('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'))
            for s in reports), 'all_38_contracts_required')
    return {'status': 'PASS', 'application_commit': COMMIT, 'application_tree': TREE, 'head': HEAD,
        'schema_tables': 99, 'tests': 38, 'suites': reports, 'all_catalog_rows_sequences_unchanged': True,
        'protected_owner_contacts': 82, 'protected_hidden_profiles': 46, 'retained_jobs': 9,
        'retained_cms_version': 7, 'retained_floorplans': 2, 'compatible_rollback_reads': 6,
        'unexpected_transport_attempts': 0, 'professional_schema_cleanup_verified': True}


if __name__ == '__main__':
    if sys.argv[1:] == ['--rollback']:
        rollback_reads()
    else:
        require(not sys.argv[1:], 'exact_mode_required')
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                report = run()
        except Exception as error:
            report = {'status': 'FAIL', 'stage': stage, 'error_type': type(error).__name__,
                      'suites': reports, 'unexpected_transport_attempts': len(attempts)}
            if isinstance(error, Refused):
                report['reason'] = str(error)
        print('PROFESSIONAL_GOOGLE_NATIVE_RESULT ' + json.dumps(report, sort_keys=True, separators=(',', ':')), flush=True)
        raise SystemExit(0 if report['status'] == 'PASS' else 1)

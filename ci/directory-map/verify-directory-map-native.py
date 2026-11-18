"""Run affected directory contracts in a marked, isolated loopback database."""

from contextlib import ExitStack, closing, redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
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


APPLICATION = Path('/application')
APP_COMMIT = '4d21cb3ae2b14475828ff8fbe710b22c34c3a53b'
APP_TREE = '1e3f72978515a58733188caa235cb6ee61190a26'
HEAD = 'b19d8e4c720a'
SUITES = (('test_directory_geo', 'DirectoryReferenceTests', 3),
          ('test_admin_trade_contacts', 'TradeContactsTest', 9),
          ('test_professionals', 'ProfessionalTests', 30))
OWNER_SOURCE = 'Synthetic owned map native fixture'
PROFILE_PREFIX = 'Synthetic hidden directory map fixture '
PROVIDER_KEYS = ('RESEND_API_KEY', 'GHL_API_KEY', 'GHL_LOCATION_ID', 'GHL_WEBHOOK_SECRET',
                 'SUPABASE_SERVICE_KEY', 'PAYPAL_CLIENT_ID', 'PAYPAL_CLIENT_SECRET', 'PAYPAL_WEBHOOK_ID')
stage = 'isolation'
reports = []
attempts = []


class Refused(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Refused(reason)


def git(*arguments):
    return subprocess.check_output(['git', '--no-optional-locks', '-c', 'safe.directory=/application',
        '-C', str(APPLICATION), *arguments], stderr=subprocess.DEVNULL,
        env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1',
             'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0'}, timeout=15).decode().strip()


def connection(database):
    import psycopg2
    return psycopg2.connect(host='127.0.0.1', port=55449, dbname=database, user='postgres', password='',
                            connect_timeout=4, options='-cstatement_timeout=10000')


def query(database, statement, parameters=()):
    with closing(connection(database)) as db, db.cursor() as cursor:
        cursor.execute(statement, parameters)
        return cursor.fetchall()


def identity():
    require(sys.platform == 'linux' and sys.version_info[:2] == (3, 11)
            and os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('GITHUB_REPOSITORY') == 'ilovedrrealty/realty-api'
            and {path.name for path in Path('/sys/class/net').iterdir()} == {'lo'}, 'owned_hosted_loopback_required')
    nonce = os.environ.get('ILDR_DIRECTORY_MAP_CI_NONCE', '')
    require(re.fullmatch(r'[a-f0-9]{32}', nonce), 'owned_nonce_required')
    database = 'ildr_directory_map_' + nonce
    require(os.environ.get('DATABASE_URL') == 'postgresql+psycopg2://postgres@127.0.0.1:55449/' + database
            and all(not os.environ.get(key) for key in PROVIDER_KEYS), 'owned_configuration_required')
    require(APPLICATION.resolve(strict=True) == APPLICATION and os.statvfs(APPLICATION).f_flag & os.ST_RDONLY
            and not (APPLICATION / '.env').exists() and not (APPLICATION / '.env').is_symlink(), 'immutable_source_required')
    require(git('rev-parse', 'HEAD', 'HEAD^{tree}').splitlines() == [APP_COMMIT, APP_TREE]
            and not git('status', '--porcelain=v1', '--untracked-files=all'), 'exact_source_required')
    require(query(database, """SELECT current_database(),host(inet_server_addr()),inet_server_port(),
        current_setting('server_version_num')::int,shobj_description(oid,'pg_database'),
        NOT EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public')
        FROM pg_database WHERE datname=current_database()""") == [
        (database, '127.0.0.1', 55449, 170006, 'ildr-directory-map-ci:' + nonce, True)], 'fresh_marked_database_required')
    baseline = json.loads(Path('/opt/directory-map/baseline-inventory.json').read_text())
    normalize = lambda name: re.sub(r'[-_.]+', '-', name.lower())
    inventory = {normalize(item.metadata['Name']): item.version for item in importlib.metadata.distributions()}
    readers = {'openpyxl': '3.1.5', 'defusedxml': '0.7.1', 'et-xmlfile': '2.0.0'}
    require(set(inventory) - set(baseline) == set(readers)
            and all(inventory.get(name) == version for name, version in baseline.items())
            and all(inventory.get(name) == version for name, version in readers.items()), 'exact_reader_inventory_required')
    return database


def bootstrap(database):
    for arguments in (
        ('-c', "import runpy,sys;sys.path.insert(0,'/application');runpy.run_path('/application/bootstrap_db.py',run_name='__main__')"),
        ('-m', 'alembic', 'upgrade', 'head'),
    ):
        result = subprocess.run([sys.executable, '-I', '-B', *arguments], cwd=APPLICATION,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=150)
        require(result.returncode == 0 and len(result.stdout) <= 4 * 1024 * 1024, 'owned_schema_bootstrap_required')
    require(query(database, 'SELECT version_num FROM alembic_version') == [(HEAD,)]
            and query(database, "SELECT count(*) FROM pg_tables WHERE schemaname='public'") == [(92,)],
            'exact_existing_92_head_required')


def preserved_rows(database):
    from psycopg2 import sql
    result = {}
    with closing(connection(database)) as db, db.cursor() as cursor:
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        names = [row[0] for row in cursor.fetchall()]
        require(len(names) == 92 and all(re.fullmatch(r'[a-z_]+', name) for name in names), 'exact_existing_92_head_required')
        for name in names:
            if name in {'trade_contacts', 'trade_contact_sources'}:
                continue
            cursor.execute(sql.SQL("SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb)::text FROM public.{} t").format(sql.Identifier(name)))
            result[name] = cursor.fetchone()[0]
        cursor.execute("SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")
        result['sequences'] = cursor.fetchall()
    return result


def seed_private_data():
    import jwt
    from config import settings
    from database import SessionLocal
    from models.professional_profile import ProfessionalProfile
    from models.user import User
    from schemas.professionals import ProfessionalDraft
    from schemas.trade_contacts import TradeContactImport
    from services.trade_contacts import import_entries
    from sqlalchemy import null

    entries = [{
        'name': 'Synthetic owned directory map contact ' + str(index),
        'category': 'Synthetic property service', 'province': 'Puerto Plata', 'locality': 'Sosúa',
        'phone': '+18095550101', 'source_authority': OWNER_SOURCE,
        'source_url': 'https://example.com/synthetic-map-source',
        'verified_on': date.today().isoformat(), 'verification_status': 'owner_source',
        'source_details': {'fixture': 'Synthetic owner-supplied record; retain all evidence'},
    } for index in range(82)]
    with SessionLocal() as db:
        require(import_entries(db, TradeContactImport(dry_run=False, entries=entries)).model_dump()
                == {'inserted': 82, 'updated': 0, 'unchanged': 0}, 'protected_owner_fixture_required')
        for index in range(46):
            draft = ProfessionalDraft(business_name=PROFILE_PREFIX + str(index),
                                      category_ids=['property_law'], service_area_ids=['sosua'])
            db.add(ProfessionalProfile(user_id=None, draft_data=draft.model_dump(mode='json'),
                                       published_data=null(), review_status='draft', listing_status='hidden'))
        admin = User(email='synthetic-directory-map-admin@example.com', display_name='Synthetic directory map admin',
                     role='admin', status='active', email_verified=True)
        db.add(admin)
        db.flush()
        identifier = str(admin.id)
        db.commit()
    return {'Authorization': 'Bearer ' + jwt.encode({
        'sub': identifier, 'exp': datetime.now(timezone.utc) + timedelta(hours=1),
    }, settings.jwt_secret, algorithm='HS256')}


def protected_snapshot(database):
    contacts = query(database, """SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.id),'[]'::jsonb)::text
        FROM trade_contacts t WHERE details->>'source_authority'=%s""", (OWNER_SOURCE,))[0][0]
    sources = query(database, """SELECT coalesce(jsonb_agg(to_jsonb(s) ORDER BY s.id),'[]'::jsonb)::text
        FROM trade_contact_sources s JOIN trade_contacts t ON t.id=s.contact_id
        WHERE t.details->>'source_authority'=%s""", (OWNER_SOURCE,))[0][0]
    profiles = query(database, """SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.id),'[]'::jsonb)::text
        FROM professional_profiles p WHERE draft_data->>'business_name' LIKE %s""", (PROFILE_PREFIX + '%',))[0][0]
    require((len(json.loads(contacts)), len(json.loads(sources)), len(json.loads(profiles))) == (82, 82, 46),
            'protected_82_owner_46_hidden_fixture_required')
    require(query(database, """SELECT count(*) FROM professional_profiles
        WHERE draft_data->>'business_name' LIKE %s AND user_id IS NULL AND listing_status='hidden'
        AND review_status='draft' AND published_data IS NULL AND published_at IS NULL""",
        (PROFILE_PREFIX + '%',)) == [(46,)], 'hidden_sql_null_fixture_required')
    return contacts, sources, profiles


def private_projection_checks(headers):
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    try:
        response = client.get('/professionals/map', headers=headers)
        require(response.status_code == 200 and response.headers.get('cache-control') == 'no-store'
                and response.json()['total'] == 0 and response.json()['areas'] == [],
                'hidden_profiles_absent_from_member_map')
        response = client.get('/admin/trade-contacts/map', headers=headers,
                              params={'q': 'Synthetic owned directory map contact'})
        require(response.status_code == 200 and response.headers.get('cache-control') == 'no-store'
                and response.json()['total'] == 82 and response.json()['mapped_total'] == 82
                and [(area['area_key'], area['record_count']) for area in response.json()['areas']] == [('DO:sosua', 82)],
                'protected_owner_admin_map_scope_required')
    finally:
        client.close()


class SafeResult(unittest.TestResult):
    def addFailure(self, test, error):
        self.failures.append((test, type(error[1]).__name__))

    def addError(self, test, error):
        self.errors.append((test, type(error[1]).__name__))


def refused_transport(*arguments, **keywords):
    attempts.append('unexpected_transport')
    raise Refused('external_transport_refused')


def contracts(database):
    global stage
    import httpx
    import requests
    import resend
    import smtplib
    import urllib.request
    from config import settings
    from database import engine, SessionLocal
    from sqlalchemy.engine import make_url
    from main import app

    expected = make_url(os.environ['DATABASE_URL'])
    require(engine.url == expected and SessionLocal.kw['bind'] is engine
            and settings.origins_list == ['http://localhost:5174']
            and os.environ.get('TRADE_CONTACT_FIXTURE') == 'yes'
            and os.environ.get('SEARCH_FILTER_TEST_DATABASE_URL') == os.environ['DATABASE_URL'],
            'actual_global_fixture_required')
    with ExitStack() as stack:
        for target, name in ((requests.sessions.Session, 'request'), (httpx.HTTPTransport, 'handle_request'),
                             (httpx.AsyncHTTPTransport, 'handle_async_request'), (resend.Emails, 'send'),
                             (urllib.request, 'urlopen'), (smtplib.SMTP, 'connect')):
            stack.enter_context(patch.object(target, name, refused_transport))
        stage = 'protected_fixture'
        headers = seed_private_data()
        protected_before = protected_snapshot(database)
        before = preserved_rows(database)
        private_projection_checks(headers)
        stage = 'directory_contracts'
        for module_name, class_name, expected_count in SUITES:
            # Existing ProfessionalTests sets synthetic module environment values.
            # Its loopback URL is provided before import; restore the owned
            # application's environment after the independent suite finishes.
            with patch.dict(os.environ, dict(os.environ), clear=True):
                module = importlib.import_module(module_name)
                case = getattr(module, class_name)
                suite = unittest.defaultTestLoader.loadTestsFromTestCase(case)
                require(suite.countTestCases() == expected_count, 'exact_contract_inventory_required')
                result = SafeResult()
                suite.run(result)
            failures = []
            for test, error_type in result.failures + result.errors:
                identifier = test.id()
                method = identifier.rsplit('.', 1)[-1]
                failures.append({'test': method if re.fullmatch(r'test_[a-z_]+', method) else 'setup_or_cleanup',
                                 'error_type': error_type if re.fullmatch(r'[A-Za-z_]{1,80}', error_type) else 'Exception'})
            for test, _ in result.skipped:
                method = test.id().rsplit('.', 1)[-1]
                failures.append({'test': method if re.fullmatch(r'test_[a-z_]+', method) else 'setup_or_cleanup',
                                 'error_type': 'SkipTest'})
            reports.append({'module': module_name, 'class': class_name, 'expected': expected_count,
                'run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                'skipped': len(result.skipped), 'expected_failures': len(result.expectedFailures),
                'unexpected_successes': len(result.unexpectedSuccesses), 'diagnostics': failures})
        stage = 'preservation'
        private_projection_checks(headers)
        require(protected_snapshot(database) == protected_before, 'all_protected_owner_evidence_and_hidden_rows_preserved')
    require(preserved_rows(database) == before and not app.dependency_overrides,
            'unrelated_rows_sequences_and_auth_overrides_preserved')
    require(query(database, "SELECT count(*) FROM pg_namespace WHERE nspname ~ '^professional_test_[a-f0-9]{32}$'") == [(0,)],
            'professional_schema_cleanup_required')
    private = query(database, """SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
        (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid),
        (SELECT count(*) FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
         LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE coalesce(r.rolname,'PUBLIC')
         IN ('PUBLIC','anon','authenticated','service_role'))
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
        AND c.relname IN ('trade_contacts','trade_contact_sources') ORDER BY c.relname""")
    require(private == [('trade_contact_sources', True, False, 0, 0), ('trade_contacts', True, False, 0, 0)],
            'private_trade_data_api_required')
    require(query(database, """SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,
        (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
        AND c.relname IN ('professional_profiles','professional_profile_revisions') ORDER BY c.relname""")
        == [('professional_profile_revisions', True, False, 0), ('professional_profiles', True, False, 0)],
        'private_professional_row_security_required')
    require(not attempts and sum(row['run'] for row in reports) == 42 and all(
        row['run'] == row['expected'] and not any(row[key] for key in ('failures','errors','skipped',
        'expected_failures','unexpected_successes')) for row in reports), 'all_42_contracts_required')


def run():
    global stage
    database = identity()
    os.environ.update(ENVIRONMENT='development', COOKIE_DOMAIN='', ALLOWED_ORIGINS='http://localhost:5174',
        JWT_SECRET='synthetic-directory-map-fixture-only-secret', LANDING_URL='http://127.0.0.1:8917',
        LEAD_DELIVERY_ENABLED='false', CUSTOMER_MESSAGES_ENABLED='false', GHL_ENABLED='false',
        PROPERTY_INQUIRY_REQUIRE_CONSENT='false', EMAIL_FROM='synthetic@example.com', NOTIFY_EMAIL='',
        SUPABASE_URL='', TRANSACTION_DOCUMENT_BUCKET='', TRADE_CONTACT_FIXTURE='yes',
        SEARCH_FILTER_TEST_DATABASE_URL=os.environ['DATABASE_URL'])
    logging.disable(logging.CRITICAL)
    stage = 'schema_fixture'
    bootstrap(database)
    sys.path.insert(0, str(APPLICATION))
    sys.path.insert(0, str(APPLICATION / 'tests'))
    stage = 'directory_contracts'
    contracts(database)
    return {'status': 'PASS', 'application_commit': APP_COMMIT, 'application_tree': APP_TREE,
            'head': HEAD, 'schema_tables': 92, 'tests': 42, 'suites': reports,
            'unrelated_rows_sequences_unchanged': True, 'trade_data_api_private': True,
            'protected_owner_contacts': 82, 'protected_sources': 82, 'protected_hidden_profiles': 46,
            'private_projection_no_disclosure': True, 'professional_schema_cleanup_verified': True,
            'unexpected_transport_attempts': len(attempts), 'external_provider_requests': 0}


if __name__ == '__main__':
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            report = run()
    except Exception as error:
        report = {'status': 'FAIL', 'stage': stage, 'error_type': type(error).__name__,
                  'suites': reports, 'unexpected_transport_attempts': len(attempts)}
        if isinstance(error, Refused):
            report['reason'] = str(error)
    print('DIRECTORY_MAP_NATIVE_RESULT ' + json.dumps(report, sort_keys=True, separators=(',', ':')), flush=True)
    raise SystemExit(0 if report['status'] == 'PASS' else 1)

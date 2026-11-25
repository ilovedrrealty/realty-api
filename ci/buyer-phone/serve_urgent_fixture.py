"""Confined real APIs for buyer assignment; no signup or provider writes."""
from collections import Counter
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import smtplib
import stat
import subprocess
import sys
from urllib import request as urllib_request
from unittest.mock import patch
from uuid import uuid4

COMMIT = 'd90a68cbfa07f42a02a841f7730999d96f40dc60'
TREE = 'df717faf477930d2a8a892be72b99d056553599a'
HEAD = 'c4e2a7319b60'
DIRECTORY = Path('/browser-fixtures')
FACTS_FILE = DIRECTORY / 'fixtures.json'
ACCEPTED = Path(__file__).resolve().parent.parent / 'social-groups-browser/serve_social_groups_fixture.py'
ACCEPTED_SHA = 'e0c5f0971c7c13ed47063907f01fa6ef01590b00fa2855eb1ecb16a38b99463e'
ROLES = {'admin': 'admin', 'buyer': 'buyer', 'agent': 'realtor', 'owner': 'owner'}
stage = 'checkout'


class Failure(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise Failure(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def accepted_helpers():
    require(ACCEPTED.is_file() and not ACCEPTED.is_symlink()
            and sha256(ACCEPTED.read_bytes()).hexdigest() == ACCEPTED_SHA, 'accepted_fixture_bytes_required')
    spec = spec_from_file_location('accepted_urgent_fixture_helpers', ACCEPTED)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    module.APP_COMMIT, module.APP_TREE = COMMIT, TREE
    return module


def seed_database(retained):
    import bootstrap_db
    from alembic import command
    from alembic.config import Config
    from database import SessionLocal
    from models.user import User
    from utils.security import hash_password
    bootstrap_db.main()
    command.upgrade(Config('alembic.ini'), 'head')
    require(retained.seed_old() == {'users': 4, 'leads': 3, 'owner_contacts': 82, 'contacts': 85, 'sources': 86,
        'hidden_profiles': 46, 'accepted_jobs': 9, 'worker_rows': 1, 'cms_version': 7, 'floorplan_rows': 2},
        'accepted_retained_seed_required')
    identities = {actor: retained.IDS[role] for actor, role in ROLES.items() if actor != 'buyer'}
    identities['buyer'] = uuid4()
    accounts, facts = {}, {}
    with SessionLocal.begin() as db:
        # Historical buyer leads remain untouched; this separate account has no assigned lead fallback.
        db.add(User(id=identities['buyer'], role='buyer', status='active', email='urgent-buyer@example.com',
            display_name='Synthetic urgent buyer', email_verified=True))
        db.flush()
        for actor, identity in identities.items():
            user = db.get(User, identity)
            require(user.role == ROLES[actor] and user.status == 'active' and user.email_verified
                    and user.assigned_realtor_id is None, 'supported_unassigned_actor_required')
            accounts[actor] = {'email': user.email, 'password': secrets.token_urlsafe(24)}
            user.password_hash = hash_password(accounts[actor]['password'])
            facts[actor + 'Id'] = str(identity)
            if actor == 'buyer':
                facts.update(buyerName=user.display_name, buyerEmail=user.email)
    return accounts, identities, facts


NATIVE_CODE = r'''
import io,json,os,smtplib,sys,unittest
from urllib import request as urllib_request
from unittest.mock import patch
sys.path.insert(0,'/application')
import httpx,requests,resend
attempts=[]
def refuse(*args,**kwargs):
    attempts.append(1)
    raise RuntimeError('external_transport_refused')
with (patch.object(httpx.HTTPTransport,'handle_request',refuse),
      patch.object(httpx.AsyncHTTPTransport,'handle_async_request',refuse),
      patch.object(requests.Session,'send',refuse),patch.object(urllib_request,'urlopen',refuse),
      patch.object(resend.Emails,'send',refuse),patch.object(smtplib.SMTP,'connect',refuse),
      patch.object(smtplib.SMTP_SSL,'connect',refuse)):
    suite=unittest.defaultTestLoader.loadTestsFromName('tests.test_admin_agent_assignments')
    result=unittest.TestResult()
    suite.run(result)
summary={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
    'skipped':len(result.skipped),'provider_attempts':len(attempts)}
print('URGENT_NATIVE_RESULT '+json.dumps(summary,sort_keys=True))
raise SystemExit(0 if summary=={'tests':8,'failures':0,'errors':0,'skipped':0,'provider_attempts':0} else 1)
'''


def native_regression(application, url, database, helpers, rows, catalog):
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/tmp', 'PYTHONDONTWRITEBYTECODE': '1',
        'SEARCH_FILTER_TEST_DATABASE_URL': url}
    result = subprocess.run([sys.executable, '-I', '-B', '-c', NATIVE_CODE], cwd=application,
        env=env, timeout=180, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(len(result.stdout) + len(result.stderr) <= 1024 * 1024, 'bounded_native_output_required')
    markers = [line for line in result.stdout.splitlines() if line.startswith(b'URGENT_NATIVE_RESULT ')]
    require(len(markers) == 1 and len(markers[0]) < 512, 'native_result_marker_required')
    value = json.loads(markers[0].split(b' ', 1)[1])
    require(set(value) == {'tests', 'failures', 'errors', 'skipped', 'provider_attempts'}
        and all(type(number) is int and 0 <= number <= 999 for number in value.values()), 'native_count_shape_required')
    print('URGENT_NATIVE_RESULT ' + canonical(value), flush=True)
    require(result.returncode == 0 and value == {'tests': 8, 'failures': 0, 'errors': 0,
        'skipped': 0, 'provider_attempts': 0}, 'actual_eight_native_cases_required')
    tables = helpers.table_names(database)
    require(len(tables) == 99 and helpers.snapshot(database, tables) == rows
        and helpers.catalog(database, tables) == catalog
        and helpers.query(database, "SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'admin_agent_test_%'") == [(0,)],
        'native_public_baseline_and_test_schema_cleanup_required')


def confined(app, base, accounts, identities, seen):
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from utils.auth import _decode_token
    actors = {str(value): actor for actor, value in identities.items()}
    logins = {canonical(value) for value in accounts.values()}
    assignment = '/admin/users/' + str(identities['buyer']) + '/assign-realtor'

    async def handle(scope, receive, send):
        if scope['type'] != 'http':
            return await app(scope, receive, send)
        request = Request(scope, receive)
        path, method = scope['path'], scope['method']
        actor = actors.get(_decode_token(request.cookies.get('ildr_token'))) if request.cookies.get('ildr_token') else None
        if method in {'GET', 'HEAD', 'OPTIONS'}:
            return await app(scope, receive, send)
        raw = await request.body()
        if len(raw) > 1024:
            seen['refused'] += 1
            return await JSONResponse({'detail': 'ci_mutation_refused'}, status_code=403)(scope, receive, send)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            body = None
        if method == 'POST' and path == '/auth/login' and canonical(body) in logins:
            return await app(scope, base.replay_body(raw, receive), send)
        expected = str(identities['agent']) if len(seen['writes']) == 0 else None
        if not (method == 'PUT' and path == assignment and actor == 'admin'
                and len(seen['writes']) < 2 and body == {'realtor_id': expected}):
            seen['refused'] += 1
            return await JSONResponse({'detail': 'ci_mutation_refused'}, status_code=403)(scope, receive, send)
        async def capture(message):
            if message['type'] == 'http.response.start':
                require(message['status'] == 204, 'actual_assignment_success_required')
                seen['writes'].append(expected)
            await send(message)
        return await app(scope, base.replay_body(raw, receive), capture)
    return handle


def verify_database(database, helpers, identities, rows, catalog, seen, attempts):
    from database import SessionLocal
    from models.activity_log import ActivityLog
    from models.user import User
    tables = helpers.table_names(database)
    require(len(tables) == 99 and helpers.query(database, 'SELECT version_num FROM alembic_version') == [(HEAD,)]
        and helpers.catalog(database, tables) == catalog, 'all_99_catalog_and_head_unchanged_required')
    after = helpers.snapshot(database, tables)
    for table, value in rows.items():
        if table != 'activity_log':
            require(after[table] == value, 'all_original_rows_and_sequences_unchanged_required')
    old_logs = json.loads(rows['activity_log'])
    new_logs = json.loads(after['activity_log'])
    old_ids = {row['id'] for row in old_logs}
    require([row for row in new_logs if row['id'] in old_ids] == old_logs, 'old_audit_rows_unchanged_required')
    added = [row for row in new_logs if row['id'] not in old_ids]
    require(len(added) == 2 and Counter(row['event_type'] for row in added)
        == {'realtor_assigned': 1, 'realtor_unassigned': 1}
        and all(row['actor_id'] == str(identities['admin']) for row in added), 'exact_two_audit_rows_required')
    with SessionLocal() as db:
        buyer = db.get(User, identities['buyer'])
        require(buyer.role == 'buyer' and buyer.assigned_realtor_id is None, 'buyer_role_and_clear_required')
    require(seen['writes'] == [str(identities['agent']), None] and seen['refused'] == 0 and not attempts,
        'exact_browser_writes_without_signup_or_provider_attempts_required')
    return {'status': 'PASS', 'schema_tables': 99, 'native_tests': 8, 'browser_assignment_writes': 2,
        'audit_rows_added': 2, 'users_unchanged': 5, 'protected_owner_contacts_unchanged': 82,
        'contacts_unchanged': 85, 'sources_unchanged': 86, 'private_profiles_unchanged': 46,
        'accepted_jobs_unchanged': 9, 'cms_version_unchanged': 7, 'floorplans_unchanged': 2,
        'signup_writes': 0, 'unexpected_mutations': 0, 'external_provider_attempts': 0}


def run():
    global stage
    logging.disable(logging.CRITICAL)
    base = accepted_helpers()
    application = base.verified_checkout()
    database, url = base.claim_database()
    base.configure(application, url)
    helpers, retained = base.frozen_helpers()
    import httpx, requests, resend, uvicorn
    owned, attempts = [], []
    seen = {'writes': [], 'refused': 0}
    def refuse(*args, **kwargs):
        attempts.append(1)
        raise RuntimeError('external_transport_refused')
    try:
        with (patch.object(httpx.HTTPTransport, 'handle_request', refuse),
            patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', refuse),
            patch.object(requests.Session, 'send', refuse), patch.object(urllib_request, 'urlopen', refuse),
            patch.object(resend.Emails, 'send', refuse), patch.object(smtplib.SMTP, 'connect', refuse),
            patch.object(smtplib.SMTP_SSL, 'connect', refuse)):
            from main import app
            stage = 'retained_seed'
            accounts, identities, facts = seed_database(retained)
            tables = helpers.table_names(database)
            require(len(tables) == 99, 'exact_99_tables_required')
            rows, catalog = helpers.snapshot(database, tables), helpers.catalog(database, tables)
            stage = 'native_assignment'
            native_regression(application, url, database, helpers, rows, catalog)
            base.write_owned(FACTS_FILE, (canonical({'scope': 'buyer-phone-only', 'accounts': accounts,
                'facts': facts}) + '\n').encode(), owned)
            server = uvicorn.Server(uvicorn.Config(confined(app, base, accounts, identities, seen),
                host='127.0.0.1', port=8918, lifespan='off', access_log=False, log_level='warning'))
            handlers = {}
            def shutdown(number, frame):
                server.should_exit = True
            try:
                for number in (signal.SIGINT, signal.SIGTERM):
                    handlers[number] = signal.signal(number, shutdown)
                stage = 'urgent_browser'
                server.run()
                require(server.started, 'actual_api_server_required')
                stage = 'verify_urgent'
                report = verify_database(database, helpers, identities, rows, catalog, seen, attempts)
            finally:
                for number, handler in handlers.items():
                    signal.signal(number, handler)
    finally:
        for path, device, inode in reversed(owned):
            if path.exists() or path.is_symlink():
                info = path.lstat()
                require(stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == (device, inode), 'owned_private_inode_required')
                path.unlink()
        require(all(not path.exists() and not path.is_symlink() for path, _, _ in owned), 'private_fixture_cleanup_required')
    print('URGENT_FIXTURE_RESULT ' + canonical({**report, 'private_files_removed': 1}), flush=True)


if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        name = type(error).__name__
        print('URGENT_FIXTURE_FAILURE ' + canonical({'stage': stage,
            'error_type': name if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', name) else 'UnhandledError',
            'reason': error.args[0] if isinstance(error, Failure) else 'external_failure'}), flush=True)
        raise SystemExit(1) from None

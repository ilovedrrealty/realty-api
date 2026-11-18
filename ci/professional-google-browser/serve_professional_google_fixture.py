"""Real Google-profile APIs; retained synthetic data and no Google connections."""
from collections import Counter
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
import json
import logging
from pathlib import Path
import re
import secrets
import signal
import smtplib
import stat
from urllib import request as urllib_request
from unittest.mock import patch
from uuid import UUID, uuid4

COMMIT = 'd9cfc94a1ebc45b8f3c391a69cbb8d0d8b25d26b'
TREE = '4a00441d18cf55c3e26414d1a7c2b49f791ee33d'
HEAD = 'c4e2a7319b60'
DIRECTORY = Path('/browser-fixtures')
FACTS_FILE = DIRECTORY / 'fixtures.json'
ACCEPTED = Path(__file__).resolve().parent.parent / 'social-groups-browser/serve_social_groups_fixture.py'
ACCEPTED_SHA = 'e0c5f0971c7c13ed47063907f01fa6ef01590b00fa2855eb1ecb16a38b99463e'
FACTS = {'searchTerm': 'Synthetic Google', 'ownerName': 'Synthetic Google owner service',
    'managedName': 'Synthetic Google managed service', 'legacyName': 'Synthetic Google legacy service',
    'profileUrl': 'https://www.google.com/maps/place/Synthetic+Google+Business/',
    'nextProfileUrl': 'https://maps.app.goo.gl/SyntheticNext123',
    'embedUrl': 'https://www.google.com/maps/embed?pb=!1m2!1m1!1d1',
    'invalidProfileUrl': 'https://google.example/maps/place/Synthetic',
    'reviewNote': 'Synthetic Google profile review'}
ROLES = {'admin': 'admin', 'owner': 'owner', 'member': 'buyer', 'realtor': 'realtor'}
stage = 'checkout'


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def accepted_helpers():
    require(ACCEPTED.is_file() and not ACCEPTED.is_symlink()
            and sha256(ACCEPTED.read_bytes()).hexdigest() == ACCEPTED_SHA, 'accepted_fixture_bytes_required')
    spec = spec_from_file_location('accepted_google_fixture_helpers', ACCEPTED)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    # Explicit source bindings only; the accepted helper source stays immutable.
    module.APP_COMMIT, module.APP_TREE = COMMIT, TREE
    return module


def seed_database(base, retained):
    import bootstrap_db
    from alembic import command
    from alembic.config import Config
    from database import SessionLocal
    from models.professional_profile import ProfessionalProfile, ProfessionalProfileRevision
    from models.user import User
    from schemas.professionals import ProfessionalDraft
    from sqlalchemy import null
    from utils.security import hash_password
    bootstrap_db.main()
    command.upgrade(Config('alembic.ini'), 'head')
    result = retained.seed_old()
    require(result == {'users': 4, 'leads': 3, 'owner_contacts': 82, 'contacts': 85, 'sources': 86,
        'hidden_profiles': 46, 'accepted_jobs': 9, 'worker_rows': 1, 'cms_version': 7, 'floorplan_rows': 2},
        'accepted_retained_seed_required')
    identities = {actor: retained.IDS[role] for actor, role in ROLES.items()}
    accounts, profiles, drafts = {}, {}, {}
    with SessionLocal.begin() as db:
        for actor, identity in identities.items():
            user = db.get(User, identity)
            require(user.role == ROLES[actor] and user.status == 'active' and user.email_verified,
                    'supported_existing_actor_required')
            accounts[actor] = {'email': user.email, 'password': secrets.token_urlsafe(24)}
            user.password_hash = hash_password(accounts[actor]['password'])
        for kind in ('owner', 'managed', 'legacy'):
            draft = ProfessionalDraft(business_name=FACTS[kind + 'Name'], contact_name='Synthetic Google contact',
                category_ids=['property_law'], service_area_ids=['sosua'],
                descriptions={'en': 'Synthetic Google professional description', 'es': 'Descripción profesional sintética'},
                languages=['en', 'es'], public_phone='+18095550101', public_contact_consent=kind == 'legacy').model_dump(mode='json')
            if kind == 'legacy':
                draft = {key: value for key, value in draft.items() if key not in {'google_profile_url', 'google_maps_embed_url'}}
            row = ProfessionalProfile(id=uuid4(), user_id=identities['owner'] if kind == 'owner' else None,
                version=1, draft_data=draft, published_data=draft.copy() if kind == 'legacy' else null(),
                review_status='approved' if kind == 'legacy' else 'draft',
                listing_status='published' if kind == 'legacy' else 'hidden',
                published_at=retained.HISTORY if kind == 'legacy' else None)
            db.add(row)
            db.flush()
            db.add(ProfessionalProfileRevision(profile_id=row.id, version=1, action='fixture_seed',
                actor_id=identities['admin'], snapshot={'draft': draft, 'published': draft if kind == 'legacy' else None}))
            profiles[kind], drafts[kind] = str(row.id), draft
    facts = {**FACTS, **{kind + 'ProfileId': value for kind, value in profiles.items()}}
    return accounts, identities, profiles, drafts, facts


def confined(app, base, accounts, identities, profiles, drafts, seen):
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from utils.auth import _decode_token
    actors = {str(value): actor for actor, value in identities.items()}
    logins = {canonical(value) for value in accounts.values()}
    operations = {('PUT', '/professional-profile'): ('owner', 'save'),
        ('POST', '/professional-profile/submit'): ('owner', 'submit'),
        ('PUT', '/admin/professionals/' + profiles['managed']): ('managed', 'admin_save'),
        ('POST', '/admin/professionals/' + profiles['owner'] + '/approve'): ('owner', 'approve')}

    async def handle(scope, receive, send):
        if scope['type'] != 'http':
            return await app(scope, receive, send)
        request = Request(scope, receive)
        path, method = scope['path'], scope['method']
        token = request.cookies.get('ildr_token')
        actor = actors.get(_decode_token(token)) if token else None
        async def read_capture(message):
            if message['type'] == 'http.response.start':
                seen['reads'][(actor or 'anonymous', path, message['status'])] += 1
            await send(message)
        if method in {'GET', 'HEAD', 'OPTIONS'}:
            return await app(scope, receive, read_capture)
        async def reject():
            seen['refused'].append(1)
            return await JSONResponse({'detail': 'Synthetic Google scope refused'}, 405,
                headers={'Cache-Control': 'no-store'})(scope, receive, send)
        raw = await request.body()
        if len(raw) > 32768:
            return await reject()
        if path == '/auth/logout' and method == 'POST' and raw in (b'', b'{}'):
            return await app(scope, base.replay_body(raw, receive), send)
        try:
            body = json.loads(raw) if request.headers.get('content-type', '').split(';')[0] == 'application/json' else None
        except (ValueError, UnicodeError):
            body = None
        if path == '/auth/login' and method == 'POST' and canonical(body) in logins:
            return await app(scope, base.replay_body(raw, receive), send)
        operation = operations.get((method, path))
        if not operation or not isinstance(body, dict):
            return await reject()
        kind, action = operation
        if not isinstance(body.get('expected_version'), int) or isinstance(body['expected_version'], bool) or not 1 <= body['expected_version'] <= 12:
            return await reject()
        if action in {'save', 'admin_save'}:
            data = body.get('draft')
            if not isinstance(data, dict) or set(body) != {'expected_version', 'draft'} or set(data) != set(drafts[kind]):
                return await reject()
            for key in data:
                if key not in {'google_profile_url', 'google_maps_embed_url', 'public_contact_consent'} and data[key] != drafts[kind][key]:
                    return await reject()
            if (data['google_profile_url'], data['google_maps_embed_url']) not in {
                (FACTS['profileUrl'], FACTS['embedUrl']), (FACTS['nextProfileUrl'], '')} or type(data['public_contact_consent']) is not bool:
                return await reject()
            # Normal self-profile writes are confined to the known owner. Safe
            # anonymous requests still reach real authentication, not fake401.
            if action == 'save' and actor not in {None, 'owner'}:
                return await reject()
        elif action == 'submit' and set(body) != {'expected_version'}:
            return await reject()
        elif action == 'approve' and (set(body) != {'expected_version', 'note'} or body['note'] != FACTS['reviewNote']):
            return await reject()
        captured, size = [], 0
        async def capture(message):
            nonlocal size
            if message['type'] == 'http.response.body':
                size += len(message.get('body', b''))
                require(size <= 262144, 'bounded_actual_response_required')
            captured.append(message)
        await app(scope, base.replay_body(raw, receive), capture)
        status = next(message['status'] for message in captured if message['type'] == 'http.response.start')
        if status in {200, 201}:
            result = json.loads(b''.join(m.get('body', b'') for m in captured if m['type'] == 'http.response.body'))
            require(actor in identities and result['id'] == profiles[kind] and len(seen['writes']) < 10,
                    'owned_target_write_budget_required')
            expected_actor = 'owner' if action in {'save', 'submit'} else 'admin'
            require(actor == expected_actor and result['version'] == body['expected_version'] + 1,
                    'real_actor_and_cas_result_required')
            seen['writes'].append((profiles[kind], result['version'], str(identities[actor]), action))
        else:
            seen['denials'][(actor or 'anonymous', action, status)] += 1
        for message in captured:
            await send(message)
    return handle


def verify_database(database, helpers, profiles, before_rows, before_catalog, seen, attempts):
    from database import SessionLocal
    from models.professional_profile import ProfessionalProfile, ProfessionalProfileRevision
    tables = helpers.table_names(database)
    require(len(tables) == 99 and helpers.query(database, 'SELECT version_num FROM alembic_version') == [(HEAD,)]
            and helpers.catalog(database, tables) == before_catalog, 'all_99_catalog_and_head_unchanged_required')
    after = helpers.snapshot(database, tables)
    for table, values in before_rows.items():
        if table not in {'professional_profiles', 'professional_profile_revisions'}:
            require(after[table] == values, 'all_other_rows_sequences_unchanged_required')
    targets = {profiles['owner'], profiles['managed']}
    for table, field in (('professional_profiles', 'id'), ('professional_profile_revisions', 'profile_id')):
        filter_rows = lambda value: [row for row in json.loads(value) if row[field] not in targets
            or table == 'professional_profile_revisions' and row['version'] == 1]
        require(filter_rows(after[table]) == filter_rows(before_rows[table]), 'all_46_private_and_legacy_rows_unchanged_required')
    require(Counter(action for _, _, _, action in seen['writes']) == {'save': 5, 'submit': 2, 'approve': 2, 'admin_save': 1}
            and seen['denials'][('owner', 'save', 409)] == 1, 'actual_ten_writes_and_stale_conflict_required')
    with SessionLocal() as db:
        owner = db.get(ProfessionalProfile, UUID(profiles['owner']))
        managed = db.get(ProfessionalProfile, UUID(profiles['managed']))
        require(owner.version == 10 and owner.listing_status == 'hidden' and owner.review_status == 'draft'
                and owner.draft_data['public_contact_consent'] is False
                and owner.published_data['google_profile_url'] == FACTS['nextProfileUrl']
                and owner.published_data['google_maps_embed_url'] == '', 'owner_explicit_approved_then_withdrawn_required')
        require(managed.version == 2 and managed.user_id is None and managed.listing_status == 'hidden'
                and managed.review_status == 'draft' and managed.published_data is None
                and managed.draft_data['google_profile_url'] == FACTS['profileUrl']
                and managed.draft_data['google_maps_embed_url'] == FACTS['embedUrl']
                and managed.draft_data['public_contact_consent'] is False, 'managed_google_save_stays_private_required')
        actual = [(str(row.profile_id), row.version, str(row.actor_id), row.action)
            for row in db.query(ProfessionalProfileRevision).filter(
                ProfessionalProfileRevision.profile_id.in_([UUID(value) for value in targets]), ProfessionalProfileRevision.version > 1)]
        require(sorted(actual) == sorted(seen['writes']) and db.query(ProfessionalProfile).count() == 49,
                'exact_native_revision_ledger_and_profile_count_required')
    owner_path = '/professionals/' + profiles['owner']
    managed_path = '/professionals/' + profiles['managed']
    legacy_path = '/professionals/' + profiles['legacy']
    require(seen['reads'][('member', owner_path, 200)] > 0 and seen['reads'][('member', owner_path, 404)] > 0
            and seen['reads'][('member', managed_path, 404)] > 0 and seen['reads'][('member', legacy_path, 200)] > 0
            and any(actor == 'anonymous' and status == 401 and path.startswith('/professionals')
                    for actor, path, status in seen['reads']), 'actual_member_visibility_and_guest_denial_required')
    require(not attempts and not seen['refused'], 'no_unexpected_mutations_or_provider_attempts_required')
    return {'status': 'PASS', 'schema_tables': 99, 'protected_owner_contacts_unchanged': 82,
        'contacts_unchanged': 85, 'sources_unchanged': 86, 'private_profiles_unchanged': 46,
        'legacy_profile_unchanged': 1, 'profiles': 49, 'users_unchanged': 4, 'accepted_jobs_unchanged': 9,
        'worker_rows_unchanged': 1, 'cms_version_unchanged': 7, 'floorplans_unchanged': 2,
        'successful_profile_writes': 10, 'stale_version_conflicts': 1,
        'actual_revision_rows_matched': 10, 'unexpected_mutations': 0, 'external_provider_attempts': 0}


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
    seen = {'reads': Counter(), 'denials': Counter(), 'refused': [], 'writes': []}
    def refuse_transport(*args, **kwargs):
        attempts.append(1)
        raise RuntimeError('external_transport_refused')
    try:
        with (patch.object(httpx.HTTPTransport, 'handle_request', refuse_transport),
            patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', refuse_transport),
            patch.object(requests.Session, 'send', refuse_transport), patch.object(urllib_request, 'urlopen', refuse_transport),
            patch.object(resend.Emails, 'send', refuse_transport), patch.object(smtplib.SMTP, 'connect', refuse_transport),
            patch.object(smtplib.SMTP_SSL, 'connect', refuse_transport)):
            from main import app
            stage = 'retained_seed'
            accounts, identities, profiles, drafts, facts = seed_database(base, retained)
            tables = helpers.table_names(database)
            require(len(tables) == 99, 'exact_99_tables_required')
            before_rows, before_catalog = helpers.snapshot(database, tables), helpers.catalog(database, tables)
            base.write_owned(FACTS_FILE, (canonical({'scope': 'professional-google-only', 'accounts': accounts,
                'facts': facts}) + '\n').encode(), owned)
            server = uvicorn.Server(uvicorn.Config(confined(app, base, accounts, identities, profiles, drafts, seen),
                host='127.0.0.1', port=8918, lifespan='off', access_log=False, log_level='warning'))
            handlers = {}
            def shutdown(number, frame):
                server.should_exit = True
            try:
                for number in (signal.SIGINT, signal.SIGTERM):
                    handlers[number] = signal.signal(number, shutdown)
                stage = 'professional_google_browser'
                server.run()
                require(server.started, 'actual_api_server_required')
                stage = 'verify_professional_google'
                report = verify_database(database, helpers, profiles, before_rows, before_catalog, seen, attempts)
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
    print('PROFESSIONAL_GOOGLE_FIXTURE_RESULT ' + canonical({**report, 'private_files_removed': 1}), flush=True)


if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        name = type(error).__name__
        print('PROFESSIONAL_GOOGLE_FIXTURE_FAILURE ' + canonical({'stage': stage,
            'error_type': name if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', name) else 'UnhandledError'}), flush=True)
        raise SystemExit(1) from None

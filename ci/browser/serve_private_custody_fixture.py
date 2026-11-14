"""Serve exact synthetic private-registry custody acceptance on a loopback-only database."""

from datetime import timedelta
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import traceback
from unittest.mock import patch
from urllib import request as urllib_request
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_service_visit_fixture as visit


APP_COMMIT = 'b38d477c0e2e50f87f1f2e4cc1ceb3c707af92c5'
APP_TREE = 'a53b3f829f70717069f5c637c826fb8f92c02f7c'
MIGRATION = 'f4b27d6e913c'
BASE = '/property-network/registry-custody'
UUID_TEXT = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
CASES = ('decline', 'cancel', 'accept')
STATUS = {'offer': 'pending', 'decline': 'declined', 'cancel': 'cancelled', 'accept': 'accepted'}
OPERATIONS = tuple((case, operation) for case in CASES for operation in ('offer', case))
FIELDS = {'title': 'Synthetic private custody villa', 'location': 'Synthetic custody locality',
          'address': 'Synthetic access address for the new custodian only',
          'property_class': 'house_villa', 'relationship': 'owner'}
NOTES = 'Synthetic former-custodian private notes, excluded from every transfer.'
RAW_HISTORY = 'Synthetic legacy project work note and attached image, outside the transfer.'
UNAFFECTED = (
    'listings', 'professional_profiles', 'network_project_members', 'network_project_updates',
    'network_project_comments', 'network_media', 'network_service_preferences', 'network_service_recipients',
    'network_service_proposals', 'network_service_actions', 'network_service_work', 'network_service_reviews',
    'network_service_conversation_messages', 'network_service_negotiation_messages', 'network_history_shares',
    'network_history_share_actions', 'network_maintenance_preferences', 'network_maintenance_schedules',
    'network_maintenance_occurrences', 'network_maintenance_actions', 'professional_profile_revisions',
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def exact_source():
    expected = (APP_COMMIT, APP_TREE, MIGRATION)
    actual = tuple(os.environ.get(key) for key in (
        'ILDR_PRIVATE_CUSTODY_APP_COMMIT', 'ILDR_PRIVATE_CUSTODY_APP_TREE', 'ILDR_PRIVATE_CUSTODY_MIGRATION_HEAD'))
    visit.require(all(re.fullmatch(r'[a-f0-9]{40}', pin) for pin in expected[:2]) and actual == expected,
                  'Exact frozen custody application required')
    visit.APP_COMMIT, visit.APP_TREE, visit.MIGRATION_HEAD = expected
    return visit.verified_checkout()


def seed_custody(ids, instant):
    from PIL import Image
    from database import SessionLocal
    from models.history_share import NetworkHistoryShare
    from models.user import User
    from schemas.history_share import HistoryShareAction, HistoryShareCreate, HistoryShareItem
    from schemas.maintenance_reminders import MaintenancePreferencesWrite, MaintenanceScheduleCreate
    from schemas.property_network import ProgressWrite, MemberWrite, ProjectCreate, ProjectEdit, RegistryCreate
    from schemas.service_workflow import RequestAction, RequestCreate
    from services import history_share, maintenance_reminders, property_network as network, service_visit, service_workflow

    day = instant.astimezone(service_visit.DR_TIMEZONE).date()
    with SessionLocal() as db:
        owner, professional, recipient = (db.get(User, ids[name]) for name in ('owner', 'winner', 'loser'))
        creator_key = uuid4()
        registry = network.create_registry(db, owner, RegistryCreate(
            submission_id=creator_key, notes=NOTES, authority_acknowledged=True, **FIELDS))
        property_id = UUID(registry['id'])
        history_title = 'Synthetic completed custody history project'
        anchor_title = 'Synthetic former history-sharing anchor'
        historical = network.create_project(db, owner, ProjectCreate(submission_id=uuid4(), property_id=property_id,
            title=history_title, property_class=FIELDS['property_class'], service_category_ids=['pool_service']))
        history_project_id = UUID(historical['id'])
        source = BytesIO()
        Image.new('RGB', (64, 48), (59, 127, 193)).save(source, 'PNG')
        photo = network.upload_media(db, owner, history_project_id, source.getvalue())
        update = network.create_update(db, owner, history_project_id, ProgressWrite(submission_id=uuid4(),
            occurred_on=day - timedelta(days=1), body=RAW_HISTORY, media_ids=[UUID(photo['id'])]))
        network.edit_project(db, owner, history_project_id, ProjectEdit(expected_version=historical['version'],
            title=history_title, status='completed'))
        anchor = network.create_project(db, owner, ProjectCreate(submission_id=uuid4(), property_id=property_id,
            title=anchor_title, property_class=FIELDS['property_class']))
        anchor_id = UUID(anchor['id'])
        network.assign(db, owner, anchor_id, MemberWrite(expected_version=anchor['version'],
            user_code=professional.user_code, role='professional'))
        share = history_share.create(db, owner, HistoryShareCreate(submission_id=uuid4(), source_kind='private',
            source_id=property_id, anchor_project_id=anchor_id, recipient_user_code=professional.user_code,
            label='Synthetic former selective share', expires_in_days=7,
            items=[HistoryShareItem(update_id=UUID(update['id']), summary='Synthetic superseded summary.')]))
        share_id = UUID(share['id'])
        history_share.change(db, professional, share_id, HistoryShareAction(
            submission_id=uuid4(), expected_version=1, operation='accept'))
        visit.require(history_share.detail(db, professional, share_id)['status'] == 'accepted', 'Real prior selective grant required')
        network.edit_project(db, owner, anchor_id, ProjectEdit(expected_version=anchor['version'] + 1,
            title=anchor_title, status='completed'))
        db.expire_all()
        visit.require(db.get(NetworkHistoryShare, share_id).invalidated_at is not None, 'Closed work must already invalidate the history grant')
        request = service_workflow.create_request(db, owner, RequestCreate(submission_id=uuid4(), property_id=property_id,
            category_id='pool_service', property_class=FIELDS['property_class'], title='Synthetic former closed request',
            brief='Synthetic former request scope', area_label='Synthetic custody locality', requester_alias='Synthetic former custodian',
            recipient_user_codes=[professional.user_code], shared_brief_acknowledged=True))
        request_id = UUID(request['id'])
        service_workflow.close_request(db, owner, request_id, RequestAction(
            submission_id=uuid4(), expected_request_version=request['version']))

        reminder = maintenance_reminders.save_preferences(db, owner, 'private', property_id,
            MaintenancePreferencesWrite(submission_id=uuid4(), expected_version=0, enabled=True, lead_days=9))
        maintenance_reminders.create(db, owner, 'private', property_id, MaintenanceScheduleCreate(submission_id=uuid4(),
            title='Synthetic old custodian reminder', repeat='monthly', first_due_on=day.isoformat()))
        visit.require(reminder == {'enabled': True, 'lead_days': 9, 'version': 1}, 'Former personal reminder required')
        blocked = network.create_registry(db, owner, RegistryCreate(submission_id=uuid4(),
            title='Synthetic private custody record with open work', location='Synthetic blocker locality',
            property_class='house_villa', relationship='owner', authority_acknowledged=True))
        network.create_project(db, owner, ProjectCreate(submission_id=uuid4(), property_id=UUID(blocked['id']),
            title='Synthetic open work blocks a custody offer', property_class='house_villa'))
        return {'propertyId': registry['id'], 'propertyVersion': registry['version'], 'fields': dict(FIELDS), 'priorNotes': NOTES,
                'recipientUserCode': recipient.user_code, 'historyProjectId': historical['id'], 'historyProjectTitle': history_title,
                'historyBody': RAW_HISTORY, 'historyMediaId': photo['id'], 'historyMediaSha256': photo['sha256'],
                'historyShareId': share['id'], 'priorRequestId': str(request_id), 'creatorSubmissionId': str(creator_key),
                'blockedPropertyId': blocked['id'], 'blockedPropertyTitle': blocked['title'],
                'labels': {case: f'Synthetic custody fixture {case}' for case in CASES},
                'expiresAt': (instant + timedelta(days=7)).isoformat()}


def observed_state(ids, key, operation):
    from database import SessionLocal
    from models.property_custody import NetworkPropertyCustodyAction, NetworkPropertyCustodyTransfer
    from models.property_network import NetworkProperty

    with SessionLocal() as db:
        actor = ids['owner' if operation in ('offer', 'cancel') else 'loser']
        receipt = db.get(NetworkPropertyCustodyAction, (actor, UUID(key)))
        visit.require(receipt is not None, 'Exact actor command receipt required')
        transfer = db.get(NetworkPropertyCustodyTransfer, receipt.transfer_id)
        property_row = db.get(NetworkProperty, transfer.property_id)
        return {'id': str(transfer.id), 'label': transfer.label, 'operation': receipt.operation,
                'status': transfer.status, 'version': transfer.version, 'receipt_status': receipt.status,
                'receipt_version': receipt.version, 'current_owner': str(property_row.current_custodian_id),
                'property_version': property_row.version, 'custody_version': property_row.custody_version}


def restricted_app(app, accounts, scenario, ids, observed, refused):
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
    from starlette.requests import Request
    from routes.property_network import NO_STORE
    from utils.auth import _decode_token

    actors = {str(identity): role for role, identity in ids.items()}
    login_bodies = {canonical(account) for account in accounts.values()}
    transfers = {}
    active_write = False

    async def wrapped(scope, receive, send):
        nonlocal active_write
        if scope['type'] != 'http' or scope['method'] in {'GET', 'HEAD', 'OPTIONS'}:
            return await app(scope, receive, send)
        req = Request(scope, receive)
        path = scope['path']

        async def reject():
            refused.append(True)
            return await JSONResponse({'detail': 'Mutation unavailable in browser rehearsal'}, status_code=403,
                                      headers=NO_STORE)(scope, receive, send)

        if (req.method != 'POST' or req.url.query or req.headers.get('origin') != visit.DASHBOARD_ORIGIN
                or req.headers.get('authorization') is not None
                or req.headers.get('content-type', '').split(';', 1)[0].lower() != 'application/json'):
            return await reject()
        raw = await req.body()
        try:
            body = json.loads(raw) if len(raw) <= 4096 else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = None
        if not isinstance(body, dict):
            return await reject()

        replayed = False

        async def replay():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {'type': 'http.request', 'body': raw, 'more_body': False}

        if path == '/auth/login':
            if canonical(body) not in login_bodies:
                return await reject()
            return await app(scope, replay, send)
        if active_write or len(observed) >= len(OPERATIONS):
            return await reject()
        case, operation = OPERATIONS[len(observed)]
        role = 'owner' if operation in ('offer', 'cancel') else 'loser'
        cookie = req.cookies.get('ildr_token')
        actor = actors.get(_decode_token(cookie)) if cookie else None
        key = body.get('submission_id')
        if actor != role or not isinstance(key, str) or not UUID_TEXT.fullmatch(key):
            return await reject()
        if operation == 'offer':
            target, expected_status = BASE, 201
            expected = {'submission_id': key, 'property_id': scenario['propertyId'],
                        'expected_property_version': scenario['propertyVersion'],
                        'recipient_user_code': scenario['recipientUserCode'], 'label': scenario['labels'][case],
                        'transfer_acknowledged': True}
        else:
            target, expected_status = f'{BASE}/{transfers[case]}/actions', 200
            expected = {'submission_id': key, 'expected_version': 1, 'operation': operation}
            if operation == 'accept':
                expected['transfer_acknowledged'] = True
        if path != target or canonical(body) != canonical(expected):
            return await reject()

        async def observe(message):
            if message['type'] == 'http.response.start':
                if message['status'] == expected_status:
                    current = await run_in_threadpool(observed_state, ids, key, operation)
                    if operation == 'offer':
                        transfers[case] = current['id']
                    observed.append((case, operation, role, key, current))
                else:
                    refused.append(True)
            await send(message)

        active_write = True
        try:
            return await app(scope, replay, observe)
        finally:
            active_write = False

    return wrapped


def snapshot(db, property_id):
    from sqlalchemy import text

    values = {table: tuple(db.execute(text(
        f"SELECT count(*), md5(coalesce(string_agg(value::text, '' ORDER BY value::text), '')) "
        f"FROM (SELECT to_jsonb(snapshot_row) AS value FROM {table} AS snapshot_row) AS snapshot"
    )).one()) for table in UNAFFECTED}
    values['other_properties'] = tuple(db.execute(text(
        "SELECT count(*), md5(coalesce(string_agg(value::text, '' ORDER BY value::text), '')) "
        "FROM (SELECT to_jsonb(p) AS value FROM network_properties p WHERE p.id<>:id) AS snapshot"
    ), {'id': property_id}).one())
    values['project_lineage'] = tuple(db.execute(text(
        "SELECT count(*), md5(coalesce(string_agg(value::text, '' ORDER BY value::text), '')) "
        "FROM (SELECT to_jsonb(p) - 'closed_custody_title' AS value FROM network_projects p) AS snapshot"
    )).one())
    values['other_project_markers'] = tuple(db.execute(text(
        "SELECT count(*), md5(coalesce(string_agg(value::text, '' ORDER BY value::text), '')) "
        "FROM (SELECT jsonb_build_array(p.id,p.closed_custody_title) AS value FROM network_projects p "
        "WHERE p.property_id IS DISTINCT FROM :id) AS snapshot"
    ), {'id': property_id}).one())
    values['service_request_lineage'] = tuple(db.execute(text(
        "SELECT count(*), md5(coalesce(string_agg(value::text, '' ORDER BY value::text), '')) "
        "FROM (SELECT to_jsonb(r) - 'custody_closed' AS value FROM network_service_requests r) AS snapshot"
    )).one())
    values['other_request_markers'] = tuple(db.execute(text(
        "SELECT count(*), md5(coalesce(string_agg(value::text, '' ORDER BY value::text), '')) "
        "FROM (SELECT jsonb_build_array(r.id,r.custody_closed) AS value FROM network_service_requests r "
        "WHERE r.property_id IS DISTINCT FROM :id) AS snapshot"
    ), {'id': property_id}).one())
    values['user_identities'] = tuple(db.execute(text(
        "SELECT count(*), md5(coalesce(string_agg(value::text, '' ORDER BY value::text), '')) "
        "FROM (SELECT jsonb_build_array(id,email,role,status,email_verified,user_code,display_name) AS value FROM users) AS snapshot"
    )).one())
    return values


def assert_share_gone(db, ids, share_id):
    from fastapi import HTTPException
    from models.user import User
    from services import history_share

    for role in ('owner', 'winner', 'loser'):
        try:
            history_share.detail(db, db.get(User, ids[role]), UUID(share_id))
        except HTTPException as error:
            visit.require(error.status_code == 404, 'Invalidated history grant must be generically unavailable')
        else:
            visit.require(False, 'Original share reopened after property handoff')


def verify_database(scenario, ids, original, observed, refused, transport_attempts, media_state):
    from database import SessionLocal
    from models.history_share import NetworkHistoryShare
    from models.property_custody import NetworkPropertyCustodyAction, NetworkPropertyCustodyTransfer
    from models.property_network import NetworkAudit, NetworkMedia, NetworkProject, NetworkProperty
    from models.service_workflow import NetworkServiceRequest

    visit.require([row[:2] for row in observed] == list(OPERATIONS) and not refused and not transport_attempts,
                  'Six exact custody actions and no unapproved mutation or external transport required')
    for case, operation, _, _, row in observed:
        version = 1 if operation == 'offer' else 2
        expected_owner = ids['loser' if operation == 'accept' else 'owner']
        visit.require(row['label'] == scenario['labels'][case] and row['operation'] == operation
                      and row['version'] == row['receipt_version'] == version
                      and row['status'] == row['receipt_status'] == STATUS[operation]
                      and row['current_owner'] == str(expected_owner)
                      and row['property_version'] == scenario['propertyVersion'] + int(operation == 'accept')
                      and row['custody_version'] == 1 + int(operation == 'accept'),
                      'Exact committed state after each custody transition required')
    property_id = UUID(scenario['propertyId'])
    with SessionLocal() as db:
        property_row = db.get(NetworkProperty, property_id)
        visit.require(property_row.custodian_id == ids['owner'] and property_row.current_custodian_id == ids['loser']
                      and property_row.custody_version == 2 and property_row.version == scenario['propertyVersion'] + 1
                      and property_row.submission_id == UUID(scenario['creatorSubmissionId']) and property_row.notes == ''
                      and {key: getattr(property_row, key) for key in FIELDS} == FIELDS,
                      'Single stable property handoff with approved fields and cleared notes required')
        transfers = {row.label: row for row in db.query(NetworkPropertyCustodyTransfer).all()}
        visit.require(set(transfers) == set(scenario['labels'].values()) and db.query(NetworkPropertyCustodyAction).count() == 6,
                      'Exactly three retained transfer invitations and six actor receipts required')
        for case in CASES:
            row = transfers[scenario['labels'][case]]
            visit.require(row.offered_by_id == ids['owner'] and row.recipient_id == ids['loser'] and row.property_id == property_id
                          and row.property_version == scenario['propertyVersion'] and row.custody_version == 1
                          and row.prior_notes == NOTES and row.fields == FIELDS
                          and row.status == STATUS[case] and row.version == 2 and row.finished_at is not None
                          and row.expires_at.isoformat() == scenario['expiresAt'], 'Exact retained transfer evidence required')
        audit = db.query(NetworkAudit).filter_by(target_kind='property_custody').all()
        visit.require(len(audit) == 6 and sum(row.action == 'accepted' for row in audit) == 1
                      and sum(row.action == 'offered' for row in audit) == 3
                      and {row.action for row in audit} == {'offered', 'declined', 'cancelled', 'accepted'}
                      and all(NOTES not in canonical(row.data) for row in audit), 'Only exact audit transitions without note disclosure allowed')
        history = db.get(NetworkProject, UUID(scenario['historyProjectId']))
        past_projects = db.query(NetworkProject).filter_by(property_id=property_id).all()
        photo = db.get(NetworkMedia, UUID(scenario['historyMediaId']))
        former_request = db.get(NetworkServiceRequest, UUID(scenario['priorRequestId']))
        visit.require(history.owner_id == ids['owner'] and history.status == 'completed' and photo.uploader_id == ids['owner']
                      and len(past_projects) == 2 and all(row.closed_custody_title == FIELDS['title'] for row in past_projects)
                      and former_request.property_id == property_id and former_request.custodian_id == ids['owner']
                      and former_request.custody_closed and former_request.status == 'cancelled' and former_request.version == 2
                      and photo.sha256 == scenario['historyMediaSha256'] and len(media_state) == 1
                      and sha256(media_state[photo.object_key]).hexdigest() == photo.sha256
                      and db.get(NetworkHistoryShare, UUID(scenario['historyShareId'])).invalidated_at is not None,
                      'Exact original history, private bytes and closed share required')
        assert_share_gone(db, ids, scenario['historyShareId'])
        visit.require(snapshot(db, property_id) == original, 'No unrelated accounts, profiles, media or business changes allowed')
    return {'status': 'PASS', 'offers': 3, 'actions': 6, 'effective_transfers': 1,
            'previous_notes_retained_privately': True, 'history_data_unchanged': True, 'old_share_invalidated': True,
            'unexpected_mutations': len(refused), 'external_transport_attempts': len(transport_attempts)}


def run():
    application = exact_source()
    visit.advance('claim_database')
    url = visit.claim_disposable_database()
    visit.advance('configure_application')
    fixture = visit.FIXTURE_FILE
    visit.require(fixture.parent.resolve(strict=True) == fixture.parent and not fixture.exists()
                  and not fixture.is_symlink(), 'Empty private fixture directory required')
    visit.configure_application(application, url)
    visit.advance('load_application')
    import httpx
    import requests
    import uvicorn
    from utils import private_network_media as storage

    transport, observed, refused, media = [], [], [], {}

    def refuse_transport(*_args, **_kwargs):
        transport.append(True)
        raise RuntimeError('Fixture provider transport is disabled')

    def store(key, data):
        visit.require(key not in media and len(media) == 0, 'Only one synthetic private fixture photo allowed')
        media[key] = data

    with (
        patch.object(httpx.HTTPTransport, 'handle_request', refuse_transport),
        patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', refuse_transport),
        patch.object(requests.Session, 'send', refuse_transport),
        patch.object(urllib_request, 'urlopen', refuse_transport),
        patch.object(storage, 'store_image', store),
        patch.object(storage, 'read_image', lambda row: media[row.object_key]),
    ):
        from database import SessionLocal
        from main import app
        from services import property_network as network

        base, ids = visit.migrate_and_seed()
        instant = network.now().replace(microsecond=0)
        with patch.object(network, 'now', return_value=instant):
            visit.advance('seed_private_custody')
            accounts = base['accounts']
            scenario = seed_custody(ids, instant)
            with SessionLocal() as db:
                original = snapshot(db, UUID(scenario['propertyId']))
                assert_share_gone(db, ids, scenario['historyShareId'])
            visit.advance('write_fixture')
            fd = os.open(fixture, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            owned = os.fstat(fd)
            handlers = {}
            try:
                with os.fdopen(fd, 'w', encoding='ascii') as stream:
                    stream.write(canonical({'accounts': accounts, 'custody': scenario}) + '\n')
                visit.advance('serve_application')
                server = uvicorn.Server(uvicorn.Config(restricted_app(app, accounts, scenario, ids, observed, refused),
                    host=visit.HOST, port=visit.API_PORT, lifespan='off', access_log=False, log_level='warning'))

                def finish_shutdown(_number, _frame):
                    server.should_exit = True

                for number in (signal.SIGINT, signal.SIGTERM):
                    handlers[number] = signal.signal(number, finish_shutdown)
                server.run()
                visit.require(server.started, 'Synthetic custody API failed to start')
                visit.advance('verify_private_custody')
                report = verify_database(scenario, ids, original, observed, refused, transport, media)
            finally:
                try:
                    if fixture.exists() or fixture.is_symlink():
                        current = fixture.lstat()
                        if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                            fixture.unlink()
                finally:
                    for number, handler in handlers.items():
                        signal.signal(number, handler)

    visit.require(not fixture.exists() and not fixture.is_symlink(), 'Synthetic login file cleanup failed')
    print('PRIVATE_CUSTODY_BROWSER_FIXTURE_RESULT ' + canonical(report), flush=True)


if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        error_type = type(error).__name__
        frames = [frame.lineno for frame in traceback.extract_tb(error.__traceback__)
                  if frame.filename in {__file__, visit.__file__}]
        print('BROWSER_API_FIXTURE_FAILURE ' + canonical({'stage': visit.stage,
              'error_type': error_type if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', error_type) else 'UnhandledError',
              'fixture_line': frames[-1] if frames else None}), flush=True)
        raise SystemExit(1) from None

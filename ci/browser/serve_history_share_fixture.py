"""Synthetic, loopback-only browser fixture for explicit property history shares."""

from datetime import timedelta
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


APP_COMMIT = '022330abd01c771b833ecd4b6f2baa03410a9fd7'
APP_TREE = '05217dae00cd82ce20243475539d9c1f8f44c9d7'
MIGRATION_HEAD = 'e64a91f327bc'
BASE = '/property-network/history-shares'
EXPIRY_DAYS = 7
CATEGORIES = ['pool_service']
UUID_TEXT = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
BUSINESS_TABLES = (
    'listings', 'network_properties', 'network_project_updates', 'network_project_comments',
    'network_media', 'network_service_requests', 'network_service_recipients',
    'network_service_proposals', 'network_service_work', 'network_service_reviews',
    'network_service_conversation_messages', 'network_service_negotiation_messages',
)
ORDER = (
    ('owner', 'private', 'create', 1), ('winner', 'private', 'accept', 2),
    ('owner', 'private', 'revoke', 3), ('owner', 'listing', 'create', 1),
    ('winner', 'listing', 'accept', 2), ('owner', 'listing', 'member_revoke', None),
    ('owner', 'listing', 'member_regrant', None),
)


def exact_source():
    visit.require((os.environ.get('ILDR_HISTORY_SHARE_APP_COMMIT'), os.environ.get('ILDR_HISTORY_SHARE_APP_TREE'),
                   os.environ.get('ILDR_HISTORY_SHARE_MIGRATION_HEAD')) == (APP_COMMIT, APP_TREE, MIGRATION_HEAD),
                  'Exact history-share source required')
    visit.APP_COMMIT, visit.APP_TREE, visit.MIGRATION_HEAD = APP_COMMIT, APP_TREE, MIGRATION_HEAD
    return visit.verified_checkout()


def seed_scenario(award, ids, instant):
    from database import SessionLocal
    from models.listing import Listing
    from models.property_network import NetworkProject, NetworkProjectMember
    from models.user import User
    from schemas.property_network import MemberWrite, ProgressWrite, ProjectCreate, ProjectEdit, RegistryCreate
    from services import property_network as network, service_visit

    today = instant.astimezone(service_visit.DR_TIMEZONE).date()
    with SessionLocal() as db:
        owner, winner, other, manager = (db.get(User, ids[key]) for key in ('owner', 'winner', 'loser', 'outsider'))
        private = network.create_registry(db, owner, RegistryCreate(
            submission_id=uuid4(), title='Synthetic private history property', location='Synthetic service area',
            property_class='house_villa', relationship='owner', authority_acknowledged=True))
        listing = db.get(Listing, UUID(award['listingId']))

        def project(kind, source_id, title):
            source_key = 'property_id' if kind == 'private' else 'listing_id'
            return network.create_project(db, owner, ProjectCreate(**{source_key: UUID(source_id)},
                submission_id=uuid4(), title=title, summary='Synthetic project scope', property_class='house_villa',
                service_category_ids=CATEGORIES))

        def source(kind, identity, title, anchor_id, anchor_title):
            noun = 'Private' if kind == 'private' else 'Listing'
            past_title = f'Synthetic {noun.lower()} completed history project'
            past = project(kind, identity, past_title)
            past_id = UUID(past['id'])
            raw = f'Synthetic {noun.lower()} internal note, withheld from the share.'
            selected = network.create_update(db, owner, past_id, ProgressWrite(
                submission_id=uuid4(), occurred_on=today - timedelta(days=2), body=raw))
            unselected = network.create_update(db, owner, past_id, ProgressWrite(
                submission_id=uuid4(), occurred_on=today - timedelta(days=3),
                body=f'Synthetic unselected {noun.lower()} maintenance record.'))
            network.edit_project(db, owner, past_id, ProjectEdit(expected_version=past['version'], title=past_title,
                summary='Synthetic project scope', status='completed'))
            network.create_update(db, owner, UUID(anchor_id), ProgressWrite(
                submission_id=uuid4(), occurred_on=today - timedelta(days=1),
                body=f'Synthetic {noun.lower()} active-project decoy, never eligible to share.'))
            return {'sourceKind': kind, 'sourceId': identity, 'sourceTitle': title, 'anchorProjectId': anchor_id,
                    'anchorProjectTitle': anchor_title, 'historyProjectId': past['id'], 'historyProjectTitle': past_title,
                    'selectedUpdateId': selected['id'], 'unselectedUpdateId': unselected['id'],
                    'selectedOn': selected['occurred_on'].isoformat(), 'categoryIds': list(CATEGORIES),
                    'shareLabel': f'Synthetic {noun.lower()} pool history',
                    'sharedSummary': f'Synthetic redacted {noun.lower()} pool maintenance summary.', 'rawBody': raw}

        anchor_title = 'Synthetic private active history project'
        anchor = project('private', private['id'], anchor_title)
        anchor_id = UUID(anchor['id'])
        for offset, (actor, role) in enumerate(((winner, 'professional'), (other, 'viewer'), (manager, 'manager'))):
            network.assign(db, owner, anchor_id, MemberWrite(expected_version=anchor['version'] + offset,
                user_code=actor.user_code, role=role))
        sibling = project('private', private['id'], 'Synthetic other active project on the private property')
        network.assign(db, owner, UUID(sibling['id']), MemberWrite(expected_version=sibling['version'],
            user_code=other.user_code, role='professional'))

        private_source = source('private', private['id'], private['title'], anchor['id'], anchor_title)
        listing_source = source('listing', award['listingId'], listing.title, award['projectId'], award['projectTitle'])
        listing_anchor = db.get(NetworkProject, UUID(award['projectId']))
        membership = db.query(NetworkProjectMember).filter_by(project_id=listing_anchor.id, user_id=winner.id).one()
        past_ids = {UUID(private_source['historyProjectId']), UUID(listing_source['historyProjectId'])}
        visit.require(membership.active and membership.role == 'professional' and membership.reminder_grant_version == 1
                      and not db.query(NetworkProjectMember).filter(NetworkProjectMember.project_id.in_(past_ids)).count(),
                      'Exact project memberships required')
        return {'private': private_source, 'listing': listing_source, 'winnerUserCode': winner.user_code,
                'otherProfessionalUserCode': other.user_code, 'managerUserCode': manager.user_code,
                'listingAnchorVersion': listing_anchor.version, 'expiresInDays': EXPIRY_DAYS, 'asOf': today.isoformat(),
                'expiresAt': (instant + timedelta(days=EXPIRY_DAYS)).isoformat()}


def canonical(body):
    return json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def source_payload(scenario, kind):
    source = scenario[kind]
    return {'source_kind': kind, 'source_id': source['sourceId'], 'anchor_project_id': source['anchorProjectId'],
            'recipient_user_code': scenario['winnerUserCode'], 'label': source['shareLabel'], 'expires_in_days': EXPIRY_DAYS,
            'items': [{'update_id': source['selectedUpdateId'], 'summary': source['sharedSummary']}]}


def observed_state(scenario, ids, kind, operation, submission_id=None):
    from database import SessionLocal
    from models.history_share import NetworkHistoryShare, NetworkHistoryShareAction
    from models.property_network import NetworkProject, NetworkProjectMember

    with SessionLocal() as db:
        if operation.startswith('member_'):
            anchor = db.get(NetworkProject, UUID(scenario['listing']['anchorProjectId']))
            member = db.query(NetworkProjectMember).filter_by(project_id=anchor.id, user_id=ids['winner']).one()
            return {'active': member.active, 'role': member.role, 'grant_version': member.reminder_grant_version,
                    'project_version': anchor.version}
        share = db.query(NetworkHistoryShare).filter_by(owner_id=ids['owner'], label=scenario[kind]['shareLabel']).one()
        actor = ids['winner'] if operation == 'accept' else ids['owner']
        receipt = db.get(NetworkHistoryShareAction, (actor, UUID(submission_id)))
        visit.require(receipt is not None and receipt.share_id == share.id, 'Exact actor-owned command receipt required')
        return {'id': str(share.id), 'status': share.status, 'version': share.version,
                'operation': receipt.operation, 'receipt_status': receipt.status, 'receipt_version': receipt.version}


def restricted_app(app, accounts, scenario, ids, observed, refused):
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
    from starlette.requests import Request
    from routes.property_network import NO_STORE
    from utils.auth import _decode_token

    actors = {str(identity): role for role, identity in ids.items()}
    members = f"/property-network/projects/{scenario['listing']['anchorProjectId']}/members"
    member_revoke = f"{members}/{scenario['winnerUserCode']}/revoke"
    login_bodies = {canonical(account) for account in accounts.values()}
    share_ids = {}
    running = False

    def expected_body(kind, operation):
        version = scenario['listingAnchorVersion']
        if operation == 'create':
            return BASE, source_payload(scenario, kind), 201
        if operation == 'member_revoke':
            return member_revoke, {'expected_version': version}, 200
        if operation == 'member_regrant':
            return members, {'expected_version': version + 1, 'role': 'professional', 'user_code': scenario['winnerUserCode']}, 200
        return f'{BASE}/{share_ids[kind]}/actions', {'operation': operation, 'expected_version': 1 if operation == 'accept' else 2}, 200

    async def wrapped(scope, receive, send):
        nonlocal running
        if scope['type'] != 'http' or scope['method'] in {'GET', 'HEAD', 'OPTIONS'}:
            return await app(scope, receive, send)
        request = Request(scope, receive)
        path, method = scope['path'], scope['method']

        async def reject():
            refused.append(True)
            return await JSONResponse({'detail': 'Mutation unavailable in browser rehearsal'}, status_code=403,
                                      headers=NO_STORE)(scope, receive, send)

        if (method != 'POST' or request.headers.get('origin') != visit.DASHBOARD_ORIGIN
                or request.headers.get('authorization') is not None or request.url.query
                or request.headers.get('content-type', '').split(';', 1)[0].lower() != 'application/json'):
            return await reject()
        raw = await request.body()
        try:
            body = json.loads(raw) if len(raw) <= 4096 else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = None
        if not isinstance(body, dict):
            return await reject()

        replayed = False

        async def replay_body():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {'type': 'http.request', 'body': raw, 'more_body': False}

        if path == '/auth/login':
            if canonical(body) not in login_bodies:
                return await reject()
            return await app(scope, replay_body, send)
        if running or len(observed) >= len(ORDER):
            return await reject()
        role, kind, operation, version = ORDER[len(observed)]
        cookie = request.cookies.get('ildr_token')
        actor = actors.get(_decode_token(cookie)) if cookie else None
        target, expected, expected_status = expected_body(kind, operation)
        submission_id = body.get('submission_id')
        if version is not None:
            if not isinstance(submission_id, str) or not UUID_TEXT.fullmatch(submission_id):
                return await reject()
            expected = {**expected, 'submission_id': submission_id}
        if actor != role or path != target or canonical(body) != canonical(expected):
            return await reject()

        async def observe_response(message):
            if message['type'] == 'http.response.start':
                if message['status'] == expected_status:
                    state = await run_in_threadpool(observed_state, scenario, ids, kind, operation, submission_id)
                    if operation == 'create':
                        share_ids[kind] = state['id']
                    observed.append((role, kind, operation, version, submission_id, state))
                else:
                    refused.append(True)
            await send(message)

        running = True
        try:
            return await app(scope, replay_body, observe_response)
        finally:
            running = False

    return wrapped


def snapshot(db):
    from sqlalchemy import text

    return {table: tuple(db.execute(text(
        f"SELECT count(*), md5(coalesce(string_agg(value::text, '' ORDER BY value::text), '')) "
        f"FROM (SELECT to_jsonb(snapshot_row) AS value FROM {table} AS snapshot_row) AS snapshot"
    )).one()) for table in BUSINESS_TABLES}


def verify_database(scenario, ids, observed, original, refused, transport_attempts):
    from database import SessionLocal
    from fastapi import HTTPException
    from models.history_share import NetworkHistoryShare, NetworkHistoryShareAction
    from models.property_network import NetworkAudit, NetworkProject, NetworkProjectMember
    from models.user import User
    from services import history_share

    visit.require([event[:4] for event in observed] == list(ORDER) and not refused and not transport_attempts,
                  'Exact browser history writes with no external transport required')
    write_status = {'create': 'pending', 'accept': 'accepted', 'revoke': 'revoked'}
    for _, _, operation, version, _, state in observed[:5]:
        visit.require(state['version'] == state['receipt_version'] == version and state['operation'] == operation
                      and state['status'] == state['receipt_status'] == write_status[operation],
                      'Exact committed history share transition required')
    for offset, event in enumerate(observed[5:], start=1):
        visit.require(event[-1] == {'active': offset == 2, 'role': 'professional', 'grant_version': 1 + offset,
                                  'project_version': scenario['listingAnchorVersion'] + offset},
                      'Exact original assignment revoke and regrant required')

    with SessionLocal() as db:
        owner, winner = (db.get(User, ids[key]) for key in ('owner', 'winner'))
        by_label = {row.label: row for row in db.query(NetworkHistoryShare).all()}
        visit.require(set(by_label) == {scenario[kind]['shareLabel'] for kind in ('private', 'listing')}
                      and db.query(NetworkHistoryShareAction).count() == 5, 'Exact history share and receipt rows required')
        for kind, status, version in [('private', 'revoked', 3), ('listing', 'accepted', 2)]:
            source = scenario[kind]
            row = by_label[source['shareLabel']]
            expected_scope = (UUID(source['sourceId']), None) if kind == 'private' else (None, UUID(source['sourceId']))
            item = {'update_id': source['selectedUpdateId'], 'occurred_on': source['selectedOn'],
                    'service_category_ids': source['categoryIds'], 'summary': source['sharedSummary']}
            visit.require((row.property_id, row.listing_id) == expected_scope and row.anchor_project_id == UUID(source['anchorProjectId'])
                          and row.owner_id == owner.id and row.recipient_id == winner.id and row.items == [item]
                          and row.status == status and row.version == version and row.member_version == 1
                          and row.expires_at.isoformat() == scenario['expiresAt'], 'Exact historical share scope and text required')
            for viewer in [winner] if kind == 'private' else [winner, owner]:
                try:
                    history_share.detail(db, viewer, row.id)
                except HTTPException as error:
                    visit.require(error.status_code == 404, 'Revoked history is generically unavailable')
                else:
                    visit.require(False, 'Revoked or old-grant history share remained visible')
        old_member = by_label[scenario['listing']['shareLabel']].member_id
        current_member = db.get(NetworkProjectMember, old_member)
        current_anchor = db.get(NetworkProject, UUID(scenario['listing']['anchorProjectId']))
        visit.require(current_member.active and current_member.reminder_grant_version == 3
                      and current_anchor.version == scenario['listingAnchorVersion'] + 2,
                      'Renewed assignment must not reuse original grant version')
        past = {UUID(scenario[kind]['historyProjectId']) for kind in ('private', 'listing')}
        visit.require(not db.query(NetworkProjectMember).filter(NetworkProjectMember.project_id.in_(past)).count(),
                      'No underlying history project membership granted')
        for role, kind, operation, version, key, state in observed[:5]:
            actor = owner if role == 'owner' else winner
            receipt = history_share.own_submission(db, actor, UUID(key))
            visit.require(receipt == {'id': state['id'], 'operation': operation, 'status': write_status[operation], 'version': version},
                          'Retained minimal actor receipt required after closure')
        membership_audits = db.query(NetworkAudit).filter(NetworkAudit.target_id == current_anchor.id,
            NetworkAudit.action.in_(['member_revoked', 'member_assigned'])).all()
        visit.require({(audit.actor_id, audit.action, audit.data.get('member_code')) for audit in membership_audits} == {
            (owner.id, 'member_revoked', scenario['winnerUserCode']), (owner.id, 'member_assigned', scenario['winnerUserCode'])}
            and len(membership_audits) == 2 and snapshot(db) == original, 'No unrequested property or history changes allowed')
    return {'status': 'PASS', 'history_shares': 2, 'history_actions': 5, 'membership_writes': 2,
            'explicit_revocation_closed': True, 'regrant_kept_old_share_closed': True, 'original_history_unchanged': True,
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

    transport_attempts, observed, refused = [], [], []

    def refuse_transport(*_args, **_kwargs):
        transport_attempts.append(True)
        raise RuntimeError('Fixture provider transport is disabled')

    with (
        patch.object(httpx.HTTPTransport, 'handle_request', refuse_transport),
        patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', refuse_transport),
        patch.object(requests.Session, 'send', refuse_transport),
        patch.object(urllib_request, 'urlopen', refuse_transport),
    ):
        from database import SessionLocal
        from main import app
        from services import property_network as network

        base, ids = visit.migrate_and_seed()
        instant = network.now().replace(microsecond=0)
        with patch.object(network, 'now', return_value=instant):
            visit.advance('seed_history_shares')
            accounts = base['accounts']
            scenario = seed_scenario(base['serviceVisit'], ids, instant)
            with SessionLocal() as db:
                original = snapshot(db)
            visit.advance('write_fixture')
            fd = os.open(fixture, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            owned = os.fstat(fd)
            previous_handlers = {}
            try:
                with os.fdopen(fd, 'w', encoding='ascii') as stream:
                    json.dump({'accounts': accounts, 'historyShare': scenario}, stream, ensure_ascii=True)
                    stream.write('\n')
                visit.advance('serve_application')
                server = uvicorn.Server(uvicorn.Config(restricted_app(app, accounts, scenario, ids, observed, refused),
                    host=visit.HOST, port=visit.API_PORT, lifespan='off', access_log=False, log_level='warning'))

                def finish_shutdown(_number, _frame):
                    server.should_exit = True

                for number in (signal.SIGINT, signal.SIGTERM):
                    previous_handlers[number] = signal.signal(number, finish_shutdown)
                server.run()
                visit.require(server.started, 'Synthetic history-share API failed to start')
                visit.advance('verify_history_shares')
                report = verify_database(scenario, ids, observed, original, refused, transport_attempts)
            finally:
                try:
                    if fixture.exists() or fixture.is_symlink():
                        current = fixture.lstat()
                        if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                            fixture.unlink()
                finally:
                    for number, handler in previous_handlers.items():
                        signal.signal(number, handler)

    visit.require(not fixture.exists() and not fixture.is_symlink(), 'Synthetic login file cleanup failed')
    print('HISTORY_SHARE_BROWSER_FIXTURE_RESULT ' + canonical(report), flush=True)


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

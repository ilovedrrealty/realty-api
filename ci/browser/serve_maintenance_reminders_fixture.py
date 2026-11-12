"""Serve an owner-only maintenance rehearsal on disposable loopback Postgres."""

import json
import os
import re
import signal
import stat
import sys
import traceback
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib import request as urllib_request
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_service_visit_fixture as visit


LISTING_TITLE = 'Browser rehearsal villa'
PRIVATE_TITLE = 'Browser rehearsal private maintenance property'
PRIVATE_LOCATION = 'Synthetic maintenance service area'
PRIVATE_SCHEDULE = 'Limpieza de piscina de prueba'
LISTING_SCHEDULE = 'Synthetic filter inspection'
REMINDER_BASE = '/property-network/maintenance-reminders'
UUID_PATTERN = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
SNAPSHOT_TABLES = (
    'listings', 'network_properties', 'network_audit', 'network_projects', 'network_project_members',
    'network_project_updates', 'network_service_preferences', 'network_service_requests',
    'network_service_recipients', 'network_service_proposals', 'network_service_actions', 'network_service_work',
    'network_service_work_actions', 'network_service_reviews', 'network_service_favorites',
    'network_service_negotiation_messages', 'network_service_conversation_messages',
)


def exact_source():
    commit = os.environ.get('ILDR_MAINTENANCE_APP_COMMIT', '')
    tree = os.environ.get('ILDR_MAINTENANCE_APP_TREE', '')
    migration = os.environ.get('ILDR_MAINTENANCE_MIGRATION_HEAD', '')
    visit.require(all(re.fullmatch('[0-9a-f]{40}', value) for value in (commit, tree))
                  and re.fullmatch('[0-9a-f]{12}', migration), 'Exact maintenance reminder source required')
    visit.APP_COMMIT = commit
    visit.APP_TREE = tree
    visit.MIGRATION_HEAD = migration
    return visit.verified_checkout()


def seed_sources(award, users):
    from database import SessionLocal
    from models.listing import Listing
    from models.maintenance_reminders import (
        NetworkMaintenanceAction, NetworkMaintenanceOccurrence, NetworkMaintenancePreference, NetworkMaintenanceSchedule,
    )
    from models.property_network import NetworkProjectMember
    from models.user import User
    from schemas.property_network import MemberWrite, ProjectCreate, RegistryCreate
    from services import property_network as network

    with SessionLocal() as db:
        owner = db.get(User, users['owner'])
        outsider = db.get(User, users['outsider'])
        listing_id = UUID(award['listingId'])
        listing_project_id = UUID(award['projectId'])
        listing = db.get(Listing, listing_id)
        visit.require(listing is not None and listing.owner_id == owner.id and listing.status == 'active'
                      and listing.title == LISTING_TITLE, 'Exact active owner listing required')
        member = db.query(NetworkProjectMember).filter_by(project_id=listing_project_id, user_id=users['winner']).one()
        visit.require(member.active is True and member.role == 'professional', 'Existing listing project access required')

        private = network.create_registry(db, owner, RegistryCreate(
            submission_id=uuid4(), title=PRIVATE_TITLE, location=PRIVATE_LOCATION,
            property_class='house_villa', relationship='owner', authority_acknowledged=True,
        ))
        project = network.create_project(db, owner, ProjectCreate(
            submission_id=uuid4(), property_id=UUID(private['id']), title='Synthetic private maintenance project',
            property_class='house_villa', service_category_ids=['pool_service'],
        ))
        private_project_id = UUID(project['id'])
        network.assign(db, owner, private_project_id, MemberWrite(expected_version=project['version'],
                       user_code=award['winnerUserCode'], role='professional'))
        network.assign(db, owner, private_project_id, MemberWrite(expected_version=project['version'] + 1,
                       user_code=outsider.user_code, role='viewer'))
        active = db.query(NetworkProjectMember).filter_by(project_id=private_project_id, active=True).all()
        visit.require({row.user_id: row.role for row in active}
                      == {users['winner']: 'professional', users['outsider']: 'viewer'},
                      'Real noncustodian private project members required')
        visit.require(all(db.query(model).count() == 0 for model in (NetworkMaintenancePreference,
                      NetworkMaintenanceSchedule, NetworkMaintenanceOccurrence, NetworkMaintenanceAction)),
                      'Reminder defaults must start off with no persisted rows')
    return {
        'listing': {'sourceKind': 'listing', 'sourceId': str(listing_id), 'projectId': str(listing_project_id),
                    'title': LISTING_TITLE},
        'private': {'sourceKind': 'private', 'sourceId': private['id'], 'projectId': str(private_project_id),
                    'title': PRIVATE_TITLE, 'version': private['version']},
    }


def fixture_dates():
    from services.property_management import today_dr

    today = today_dr()
    anchor = today - timedelta(days=7)
    year = anchor.year + (anchor.month == 12)
    month = anchor.month % 12 + 1
    following = date(year, month, min(anchor.day, monthrange(year, month)[1]))
    visit.require(anchor < today < following, 'Past occurrence and next anchored date required')
    return {'todayOn': today.isoformat(), 'privateFirstDueOn': anchor.isoformat(),
            'privateSnoozeOn': (today + timedelta(days=2)).isoformat(), 'privateNextDueOn': following.isoformat(),
            'listingFirstDueOn': today.isoformat()}


def snapshot(db):
    from sqlalchemy import text

    return {table: db.execute(text(f'SELECT to_jsonb(snapshot_row) FROM {table} AS snapshot_row')).scalars().all()
            for table in SNAPSHOT_TABLES}


def observe_schedule(schedule_id):
    from database import SessionLocal
    from models.maintenance_reminders import NetworkMaintenanceOccurrence, NetworkMaintenanceSchedule

    with SessionLocal() as db:
        identity = UUID(schedule_id)
        schedule = db.get(NetworkMaintenanceSchedule, identity)
        if schedule is None:
            return identity, None, None, None, None
        occurrence = db.query(NetworkMaintenanceOccurrence).filter_by(schedule_id=identity, status='open').one_or_none()
        return identity, schedule.version, schedule.status, occurrence.due_on if occurrence else None, (
            occurrence.snoozed_until if occurrence else None)


def restricted_app(app, sources, owner_id, observed):
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
    from starlette.requests import Request

    from routes.property_network import NO_STORE
    from utils.auth import _decode_token

    bases = tuple(f"{REMINDER_BASE}/{kind}/{sources[kind]['sourceId']}" for kind in ('listing', 'private'))
    fixed = {('POST', '/auth/login')}
    for base in bases:
        fixed.update({('POST', base), ('PUT', f'{base}/preferences')})
    actions = re.compile(r'(?:' + '|'.join(re.escape(base) for base in bases) + rf')/({UUID_PATTERN})/actions')
    archive = f"/property-network/registry/{sources['private']['sourceId']}"

    async def serve(scope, receive, send):
        if scope['type'] != 'http' or scope['method'] in {'GET', 'HEAD', 'OPTIONS'}:
            return await app(scope, receive, send)
        method, path = scope['method'], scope['path']
        if (method, path) in fixed:
            return await app(scope, receive, send)
        if (method, path) == ('PUT', archive):
            request = Request(scope)
            token = request.cookies.get('ildr_token')
            if token and not request.headers.get('authorization') and _decode_token(token) == str(owner_id):
                return await app(scope, receive, send)
        action = actions.fullmatch(path) if method == 'POST' else None
        if action is None:
            refused = JSONResponse({'detail': 'Mutation unavailable in browser rehearsal'}, status_code=405, headers=NO_STORE)
            return await refused(scope, receive, send)

        async def observe_response(message):
            if message['type'] == 'http.response.start' and 200 <= message['status'] < 300:
                observed.add(await run_in_threadpool(observe_schedule, action.group(1)))
            await send(message)

        return await app(scope, receive, observe_response)

    return serve


def verify_lifecycle(sources, dates, owner_id, observed):
    from database import SessionLocal
    from models.maintenance_reminders import (
        NetworkMaintenanceAction as Action, NetworkMaintenanceOccurrence as Occurrence,
        NetworkMaintenancePreference as Preference, NetworkMaintenanceSchedule as Schedule,
    )
    from services.property_management import today_dr

    private_id, listing_id = (UUID(sources[kind]['sourceId']) for kind in ('private', 'listing'))
    private_first = date.fromisoformat(dates['privateFirstDueOn'])
    private_next = date.fromisoformat(dates['privateNextDueOn'])
    private_snooze = date.fromisoformat(dates['privateSnoozeOn'])
    listing_first = date.fromisoformat(dates['listingFirstDueOn'])
    allowed_closed_dates = {date.fromisoformat(dates['todayOn']), today_dr()}
    with SessionLocal() as db:
        preferences = db.query(Preference).all()
        visit.require(len(preferences) == 2 and all(row.actor_id == owner_id for row in preferences),
                      'Exactly two owner reminder preferences required')
        private_pref, = [row for row in preferences if row.property_id == private_id]
        listing_pref, = [row for row in preferences if row.listing_id == listing_id]
        visit.require((private_pref.enabled, private_pref.lead_days, private_pref.version) == (True, 3, 1)
                      and (listing_pref.enabled, listing_pref.lead_days, listing_pref.version) == (False, 5, 2),
                      'Exact explicit reminder preference changes required')

        schedules = db.query(Schedule).all()
        visit.require(len(schedules) == 2, 'Exactly two schedules required')
        private, = [row for row in schedules if row.preference_id == private_pref.id]
        listing, = [row for row in schedules if row.preference_id == listing_pref.id]
        visit.require((private.title, private.repeat, private.anchor_on, private.status, private.version)
                      == (PRIVATE_SCHEDULE, 'monthly', private_first, 'stopped', 6)
                      and (listing.title, listing.repeat, listing.anchor_on, listing.status, listing.version)
                      == (LISTING_SCHEDULE, 'none', listing_first, 'completed', 2),
                      'Exact monthly and one-time schedule lifecycle required')

        occurrences = db.query(Occurrence).all()
        visit.require(len(occurrences) == 3 and all(row.snoozed_until is None and row.closed_on in allowed_closed_dates
                      for row in occurrences) and {(row.schedule_id, row.due_on, row.status) for row in occurrences}
                      == {(private.id, private_first, 'completed'), (private.id, private_next, 'stopped'),
                          (listing.id, listing_first, 'completed')}, 'Exact retained occurrence history required')

        actions = db.query(Action).all()
        visit.require(len(actions) == 11 and all(row.actor_id == owner_id and row.submission_id.version == 4 for row in actions),
                      'All reminder actions must be explicit and owner-scoped')
        preferences_actions = [row.preference_id for row in actions if row.operation == 'preferences' and row.schedule_id is None]
        visit.require(preferences_actions.count(private_pref.id) == 1 and preferences_actions.count(listing_pref.id) == 2,
                      'Exact preference action inventory required')
        schedule_actions = [(row.preference_id, row.schedule_id, row.operation) for row in actions if row.schedule_id is not None]
        expected = {(private_pref.id, private.id, action) for action in ('create', 'snooze', 'complete', 'pause', 'resume', 'stop')}
        expected.update((listing_pref.id, listing.id, action) for action in ('create', 'complete'))
        visit.require(len(schedule_actions) == 8 and set(schedule_actions) == expected, 'Exact schedule action inventory required')

        expected_states = {
            (private.id, 2, 'active', private_first, private_snooze),
            (private.id, 3, 'active', private_next, None),
            (private.id, 4, 'paused', private_next, None),
            (private.id, 5, 'active', private_next, None),
            (private.id, 6, 'stopped', None, None),
            (listing.id, 2, 'completed', None, None),
        }
        visit.require(observed == expected_states, 'Intermediate states must match each successful schedule action')


def verify_database(sources, dates, owner_id, original, observed, transport_attempts):
    from database import SessionLocal

    verify_lifecycle(sources, dates, owner_id, observed)
    with SessionLocal() as db:
        current = snapshot(db)
    private_id = sources['private']['sourceId']
    before, = [row for row in original['network_properties'] if row['id'] == private_id]
    after, = [row for row in current['network_properties'] if row['id'] == private_id]
    visit.require(before['status'] == 'active' and after['status'] == 'archived' and after['version'] == before['version'] + 1,
                  'Only the original private registry source must be archived')
    current['network_properties'].remove(after)
    current['network_properties'].append({**after, 'status': before['status'], 'version': before['version'],
                                          'updated_at': before['updated_at']})
    original_audit = {row['id'] for row in original['network_audit']}
    audit, = [row for row in current['network_audit'] if row['id'] not in original_audit]
    visit.require(audit['actor_id'] == str(owner_id) and audit['target_kind'] == 'registry_property'
                  and audit['target_id'] == private_id and audit['action'] == 'updated'
                  and audit['data'] == {'version': after['version'], 'status': 'archived'},
                  'Only the intended registry archive audit may be added')
    current['network_audit'].remove(audit)
    for table in SNAPSHOT_TABLES:
        visit.require(sorted(json.dumps(row, sort_keys=True) for row in current[table])
                      == sorted(json.dumps(row, sort_keys=True) for row in original[table]),
                      'Existing synthetic business rows must otherwise stay unchanged')
    visit.require(not transport_attempts, 'No external transport allowed')
    return {'status': 'PASS', 'preference_rows': 2, 'preference_actions': 3, 'schedules': 2, 'schedule_actions': 8,
            'retained_occurrences': 3, 'private_schedule_stopped': True, 'listing_schedule_completed': True,
            'listing_reminders_disabled': True, 'private_source_archived': True, 'new_service_requests': 0,
            'new_service_messages': 0, 'external_transport_attempts': 0}


def run():
    application = exact_source()
    visit.advance('claim_database')
    url = visit.claim_disposable_database()
    visit.advance('configure_application')
    fixture_file = visit.FIXTURE_FILE
    visit.require(fixture_file.parent.resolve(strict=True) == fixture_file.parent
                  and not fixture_file.exists() and not fixture_file.is_symlink(), 'Empty private fixture directory required')
    visit.configure_application(application, url)
    visit.advance('load_application')
    import httpx
    import requests
    import uvicorn

    transport_attempts = []
    observed = set()

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

        base, users = visit.migrate_and_seed()
        visit.advance('seed_maintenance_sources')
        sources = seed_sources(base['serviceVisit'], users)
        dates = fixture_dates()
        with SessionLocal() as db:
            original = snapshot(db)
        visit.advance('write_fixture')
        fd = os.open(fixture_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        owned = os.fstat(fd)
        previous_handlers = {}
        try:
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                json.dump({'accounts': base['accounts'], 'maintenanceReminders': {**sources, 'dates': dates}},
                          stream, ensure_ascii=True)
                stream.write('\n')
            visit.advance('serve_application')
            server = uvicorn.Server(uvicorn.Config(restricted_app(app, sources, users['owner'], observed), host=visit.HOST,
                                                   port=visit.API_PORT, lifespan='off', access_log=False, log_level='warning'))

            def finish_shutdown(_number, _frame):
                server.should_exit = True

            for number in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[number] = signal.signal(number, finish_shutdown)
            server.run()
            visit.require(server.started, 'Synthetic maintenance reminder API failed to start')
            visit.advance('verify_maintenance_reminders')
            report = verify_database(sources, dates, users['owner'], original, observed, transport_attempts)
        finally:
            try:
                if fixture_file.exists() or fixture_file.is_symlink():
                    current = fixture_file.lstat()
                    if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                        fixture_file.unlink()
            finally:
                for number, handler in previous_handlers.items():
                    signal.signal(number, handler)
    visit.require(not fixture_file.exists() and not fixture_file.is_symlink(), 'Synthetic login file cleanup failed')
    print('MAINTENANCE_REMINDERS_BROWSER_FIXTURE_RESULT ' + json.dumps(report, sort_keys=True, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        error_type = type(error).__name__
        frames = [frame.lineno for frame in traceback.extract_tb(error.__traceback__)
                  if frame.filename in {__file__, visit.__file__}]
        print('BROWSER_API_FIXTURE_FAILURE ' + json.dumps({'stage': visit.stage,
              'error_type': error_type if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', error_type) else 'UnhandledError',
              'fixture_line': frames[-1] if frames else None}), flush=True)
        raise SystemExit(1) from None

"""Serve and verify a private project reminder rehearsal on disposable Postgres."""

from collections import Counter
from datetime import date, datetime, time, timedelta
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


MIGRATION_HEAD = 'a6e4c2b8d517'
MILESTONE_TITLE = 'Synthetic pool readiness milestone'
DURATION = 60
UNCHANGED_TABLES = (
    'listings', 'network_properties', 'network_milestones', 'network_project_updates', 'network_project_comments',
    'network_service_preferences', 'network_service_requests', 'network_service_recipients',
    'network_service_proposals', 'network_service_actions', 'network_service_reviews', 'network_service_favorites',
    'network_service_negotiation_messages', 'network_service_conversation_messages', 'network_media',
    'network_maintenance_preferences', 'network_maintenance_schedules', 'network_maintenance_occurrences',
    'network_maintenance_actions',
)


def exact_source():
    commit = os.environ.get('ILDR_DUE_WORK_APP_COMMIT', '')
    tree = os.environ.get('ILDR_DUE_WORK_APP_TREE', '')
    visit.require(all(re.fullmatch('[0-9a-f]{40}', value) for value in (commit, tree))
                  and os.environ.get('ILDR_DUE_WORK_MIGRATION_HEAD') == MIGRATION_HEAD,
                  'Exact due-work reminder source required')
    visit.APP_COMMIT, visit.APP_TREE, visit.MIGRATION_HEAD = commit, tree, MIGRATION_HEAD
    return visit.verified_checkout()


def seed_project(award, ids, instant):
    from database import SessionLocal
    from models.property_network import NetworkProject, NetworkProjectMember
    from models.user import User
    from schemas.property_network import MemberWrite, MilestoneCreate
    from schemas.service_visit import ServiceWorkWrite
    from services import property_network as network, service_visit

    project_id, request_id = UUID(award['projectId']), UUID(award['requestId'])
    tz = service_visit.DR_TIMEZONE
    today = instant.astimezone(tz).date()
    initial = datetime.combine(today + timedelta(days=1), time(10), tz)
    next_visit = datetime.combine(today + timedelta(days=2), time(11), tz)
    milestone_day = today - timedelta(days=1)
    with SessionLocal() as db:
        owner, winner, other, viewer = (db.get(User, ids[key]) for key in ('owner', 'winner', 'loser', 'outsider'))
        project = db.get(NetworkProject, project_id)
        version = project.version
        network.assign(db, owner, project_id, MemberWrite(expected_version=version, user_code=other.user_code,
                                                           role='professional'))
        network.assign(db, owner, project_id, MemberWrite(expected_version=version + 1, user_code=viewer.user_code,
                                                           role='viewer'))
        milestone = network.create_milestone(db, owner, project_id, MilestoneCreate(title=MILESTONE_TITLE,
                                                                                     due_date=milestone_day))
        proposed = service_visit.mutate_work(db, owner, request_id, ServiceWorkWrite(submission_id=uuid4(),
            expected_version=0, operation='propose_visit', starts_at=initial, duration_minutes=DURATION))
        confirmed = service_visit.mutate_work(db, winner, request_id, ServiceWorkWrite(submission_id=uuid4(),
            expected_version=proposed['version'], operation='accept_visit'))
        members = {row.user_id: (row.role, row.active, row.reminder_grant_version)
                   for row in db.query(NetworkProjectMember).filter_by(project_id=project_id)}
        visit.require(members == {winner.id: ('professional', True, 1), other.id: ('professional', True, 1),
                                  viewer.id: ('viewer', True, 1)} and project.version == version + 2,
                      'Exact project member roles required')
        visit.require(milestone['status'] == 'open' and milestone['version'] == 1
                      and confirmed['status'] == 'confirmed' and confirmed['version'] == 2,
                      'Canonical dated milestone and initial confirmed visit required')
        return {
            'projectId': award['projectId'], 'projectTitle': award['projectTitle'], 'projectVersion': project.version,
            'requestId': award['requestId'], 'requestTitle': award['title'],
            'milestoneId': milestone['id'], 'milestoneTitle': MILESTONE_TITLE,
            'winnerUserCode': winner.user_code, 'otherProfessionalUserCode': other.user_code,
            'viewerUserCode': viewer.user_code, 'durationMinutes': DURATION,
            'dates': {'todayOn': today.isoformat(), 'milestoneDueOn': milestone_day.isoformat(),
                      'snoozeOn': next_visit.date().isoformat(), 'initialVisitStart': initial.isoformat(),
                      'initialVisitDueOn': initial.date().isoformat(), 'rescheduledVisitStart': next_visit.isoformat(),
                      'rescheduledVisitDueOn': next_visit.date().isoformat()},
        }


def observed_workflow(db, scenario, ids):
    from models.property_network import NetworkProject, NetworkProjectMember
    from models.service_visit import NetworkServiceWork

    project_id = UUID(scenario['projectId'])
    work = db.get(NetworkServiceWork, UUID(scenario['requestId']))
    member = db.query(NetworkProjectMember).filter_by(project_id=project_id, user_id=ids['winner']).one()
    project = db.get(NetworkProject, project_id)
    visit.require(member.role == 'professional' and work.confirmed_duration_minutes == DURATION
                  and work.proposed_duration_minutes in (None, DURATION)
                  and all(value is None for value in (work.completion_note, work.completed_on,
                      work.completion_review_note, work.completion_update_id)), 'No completion or unrelated work changes')
    return (work.status, work.version, work.confirmed_reminder_version, work.confirmed_starts_at,
            work.proposed_starts_at, work.proposed_by, project.version, member.active, member.reminder_grant_version)


def read_workflow(scenario, ids):
    from database import SessionLocal

    with SessionLocal() as db:
        return observed_workflow(db, scenario, ids)


def expected_workflow(scenario):
    dates, version = scenario['dates'], scenario['projectVersion']
    initial, later = (datetime.fromisoformat(dates[key]) for key in ('initialVisitStart', 'rescheduledVisitStart'))
    return {
        'seed': ('confirmed', 2, 2, initial, None, None, version, True, 1),
        'propose_visit': ('proposed', 3, 2, initial, later, 'owner', version, True, 1),
        'accept_visit': ('confirmed', 4, 4, later, None, None, version, True, 1),
        'revoke': ('confirmed', 4, 4, later, None, None, version + 1, False, 2),
        'regrant': ('confirmed', 4, 4, later, None, None, version + 2, True, 3),
    }


def restricted_app(app, scenario, ids, observed):
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
    from starlette.requests import Request

    from routes.property_network import NO_STORE
    from utils.auth import _decode_token

    project = f"/property-network/projects/{scenario['projectId']}"
    base = f'{project}/due-work-reminders'
    preferences = f'{base}/preferences'
    snooze = f"{base}/milestone/{scenario['milestoneId']}/actions"
    dismiss = f"{base}/visit/{scenario['requestId']}/actions"
    work = f"/property-network/service-requests/{scenario['requestId']}/work"
    members = f'{project}/members'
    revoke = f"{members}/{scenario['winnerUserCode']}/revoke"
    allowed = {('PUT', preferences), *[('POST', path) for path in (snooze, dismiss, work, members, revoke)]}
    actors = {str(identity): role for role, identity in ids.items()}

    def permitted_operation(path, actor, body):
        if not isinstance(body, dict):
            return None
        payload = {key: value for key, value in body.items() if key != 'submission_id'}
        if path == preferences and actor in {'owner', 'winner', 'loser'} and payload == {
            'expected_version': 0, 'milestones_enabled': True, 'visits_enabled': True, 'lead_days': 7,
        }:
            return 'preferences'
        if path == snooze and actor == 'owner' and payload == {
            'expected_version': 0, 'occurrence_version': 1, 'operation': 'snooze', 'snoozed_until': scenario['dates']['snoozeOn'],
        }:
            return 'snooze'
        if path == dismiss and actor == 'winner' and payload in [
            {'expected_version': 0, 'occurrence_version': occurrence, 'operation': 'dismiss'} for occurrence in (2, 4)
        ]:
            return 'dismiss'
        if path == work and actor == 'owner' and payload == {
            'expected_version': 2, 'operation': 'propose_visit', 'starts_at': scenario['dates']['rescheduledVisitStart'],
            'duration_minutes': DURATION,
        }:
            return 'propose_visit'
        if path == work and actor == 'winner' and payload == {'expected_version': 3, 'operation': 'accept_visit'}:
            return 'accept_visit'
        if path == revoke and actor == 'owner' and body == {'expected_version': scenario['projectVersion']}:
            return 'revoke'
        if path == members and actor == 'owner' and body == {
            'expected_version': scenario['projectVersion'] + 1, 'user_code': scenario['winnerUserCode'], 'role': 'professional',
        }:
            return 'regrant'
        return None

    async def serve(scope, receive, send):
        if scope['type'] != 'http' or scope['method'] in {'GET', 'HEAD', 'OPTIONS'}:
            return await app(scope, receive, send)
        method, path = scope['method'], scope['path']
        if (method, path) == ('POST', '/auth/login'):
            return await app(scope, receive, send)
        refused = JSONResponse({'detail': 'Mutation unavailable in browser rehearsal'}, status_code=405, headers=NO_STORE)
        if (method, path) not in allowed:
            return await refused(scope, receive, send)
        request = Request(scope, receive)
        token = request.cookies.get('ildr_token')
        actor = actors.get(_decode_token(token)) if token and not request.headers.get('authorization') else None
        if actor is None:
            return await refused(scope, receive, send)
        raw = await request.body()
        try:
            operation = permitted_operation(path, actor, json.loads(raw)) if len(raw) <= 4096 else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            operation = None
        if operation is None:
            return await refused(scope, receive, send)

        replayed = False

        async def replay_body():
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {'type': 'http.request', 'body': raw, 'more_body': False}

        async def observe_response(message):
            if (path in {work, members, revoke} and message['type'] == 'http.response.start'
                    and 200 <= message['status'] < 300):
                observed.append((operation, await run_in_threadpool(read_workflow, scenario, ids)))
            await send(message)

        return await app(scope, replay_body, observe_response)

    return serve


def snapshot(db):
    from sqlalchemy import text

    tables = (*UNCHANGED_TABLES, 'network_projects', 'network_project_members', 'network_audit', 'network_service_work_actions')
    return {table: db.execute(text(f'SELECT to_jsonb(snapshot_row) FROM {table} AS snapshot_row')).scalars().all()
            for table in tables}


def verify_reminders(db, scenario, ids):
    from models.due_work_reminders import (NetworkDueWorkAction as Action, NetworkDueWorkDisposition as Disposition,
                                          NetworkDueWorkPreference as Preference)

    project, milestone, request = (UUID(scenario[key]) for key in ('projectId', 'milestoneId', 'requestId'))
    snooze_date = date.fromisoformat(scenario['dates']['snoozeOn'])
    preferences = db.query(Preference).all()
    grants = {(row.actor_id, row.grant_version): row for row in preferences}
    visit.require(len(preferences) == 4 and set(grants) == {(ids['owner'], 0), (ids['winner'], 1),
        (ids['winner'], 3), (ids['loser'], 1)} and all(row.project_id == project and row.milestones_enabled
        and row.visits_enabled and row.lead_days == 7 and row.version == 1 for row in preferences),
        'Four separate reminder consent rows required')
    owner, old_winner = grants[ids['owner'], 0], grants[ids['winner'], 1]
    dispositions = db.query(Disposition).all()
    visit.require(len(dispositions) == 3 and {(row.preference_id, row.kind, row.item_id, row.occurrence_version,
        row.state, row.snoozed_until, row.version) for row in dispositions} == {
            (owner.id, 'milestone', milestone, 1, 'snoozed', snooze_date, 1),
            (old_winner.id, 'visit', request, 2, 'dismissed', None, 1),
            (old_winner.id, 'visit', request, 4, 'dismissed', None, 1),
        }, 'Reminder dispositions must be actor, grant, and occurrence scoped')
    actions = db.query(Action).all()
    expected = {(row.actor_id, row.id, 'preferences', None, None, None) for row in preferences}
    expected.update({(ids['owner'], owner.id, 'snooze', 'milestone', milestone, 1),
        (ids['winner'], old_winner.id, 'dismiss', 'visit', request, 2),
        (ids['winner'], old_winner.id, 'dismiss', 'visit', request, 4)})
    visit.require(len(actions) == 7 and {(row.actor_id, row.preference_id, row.operation, row.kind,
        row.item_id, row.occurrence_version) for row in actions} == expected, 'Exact scoped reminder action inventory required')


def verify_database(scenario, ids, original, observed, transport_attempts):
    from database import SessionLocal
    from models.service_visit import NetworkServiceWork

    expected = expected_workflow(scenario)
    visit.require(observed == [(key, expected[key]) for key in ('propose_visit', 'accept_visit', 'revoke', 'regrant')],
                  'Real pending, replacement, revoke, and regrant transitions required in order')
    with SessionLocal() as db:
        verify_reminders(db, scenario, ids)
        visit.require(db.query(NetworkServiceWork).count() == 1 and observed_workflow(db, scenario, ids) == expected['regrant'],
                      'Confirmed replacement work must remain after regrant')
        current = snapshot(db)

    before_project, = original['network_projects']
    after_project, = current['network_projects']
    visit.require(after_project['version'] == before_project['version'] + 2 and {
        **after_project, 'version': before_project['version'], 'updated_at': before_project['updated_at'],
    } == before_project, 'Only project membership version may change')
    original_members = {row['user_id']: row for row in original['network_project_members']}
    after_members = {row['user_id']: row for row in current['network_project_members']}
    winner_key = str(ids['winner'])
    original_members[winner_key] = {**original_members[winner_key], 'reminder_grant_version': 3}
    visit.require(after_members == original_members, 'Only the winner grant may change')

    audits = {row['id']: row for row in current['network_audit']}
    for row in original['network_audit']:
        visit.require(audits.pop(row['id'], None) == row, 'Existing audit evidence must be retained')
    visit.require(len(audits) == 2 and all(row['actor_id'] == str(ids['owner']) and row['target_kind'] == 'project'
        and row['target_id'] == scenario['projectId'] for row in audits.values()) and {
        (row['action'], json.dumps(row['data'], sort_keys=True)) for row in audits.values()} == {
            ('member_revoked', json.dumps({'member_code': scenario['winnerUserCode']}, sort_keys=True)),
            ('member_assigned', json.dumps({'member_code': scenario['winnerUserCode'], 'role': 'professional'}, sort_keys=True)),
        }, 'Exactly two canonical membership audits required')
    work_actions = {row['submission_id']: row for row in current['network_service_work_actions']}
    for row in original['network_service_work_actions']:
        visit.require(work_actions.pop(row['submission_id'], None) == row, 'Initial confirmation actions must be retained')
    visit.require(len(original['network_service_work_actions']) == 2 and len(work_actions) == 2 and Counter(
        (row['actor_id'], row['request_id'], row['operation']) for row in work_actions.values()) == Counter({
            (str(ids['owner']), scenario['requestId'], 'propose_visit'): 1,
            (str(ids['winner']), scenario['requestId'], 'accept_visit'): 1,
        }), 'Exactly two real reschedule actions required')
    for table in UNCHANGED_TABLES:
        visit.require(Counter(json.dumps(row, sort_keys=True) for row in current[table]) == Counter(
            json.dumps(row, sort_keys=True) for row in original[table]), 'Unrelated synthetic business rows must stay unchanged')
    history, = current['network_project_updates']
    visit.require(history['kind'] == 'milestone' and history['project_id'] == scenario['projectId']
                  and not transport_attempts, 'No additional history or external transport allowed')
    return {'status': 'PASS', 'preference_rows': 4, 'preference_actions': 4, 'disposition_rows': 3, 'reminder_actions': 3,
            'visit_actions': 4, 'membership_changes': 2, 'project_history_entries': 1, 'confirmed_occurrence_replaced': True,
            'private_consent_reset': True, 'new_service_requests': 0, 'new_service_messages': 0, 'external_transport_attempts': 0}


def run():
    application = exact_source()
    visit.advance('claim_database')
    url = visit.claim_disposable_database()
    visit.advance('configure_application')
    fixture_file = visit.FIXTURE_FILE
    visit.require(fixture_file.parent.resolve(strict=True) == fixture_file.parent and not fixture_file.exists()
                  and not fixture_file.is_symlink(), 'Empty private fixture directory required')
    visit.configure_application(application, url)
    visit.advance('load_application')
    import httpx
    import requests
    import uvicorn

    transport_attempts = []
    observed = []

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
            visit.advance('seed_due_work_project')
            scenario = seed_project(base['serviceVisit'], ids, instant)
            visit.require(read_workflow(scenario, ids) == expected_workflow(scenario)['seed'], 'Initial visit occurrence required')
            with SessionLocal() as db:
                original = snapshot(db)

            visit.advance('write_fixture')
            fd = os.open(fixture_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            owned = os.fstat(fd)
            previous_handlers = {}
            try:
                with os.fdopen(fd, 'w', encoding='ascii') as stream:
                    json.dump({'accounts': base['accounts'], 'dueWork': scenario}, stream, ensure_ascii=True)
                    stream.write('\n')
                visit.advance('serve_application')
                server = uvicorn.Server(uvicorn.Config(restricted_app(app, scenario, ids, observed), host=visit.HOST,
                    port=visit.API_PORT, lifespan='off', access_log=False, log_level='warning'))

                def finish_shutdown(_number, _frame):
                    server.should_exit = True

                for number in (signal.SIGINT, signal.SIGTERM):
                    previous_handlers[number] = signal.signal(number, finish_shutdown)
                server.run()
                visit.require(server.started, 'Synthetic due-work API failed to start')
                visit.advance('verify_due_work_reminders')
                report = verify_database(scenario, ids, original, observed, transport_attempts)
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
    print('DUE_WORK_REMINDERS_BROWSER_FIXTURE_RESULT ' + json.dumps(report, sort_keys=True, separators=(',', ':')), flush=True)


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

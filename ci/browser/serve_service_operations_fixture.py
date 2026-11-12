"""Serve a synthetic read-only operations rehearsal from exact hosted source."""
from datetime import datetime, time, timedelta
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
OPEN_TITLE = 'Synthetic invited courtyard service'
PRIVATE_TITLE = 'Synthetic private courtyard property'
QUEUE = '/property-network/service-operations'
TABLES = (
    'listings', 'network_properties', 'network_projects', 'network_project_members', 'network_project_updates',
    'network_service_preferences', 'network_service_requests', 'network_service_recipients', 'network_service_proposals',
    'network_service_actions', 'network_service_work', 'network_service_work_actions', 'network_service_reviews',
    'network_service_favorites', 'network_service_negotiation_messages', 'network_service_conversation_messages',
    'network_audit',
)


def exact_source():
    commit = os.environ.get('ILDR_SERVICE_OPERATIONS_APP_COMMIT', '')
    tree = os.environ.get('ILDR_SERVICE_OPERATIONS_APP_TREE', '')
    visit.require(all(re.fullmatch('[a-f0-9]{40}', value) for value in (commit, tree))
                  and os.environ.get('ILDR_SERVICE_OPERATIONS_MIGRATION_HEAD') == MIGRATION_HEAD, 'Exact application source required')
    visit.APP_COMMIT, visit.APP_TREE, visit.MIGRATION_HEAD = commit, tree, MIGRATION_HEAD
    return visit.verified_checkout()


def seed_scenario(award, ids, instant):
    from database import SessionLocal
    from models.listing import Listing
    from models.user import User
    from schemas.property_network import RegistryCreate
    from schemas.service_visit import ServiceWorkWrite
    from schemas.service_workflow import RequestCreate
    from services import property_network as network, service_visit, service_workflow as workflow

    award_id, listing_id = UUID(award['requestId']), UUID(award['listingId'])
    today = instant.astimezone(service_visit.DR_TIMEZONE).date()
    start = datetime.combine(today + timedelta(days=1), time(10), service_visit.DR_TIMEZONE)
    desired = today + timedelta(days=4)
    with SessionLocal() as db:
        owner, winner, loser = (db.get(User, ids[key]) for key in ('owner', 'winner', 'loser'))
        listing = db.get(Listing, listing_id)
        proposal = service_visit.mutate_work(db, owner, award_id, ServiceWorkWrite(
            submission_id=uuid4(), expected_version=0, operation='propose_visit', starts_at=start, duration_minutes=60))
        confirmed = service_visit.mutate_work(db, winner, award_id, ServiceWorkWrite(
            submission_id=uuid4(), expected_version=proposal['version'], operation='accept_visit'))
        visit.require(confirmed['status'] == 'confirmed' and confirmed['version'] == 2, 'Confirmed visit required')
        private = network.create_registry(db, owner, RegistryCreate(
            submission_id=uuid4(), title=PRIVATE_TITLE, location='Synthetic service area', property_class='house_villa',
            relationship='owner', authority_acknowledged=True))
        request = workflow.create_request(db, owner, RequestCreate(
            submission_id=uuid4(), property_id=UUID(private['id']), category_id='pool_service', property_class='house_villa',
            title=OPEN_TITLE, brief='Synthetic invite-only request scope', area_label='Synthetic service area',
            requester_alias='Synthetic property owner', desired_start_on=desired,
            recipient_user_codes=[loser.user_code], shared_brief_acknowledged=True))
        visit.require(request['status'] == 'open', 'Open request required')
        return {
            'awardedRequestId': award['requestId'], 'awardedTitle': award['title'],
            'openRequestId': request['id'], 'openTitle': OPEN_TITLE,
            'listingId': award['listingId'], 'listingTitle': listing.title,
            'privatePropertyId': private['id'], 'privatePropertyTitle': PRIVATE_TITLE,
            'confirmedVisitStart': start.isoformat(), 'desiredStartOn': desired.isoformat(), 'asOf': today.isoformat(),
        }


def snapshot():
    from database import SessionLocal
    from sqlalchemy import text

    with SessionLocal() as db:
        return {table: tuple(db.execute(text(
            f"SELECT count(*), md5(coalesce(string_agg(value::text, '' ORDER BY value::text), '')) "
            f"FROM (SELECT to_jsonb(snapshot_row) AS value FROM {table} AS snapshot_row) AS snapshot"
        )).one()) for table in TABLES}


def restricted_app(app, ids, observed, refused):
    from fastapi.responses import JSONResponse
    from starlette.requests import Request
    from routes.property_network import NO_STORE
    from utils.auth import _decode_token

    actors = {str(identity): role for role, identity in ids.items()}

    async def wrapped(scope, receive, send):
        if scope['type'] != 'http':
            return await app(scope, receive, send)
        request = Request(scope)
        path, method = request.url.path, request.method
        if method not in {'GET', 'HEAD', 'OPTIONS'} and (path, method) != ('/auth/login', 'POST'):
            refused.append(True)
            return await JSONResponse({'detail': 'Fixture mutation refused'}, status_code=403, headers=NO_STORE)(scope, receive, send)
        if (path, method) != (QUEUE, 'GET'):
            return await app(scope, receive, send)

        token = request.cookies.get('ildr_token')
        actor = actors.get(_decode_token(token)) if token and not request.headers.get('authorization') else None
        query = request.query_params
        shape = (actor, query.get('view'), query.get('source_kind'))

        async def observe(message):
            if message['type'] == 'http.response.start':
                observed.add((*shape, message['status']))
            await send(message)

        return await app(scope, receive, observe)
    return wrapped


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

    transport_attempts, refused = [], []
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
        from main import app
        from services import property_network as network

        base, ids = visit.migrate_and_seed()
        instant = network.now().replace(microsecond=0)
        with patch.object(network, 'now', return_value=instant):
            visit.advance('seed_service_operations')
            scenario = seed_scenario(base['serviceVisit'], ids, instant)
            original = snapshot()
            visit.advance('write_fixture')
            fd = os.open(fixture_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            owned = os.fstat(fd)
            previous_handlers = {}
            try:
                with os.fdopen(fd, 'w', encoding='ascii') as stream:
                    json.dump({'accounts': base['accounts'], 'serviceOperations': scenario}, stream, ensure_ascii=True)
                    stream.write('\n')
                visit.advance('serve_application')
                server = uvicorn.Server(uvicorn.Config(restricted_app(app, ids, observed, refused), host=visit.HOST,
                    port=visit.API_PORT, lifespan='off', access_log=False, log_level='warning'))

                def finish_shutdown(_number, _frame):
                    server.should_exit = True

                for number in (signal.SIGINT, signal.SIGTERM):
                    previous_handlers[number] = signal.signal(number, finish_shutdown)
                server.run()
                visit.require(server.started, 'Synthetic operations API failed to start')
                visit.advance('verify_service_operations')
                expected = {
                    (None, 'managed', None, 401), ('admin', 'managed', None, 404),
                    ('owner', 'managed', None, 200), ('owner', 'managed', 'private', 200),
                    ('owner', 'managed', 'listing', 200), ('winner', 'received', None, 200),
                    ('loser', 'received', None, 200), ('outsider', 'received', None, 200),
                }
                visit.require(expected <= observed and not refused and not transport_attempts and original == snapshot(),
                              'Expected scoped reads and unchanged synthetic database required')
                report = {'status': 'PASS', 'required_read_scopes': len(expected), 'tables_unchanged': len(TABLES),
                          'unexpected_mutations': len(refused), 'external_transport_attempts': len(transport_attempts)}
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
    print('SERVICE_OPERATIONS_BROWSER_FIXTURE_RESULT ' + json.dumps(report, sort_keys=True, separators=(',', ':')), flush=True)


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

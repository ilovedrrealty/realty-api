"""Serve an open request for two eligible synthetic providers on loopback Postgres."""

import json
import os
import re
import signal
import stat
import sys
import traceback
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib import request as urllib_request
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_service_visit_fixture as fixture


TITLE = 'Browser rehearsal pre-award pool service'
FIRST_OWNER_MESSAGE = 'Synthetic owner scope question for the first provider.'
FIRST_PROVIDER_MESSAGE = 'Respuesta privada del primer profesional sobre la piscina.'
SECOND_OWNER_MESSAGE = 'Synthetic owner scope question for the second provider.'
SECOND_PROVIDER_MESSAGE = 'Respuesta privada del segundo profesional sobre el horario.'


def exact_source():
    commit = os.environ.get('ILDR_NEGOTIATION_APP_COMMIT', '')
    tree = os.environ.get('ILDR_NEGOTIATION_APP_TREE', '')
    migration = os.environ.get('ILDR_NEGOTIATION_MIGRATION_HEAD', '')
    fixture.require(all(re.fullmatch('[0-9a-f]{40}', value) for value in (commit, tree))
                    and re.fullmatch('[0-9a-f]{12}', migration), 'Exact negotiation source required')
    fixture.APP_COMMIT = commit
    fixture.APP_TREE = tree
    fixture.MIGRATION_HEAD = migration
    return fixture.verified_checkout()


def create_open_request(original, users):
    from database import SessionLocal
    from models.user import User
    from schemas.service_workflow import ProposalTerms, QuoteWrite, RecipientAction, RequestCreate
    from services import service_workflow as workflow

    listing_id = UUID(original['serviceVisit']['listingId'])
    with SessionLocal() as db:
        owner = db.get(User, users['owner'])
        recipients = {name: db.get(User, users[name]) for name in ('winner', 'loser')}
        row = workflow.create_request(db, owner, RequestCreate(submission_id=uuid4(), listing_id=listing_id,
            category_id='pool_service', property_class='house_villa', title=TITLE, brief='Synthetic open negotiation scope',
            area_label='Synthetic service area', requester_alias='Synthetic owner',
            recipient_user_codes=sorted(recipient.user_code for recipient in recipients.values()), shared_brief_acknowledged=True))
        request_id = UUID(row['id'])
        thread_ids = {}
        for name, provider in recipients.items():
            current = workflow.get_request(db, provider, request_id, view='received')['recipient']
            recipient_id = UUID(current['id'])
            thread_ids[name] = str(recipient_id)
            accepted = workflow.recipient_action(db, provider, request_id, recipient_id,
                RecipientAction(submission_id=uuid4(), expected_recipient_version=current['version']), 'recipient_accept')
            workflow.write_proposal(db, provider, request_id, recipient_id, QuoteWrite(submission_id=uuid4(),
                expected_recipient_version=accepted['recipient']['version'], terms=ProposalTerms(
                    amount='135.00' if name == 'winner' else '225.00', currency='USD', pricing_basis='estimate',
                    scope_text='Synthetic quote for pool service', exclusions='Synthetic extras excluded', estimated_days=1,
                    valid_until=workflow.today_dr() + timedelta(days=10))))
        detail = workflow.get_request(db, owner, request_id, view='managed')
        fixture.require(detail['status'] == 'open' and detail['project_id'] is None
                        and {item['id'] for item in detail['recipients']} == set(thread_ids.values())
                        and all(item['status'] == 'accepted' and item['current_proposal'] for item in detail['recipients']),
                        'Two accepted candidates and no preexisting award required')
    return {'title': TITLE, 'requestId': str(request_id), 'listingId': str(listing_id), 'recipientIds': thread_ids}


def restricted_app(app, negotiation):
    from fastapi.responses import JSONResponse
    from routes.property_network import NO_STORE

    path = f"/property-network/service-requests/{negotiation['requestId']}"
    allowed_posts = {'/auth/login', f'{path}/award'} | {
        f'{path}/recipients/{recipient_id}/conversation' for recipient_id in negotiation['recipientIds'].values()
    }

    async def serve(scope, receive, send):
        if scope['type'] == 'http' and scope['method'] not in {'GET', 'HEAD', 'OPTIONS'}:
            if scope['method'] != 'POST' or scope['path'] not in allowed_posts:
                refused = JSONResponse({'detail': 'Mutation unavailable in browser rehearsal'}, status_code=405, headers=NO_STORE)
                return await refused(scope, receive, send)
        return await app(scope, receive, send)

    return serve


def verify_database(negotiation, users, transport_attempts):
    from sqlalchemy import text
    from database import SessionLocal
    from models.property_network import NetworkProjectUpdate
    from models.service_conversation import NetworkServiceConversationMessage
    from models.service_workflow import NetworkServiceRecipient, NetworkServiceRequest

    request_id = UUID(negotiation['requestId'])
    ids = {name: UUID(value) for name, value in negotiation['recipientIds'].items()}
    expected = {
        (request_id, ids['winner'], users['owner'], FIRST_OWNER_MESSAGE),
        (request_id, ids['winner'], users['winner'], FIRST_PROVIDER_MESSAGE),
        (request_id, ids['loser'], users['owner'], SECOND_OWNER_MESSAGE),
        (request_id, ids['loser'], users['loser'], SECOND_PROVIDER_MESSAGE),
    }
    with SessionLocal() as db:
        rows = db.execute(text('SELECT request_id, recipient_id, sender_id, body FROM network_service_negotiation_messages')).all()
        fixture.require(len(rows) == 4 and set(rows) == expected, 'Exact separate private message rows required')
        record = db.get(NetworkServiceRequest, request_id)
        fixture.require(record.status == 'awarded' and record.project_id is not None
                        and db.get(NetworkServiceRecipient, ids['winner']).status == 'awarded'
                        and db.get(NetworkServiceRecipient, ids['loser']).status == 'closed'
                        and db.query(NetworkServiceConversationMessage).count() == 0
                        and db.query(NetworkProjectUpdate).count() == 0,
                        'Award must leave messages outside both new conversation and property history')
    fixture.require(not transport_attempts, 'No external transport allowed')
    return {'status': 'PASS', 'negotiation_messages': 4, 'separate_recipient_threads': 2,
            'awarded_conversation_messages': 0, 'private_history_entries': 0, 'external_transport_attempts': 0}


def run():
    application = exact_source()
    fixture.advance('claim_database')
    url = fixture.claim_disposable_database()
    fixture.advance('configure_application')
    fixture_file = fixture.FIXTURE_FILE
    fixture.require(fixture_file.parent.resolve(strict=True) == fixture_file.parent
                    and not fixture_file.exists() and not fixture_file.is_symlink(), 'Empty private fixture directory required')
    fixture.configure_application(application, url)
    fixture.advance('load_application')
    import httpx
    import requests
    import uvicorn

    transport_attempts = []

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

        original, users = fixture.migrate_and_seed()
        fixture.advance('seed_negotiation')
        negotiation = create_open_request(original, users)
        fixture.advance('write_fixture')
        fd = os.open(fixture_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        owned = os.fstat(fd)
        previous_handlers = {}
        try:
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                json.dump({'accounts': original['accounts'], 'negotiation': negotiation}, stream, ensure_ascii=True)
                stream.write('\n')
            fixture.advance('serve_application')
            server = uvicorn.Server(uvicorn.Config(restricted_app(app, negotiation), host=fixture.HOST, port=fixture.API_PORT,
                                                   lifespan='off', access_log=False, log_level='warning'))

            def finish_shutdown(_number, _frame):
                server.should_exit = True

            for number in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[number] = signal.signal(number, finish_shutdown)
            server.run()
            fixture.require(server.started, 'Synthetic negotiation API failed to start')
            fixture.advance('verify_negotiation')
            report = verify_database(negotiation, users, transport_attempts)
        finally:
            try:
                if fixture_file.exists() or fixture_file.is_symlink():
                    current = fixture_file.lstat()
                    if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                        fixture_file.unlink()
            finally:
                for number, handler in previous_handlers.items():
                    signal.signal(number, handler)
    fixture.require(not fixture_file.exists() and not fixture_file.is_symlink(), 'Synthetic login file cleanup failed')
    print('SERVICE_NEGOTIATION_BROWSER_FIXTURE_RESULT ' + json.dumps(report, sort_keys=True, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        error_type = type(error).__name__
        frames = [frame.lineno for frame in traceback.extract_tb(error.__traceback__)
                  if frame.filename in {__file__, fixture.__file__}]
        print('BROWSER_API_FIXTURE_FAILURE ' + json.dumps({'stage': fixture.stage,
              'error_type': error_type if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', error_type) else 'UnhandledError',
              'fixture_line': frames[-1] if frames else None}), flush=True)
        raise SystemExit(1) from None

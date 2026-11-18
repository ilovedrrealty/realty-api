"""Run a conversation rehearsal against a real award in disposable loopback Postgres."""

import json
import os
import re
import signal
import stat
import sys
import traceback
from pathlib import Path
from unittest.mock import patch
from urllib import request as urllib_request
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_service_visit_fixture as visit_fixture


OWNER_MESSAGE = 'Synthetic owner message about the pool service.'
PROVIDER_MESSAGE = 'Mensaje sintético del profesional sobre el servicio de piscina.'


def exact_source():
    commit = os.environ.get('ILDR_CONVERSATION_APP_COMMIT', '')
    tree = os.environ.get('ILDR_CONVERSATION_APP_TREE', '')
    head = os.environ.get('ILDR_CONVERSATION_MIGRATION_HEAD', '')
    visit_fixture.require(all(re.fullmatch('[0-9a-f]{40}', value) for value in (commit, tree))
                          and re.fullmatch('[0-9a-f]{12}', head), 'Exact conversation source required')
    visit_fixture.APP_COMMIT = commit
    visit_fixture.APP_TREE = tree
    visit_fixture.MIGRATION_HEAD = head
    return visit_fixture.verified_checkout()


def restricted_app(app, award):
    from fastapi.responses import JSONResponse
    from routes.property_network import NO_STORE

    allowed_posts = {
        '/auth/login',
        f"/property-network/service-requests/{award['requestId']}/conversation",
        f"/property-network/projects/{award['projectId']}/members/{award['winnerUserCode']}/revoke",
    }

    async def serve(scope, receive, send):
        if scope['type'] == 'http' and scope['method'] not in {'GET', 'HEAD', 'OPTIONS'}:
            if scope['method'] != 'POST' or scope['path'] not in allowed_posts:
                refused = JSONResponse({'detail': 'Mutation unavailable in browser rehearsal'}, status_code=405, headers=NO_STORE)
                return await refused(scope, receive, send)
        return await app(scope, receive, send)

    return serve


def verify_database(award, user_ids, transport_attempts):
    from sqlalchemy import text
    from database import SessionLocal
    from models.property_network import NetworkProjectMember, NetworkProjectUpdate

    with SessionLocal() as db:
        rows = db.execute(text('''
            SELECT request_id, sender_id, body
            FROM network_service_conversation_messages ORDER BY created_at, id
        ''')).all()
        request_id = UUID(award['requestId'])
        visit_fixture.require(rows == [(request_id, user_ids['owner'], OWNER_MESSAGE),
                                       (request_id, user_ids['winner'], PROVIDER_MESSAGE)],
                              'Exact two-party message history required')
        member = db.query(NetworkProjectMember).filter_by(
            project_id=UUID(award['projectId']), user_id=user_ids['winner']
        ).one()
        visit_fixture.require(member.active is False and db.query(NetworkProjectUpdate).count() == 0,
                              'Revocation and separation from property history required')
    visit_fixture.require(not transport_attempts, 'No external transport allowed')
    return {'status': 'PASS', 'messages': 2, 'private_history_entries': 0,
            'winner_membership_revoked': True, 'external_transport_attempts': 0}


def run():
    application = exact_source()
    visit_fixture.advance('claim_database')
    url = visit_fixture.claim_disposable_database()
    visit_fixture.advance('configure_application')
    fixture_file = visit_fixture.FIXTURE_FILE
    visit_fixture.require(fixture_file.parent.resolve(strict=True) == fixture_file.parent
                          and not fixture_file.exists() and not fixture_file.is_symlink(),
                          'Empty private synthetic fixture directory required')
    visit_fixture.configure_application(application, url)
    visit_fixture.advance('load_application')
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

        fixture, user_ids = visit_fixture.migrate_and_seed()
        award = fixture['serviceVisit']
        visit_fixture.advance('write_fixture')
        fd = os.open(fixture_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        owned = os.fstat(fd)
        previous_handlers = {}
        try:
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                json.dump(fixture, stream, ensure_ascii=True)
                stream.write('\n')
            visit_fixture.advance('serve_application')
            api = restricted_app(app, award)
            server = uvicorn.Server(uvicorn.Config(api, host=visit_fixture.HOST, port=visit_fixture.API_PORT,
                                                   lifespan='off', access_log=False, log_level='warning'))

            def finish_shutdown(_number, _frame):
                server.should_exit = True

            for number in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[number] = signal.signal(number, finish_shutdown)
            server.run()
            visit_fixture.require(server.started, 'Synthetic conversation API failed to start')
            visit_fixture.advance('verify_conversation')
            report = verify_database(award, user_ids, transport_attempts)
        finally:
            try:
                if fixture_file.exists() or fixture_file.is_symlink():
                    current = fixture_file.lstat()
                    if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                        fixture_file.unlink()
            finally:
                for number, handler in previous_handlers.items():
                    signal.signal(number, handler)
    visit_fixture.require(not fixture_file.exists() and not fixture_file.is_symlink(), 'Synthetic login file cleanup failed')
    print('SERVICE_CONVERSATION_BROWSER_FIXTURE_RESULT ' + json.dumps(report, sort_keys=True, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        error_type = type(error).__name__
        frames = [frame.lineno for frame in traceback.extract_tb(error.__traceback__)
                  if frame.filename in {__file__, visit_fixture.__file__}]
        print('BROWSER_API_FIXTURE_FAILURE ' + json.dumps({'stage': visit_fixture.stage,
              'error_type': error_type if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', error_type) else 'UnhandledError',
              'fixture_line': frames[-1] if frames else None}), flush=True)
        raise SystemExit(1) from None

"""Serve saved-provider rehearsal cases on disposable loopback Postgres."""

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
import serve_service_visit_fixture as visit


FAVORITES_PATH = '/property-network/service-favorites'
PREFERENCES_PATH = '/property-network/me/service-preferences'
FAVORITE_ITEM = re.compile(FAVORITES_PATH + r'/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})')
LISTING_TITLE = 'Browser rehearsal villa'
PROVIDER_ALIAS = 'Synthetic winner service provider'
OTHER_PROVIDER_ALIAS = 'Synthetic loser service provider'
SNAPSHOT_TABLES = (
    'listings', 'network_service_favorites', 'network_service_preferences', 'network_service_requests',
    'network_service_recipients', 'network_service_proposals', 'network_service_actions', 'network_projects',
    'network_project_members', 'network_service_work', 'network_service_work_actions', 'network_project_updates',
    'network_service_reviews', 'network_service_negotiation_messages', 'network_service_conversation_messages',
)


def exact_source():
    commit = os.environ.get('ILDR_FAVORITES_APP_COMMIT', '')
    tree = os.environ.get('ILDR_FAVORITES_APP_TREE', '')
    migration = os.environ.get('ILDR_FAVORITES_MIGRATION_HEAD', '')
    visit.require(all(re.fullmatch('[0-9a-f]{40}', value) for value in (commit, tree))
                  and migration == 'f71c5a9d243e', 'Exact service favorites source required')
    visit.APP_COMMIT = commit
    visit.APP_TREE = tree
    visit.MIGRATION_HEAD = migration
    return visit.verified_checkout()


def seed_isolation(award, users):
    from database import SessionLocal
    from models.listing import Listing
    from models.user import User
    from services import service_favorites, service_workflow as workflow

    with SessionLocal() as db:
        owner = db.get(User, users['owner'])
        other = db.get(User, users['outsider'])
        listing = db.get(Listing, UUID(award['listingId']))
        visit.require(listing.title == LISTING_TITLE and listing.owner_id == owner.id and listing.status == 'active',
                      'Exact owner-authorized synthetic listing required')
        discovery = workflow.discovery(db, owner, 'pool_service', 'house_villa', 1)
        visit.require({row['user_code']: row['member_alias'] for row in discovery['items']}
                      == {award['winnerUserCode']: PROVIDER_ALIAS, award['loserUserCode']: OTHER_PROVIDER_ALIAS},
                      'Two exact eligible synthetic providers required')
        other_favorite = service_favorites.save(db, other, award['loserUserCode'])
        owner_list = service_favorites.list_favorites(db, owner, 1)
        other_list = service_favorites.list_favorites(db, other, 1)
        visit.require(owner_list['total'] == 0 and other_list['items'] == [other_favorite]
                      and other_favorite['provider']['member_alias'] == OTHER_PROVIDER_ALIAS,
                      'A separate actor must own the only starting favorite')
        return {'listingId': award['listingId'], 'listingTitle': LISTING_TITLE,
                'providerUserCode': award['winnerUserCode'], 'providerAlias': PROVIDER_ALIAS,
                'otherProviderUserCode': award['loserUserCode'], 'otherProviderAlias': OTHER_PROVIDER_ALIAS,
                'otherFavoriteId': other_favorite['id']}


def snapshot(db):
    from sqlalchemy import text

    return {table: db.execute(text(f'SELECT to_jsonb(snapshot_row) FROM {table} AS snapshot_row')).scalars().all()
            for table in SNAPSHOT_TABLES}


def observe_favorite(method, favorite_id, actor_id, users):
    from database import SessionLocal
    from models.service_favorites import NetworkServiceFavorite as Favorite

    if actor_id != str(users['owner']):
        return method, None, False
    with SessionLocal() as db:
        if method == 'POST':
            row = db.query(Favorite).filter_by(actor_id=users['owner'], provider_id=users['winner']).one_or_none()
            return method, str(row.id) if row else None, row is not None
        return method, favorite_id, db.get(Favorite, UUID(favorite_id)) is None


def restricted_app(app, users, favorite_events):
    from fastapi.responses import JSONResponse
    from starlette.concurrency import run_in_threadpool
    from starlette.requests import Request

    from routes.property_network import NO_STORE
    from utils.auth import _decode_token

    async def serve(scope, receive, send):
        if scope['type'] != 'http' or scope['method'] in {'GET', 'HEAD', 'OPTIONS'}:
            return await app(scope, receive, send)
        method, path = scope['method'], scope['path']
        if (method, path) == ('POST', '/auth/login'):
            return await app(scope, receive, send)

        request = Request(scope)
        token = request.cookies.get('ildr_token')
        actor_id = _decode_token(token) if token and not request.headers.get('authorization') else None
        if (method, path) == ('PUT', PREFERENCES_PATH) and actor_id == str(users['winner']):
            return await app(scope, receive, send)
        item = FAVORITE_ITEM.fullmatch(path) if method == 'DELETE' else None
        if (method, path) != ('POST', FAVORITES_PATH) and item is None:
            refused = JSONResponse({'detail': 'Mutation unavailable in browser rehearsal'}, status_code=405, headers=NO_STORE)
            return await refused(scope, receive, send)

        async def observe_response(message):
            expected_status = 201 if method == 'POST' else 204
            if message['type'] == 'http.response.start' and message['status'] == expected_status:
                favorite_events.append(await run_in_threadpool(
                    observe_favorite, method, item.group(1) if item else None, actor_id, users,
                ))
            await send(message)

        return await app(scope, receive, observe_response)

    return serve


def verify_database(case, users, original, favorite_events, transport_attempts):
    from database import SessionLocal
    from schemas.service_workflow import PreferenceWrite
    from services import service_workflow as workflow

    with SessionLocal() as db:
        current = snapshot(db)
    saved, removed = favorite_events
    visit.require(saved[0] == 'POST' and saved[1] is not None and UUID(saved[1]).version == 4 and saved[2] is True
                  and removed == ('DELETE', saved[1], True) and saved[1] != case['otherFavoriteId'],
                  'Independent readback requires one owner save and removal of that same row')
    other, = current['network_service_favorites']
    visit.require(other['id'] == case['otherFavoriteId'] and other['actor_id'] == str(users['outsider'])
                  and other['provider_id'] == str(users['loser']), 'Only the unrelated actor favorite may remain')

    winner = str(users['winner'])
    prior_preference, = [row for row in original['network_service_preferences'] if row['user_id'] == winner]
    after_preference, = [row for row in current['network_service_preferences'] if row['user_id'] == winner]
    visit.require(prior_preference['service_opt_in'] is True and prior_preference['version'] == 1
                  and after_preference['service_opt_in'] is False and after_preference['version'] == 2
                  and after_preference['member_alias'] == PROVIDER_ALIAS
                  and after_preference['category_ids'] == ['pool_service']
                  and after_preference['property_class_ids'] == ['house_villa'],
                  'Only the exact selected provider opt-in state may change')
    original_actions = {row['id'] for row in original['network_service_actions']}
    change, = [row for row in current['network_service_actions'] if row['id'] not in original_actions]
    key = UUID(change['submission_id'])
    body = PreferenceWrite(submission_id=key, expected_version=1, service_opt_in=False,
                           member_alias=PROVIDER_ALIAS, category_ids=['pool_service'], property_class_ids=['house_villa'])
    visit.require(key.version == 4 and change['operation'] == 'preference_save'
                  and change['actor_id'] == winner and change['target_id'] == winner
                  and change['request_id'] is None and change['recipient_id'] is None and change['proposal_id'] is None
                  and change['creation_sha256'] == workflow.fingerprint('preference_save', users['winner'], body),
                  'One explicit preference change for the chosen provider required')

    current['network_service_actions'].remove(change)
    current['network_service_preferences'].remove(after_preference)
    current['network_service_preferences'].append({**after_preference, 'service_opt_in': True,
                                                    'version': 1, 'updated_at': prior_preference['updated_at']})
    for table in SNAPSHOT_TABLES:
        visit.require(sorted(json.dumps(row, sort_keys=True) for row in current[table])
                      == sorted(json.dumps(row, sort_keys=True) for row in original[table]),
                      'Existing synthetic business rows must otherwise stay unchanged')
    visit.require(not transport_attempts, 'No external transport allowed')
    return {'status': 'PASS', 'owner_favorites_saved': 1, 'owner_favorites_removed': 1,
            'other_favorites_retained': 1, 'provider_opted_out': True, 'provider_preference_actions': 1,
            'new_service_requests': 0, 'new_service_messages': 0, 'external_transport_attempts': 0}


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
    favorite_events = []

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
        visit.advance('seed_favorites_isolation')
        case = seed_isolation(base['serviceVisit'], users)
        with SessionLocal() as db:
            original = snapshot(db)
        visit.advance('write_fixture')
        fd = os.open(fixture_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        owned = os.fstat(fd)
        previous_handlers = {}
        try:
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                json.dump({'accounts': base['accounts'], 'serviceFavorites': case}, stream, ensure_ascii=True)
                stream.write('\n')
            visit.advance('serve_application')
            server = uvicorn.Server(uvicorn.Config(restricted_app(app, users, favorite_events), host=visit.HOST,
                                                   port=visit.API_PORT, lifespan='off', access_log=False, log_level='warning'))

            def finish_shutdown(_number, _frame):
                server.should_exit = True

            for number in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[number] = signal.signal(number, finish_shutdown)
            server.run()
            visit.require(server.started, 'Synthetic favorites API failed to start')
            visit.advance('verify_favorites')
            report = verify_database(case, users, original, favorite_events, transport_attempts)
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
    print('SERVICE_FAVORITES_BROWSER_FIXTURE_RESULT ' + json.dumps(report, sort_keys=True, separators=(',', ':')), flush=True)


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

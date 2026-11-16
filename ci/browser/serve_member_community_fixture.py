"""Host the real member community with three synthetic accounts and in-memory photo storage."""

from collections import Counter
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import sys
import traceback
from unittest.mock import patch
from urllib import request as urllib_request
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_service_visit_fixture as base
from private_storage_fixture import install_private_storage_fixture


base.APP_COMMIT = 'b38d477c0e2e50f87f1f2e4cc1ceb3c707af92c5'
base.APP_TREE = 'a53b3f829f70717069f5c637c826fb8f92c02f7c'
base.MIGRATION_HEAD = 'f4b27d6e913c'
NETWORK = '/property-network'
ROLES = {'owner': 'owner', 'author': 'buyer', 'reader': 'buyer'}
PROFILE = 'Comunidad sintética de prueba'
STATUS = 'Estoy revisando las fotos de una propiedad de prueba.'
OWN_POST = 'Esta es mi primera publicación de prueba.'
CROSS_POST = 'Synthetic cross-wall post with a private sample photo.'
COMMENT = 'Synthetic comment about this exact sample photo.'
UUID_TEXT = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def seed_accounts():
    import bootstrap_db
    from alembic import command
    from alembic.config import Config
    from database import SessionLocal
    from models.user import User
    from sqlalchemy import text
    from utils.security import hash_password

    base.advance('bootstrap_database')
    bootstrap_db.main()
    base.advance('apply_alembic')
    command.upgrade(Config('alembic.ini'), 'head')
    base.advance('seed_accounts')
    accounts, identities, codes = {}, {}, {}
    with SessionLocal() as db:
        base.require(db.execute(text('SELECT version_num FROM alembic_version')).scalars().all() == [base.MIGRATION_HEAD]
                     and db.query(User).count() == 0, 'Actual migration and empty users required')
        for actor, role in ROLES.items():
            account = {'email': f'browser-wall-{actor}@example.com', 'password': secrets.token_urlsafe(24)}
            user = User(id=uuid4(), email=account['email'], password_hash=hash_password(account['password']),
                        role=role, status='active', email_verified=True, display_name=f'Synthetic Wall {actor.title()}')
            db.add(user)
            db.flush()
            accounts[actor], identities[actor], codes[actor] = account, user.id, user.user_code
        db.commit()
    base.require(len(set(codes.values())) == 3 and all(type(value) is int and 1 <= value <= 999_999_999
                 for value in codes.values()), 'Unique routable codes required')
    return accounts, identities, codes


def restricted_app(app, accounts, identities, codes, observed, refused):
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from routes.property_network import NO_STORE
    from utils.auth import _decode_token

    actors = {str(identity): actor for actor, identity in identities.items()}
    login_bodies = {canonical(account) for account in accounts.values()}
    wall = f'{NETWORK}/profiles/{codes["owner"]}/posts'
    state = {}
    active_write = False

    async def wrapped(scope, receive, send):
        nonlocal active_write
        if scope['type'] != 'http' or scope['method'] in {'GET', 'HEAD', 'OPTIONS'}:
            return await app(scope, receive, send)
        request = Request(scope, receive)

        async def reject():
            refused.append(True)
            response = JSONResponse({'detail': 'Mutation unavailable in member-community rehearsal'},
                                    status_code=403, headers=NO_STORE)
            return await response(scope, receive, send)

        if (request.url.query or request.headers.get('origin') != base.DASHBOARD_ORIGIN
                or request.headers.get('authorization') is not None):
            return await reject()
        content_type = request.headers.get('content-type', '').split(';', 1)[0].lower()
        path = scope['path']
        raw = None
        body = None
        if content_type == 'application/json':
            length = request.headers.get('content-length', '')
            if not length.isascii() or not length.isdigit() or int(length) > 4096:
                return await reject()
            raw = await request.body()
            try:
                body = json.loads(raw) if len(raw) <= 4096 else None
            except (UnicodeError, ValueError):
                return await reject()
            if not isinstance(body, dict):
                return await reject()
        replayed = False

        async def replay():
            nonlocal replayed
            if raw is None or replayed:
                return await receive()
            replayed = True
            return {'type': 'http.request', 'body': raw, 'more_body': False}

        if path == '/auth/login':
            if request.method != 'POST' or body is None or canonical(body) not in login_bodies:
                return await reject()
            return await app(scope, replay, send)
        if active_write or len(observed) >= 7:
            return await reject()
        cookie = request.cookies.get('ildr_token')
        actor = actors.get(_decode_token(cookie)) if cookie else None
        submission = body.get('submission_id', '') if body else ''
        has_submission = isinstance(submission, str) and UUID_TEXT.fullmatch(submission)
        post = state.get('cross_post')
        photo = state.get('photo')
        plan = (
            ('profile', 'owner', 'PUT', f'{NETWORK}/me/profile', 200, {
                'expected_version': 0, 'display_name': PROFILE, 'bio': '', 'status_text': STATUS,
                'visibility': 'members', 'wall_policy': 'approval_required', 'comments_enabled': True,
            }),
            ('own_post', 'owner', 'POST', wall, 201,
             {'submission_id': submission, 'body': OWN_POST, 'media_ids': []}),
            ('photo', 'author', 'POST', f'{NETWORK}/media', 201, None),
            ('cross_post', 'author', 'POST', wall, 201,
             {'submission_id': submission, 'body': CROSS_POST, 'media_ids': [photo]}),
            ('approve', 'owner', 'POST', f'{NETWORK}/posts/{post}/moderate', 200, {'action': 'approve'}),
            ('comment', 'reader', 'POST', f'{NETWORK}/posts/{post}/comments', 201,
             {'submission_id': submission, 'body': COMMENT, 'media_id': photo}),
            ('block', 'owner', 'PUT', f'{NETWORK}/me/blocks/{codes["reader"]}', 200, {'blocked': True}),
        )
        operation, allowed_actor, method, endpoint, status, expected = plan[len(observed)]
        if (actor, request.method, path) != (allowed_actor, method, endpoint):
            return await reject()
        if operation == 'photo':
            length = request.headers.get('content-length', '')
            if content_type != 'multipart/form-data' or not length.isascii() or not length.isdigit() or not 0 < int(length) <= 4096:
                return await reject()
        elif body is None or canonical(body) != canonical(expected) or ('submission_id' in expected and not has_submission):
            return await reject()

        response_bytes = bytearray()
        response_status = 0

        async def observe(message):
            nonlocal response_status
            if message['type'] == 'http.response.start':
                response_status = message['status']
            if message['type'] == 'http.response.body':
                response_bytes.extend(message.get('body', b''))
                if not message.get('more_body', False):
                    verdict = response_status == status and 0 < len(response_bytes) <= 8192
                    if verdict and operation in {'photo', 'cross_post'}:
                        identifier = json.loads(response_bytes).get('id')
                        verdict = isinstance(identifier, str) and UUID_TEXT.fullmatch(identifier)
                        if verdict:
                            state[operation] = identifier
                    (observed if verdict else refused).append(operation)
            await send(message)

        active_write = True
        try:
            return await app(scope, replay, observe)
        finally:
            active_write = False

    return wrapped


def verify_database(identities, observed, refused, attempts, storage):
    from database import SessionLocal
    from models.property_network import (NetworkAudit, NetworkBlock, NetworkMedia, NetworkPost,
                                         NetworkPostComment, SocialNetworkProfile)
    from models.user import User
    from sqlalchemy import text

    base.require(observed == ['profile', 'own_post', 'photo', 'cross_post', 'approve', 'comment', 'block']
                 and not refused and not attempts, 'Exactly seven planned writes required')
    media = storage.snapshot()
    base.require(media['uploads'] == media['object_count'] == 1 and media['downloads'] >= 2,
                 'Exact stored photo and authorized downloads required')
    with SessionLocal() as db:
        owner, author, reader = (identities[actor] for actor in ROLES)
        profile = db.query(SocialNetworkProfile).one()
        base.require((profile.user_id, profile.display_name, profile.status_text, profile.visibility,
                      profile.wall_policy, profile.comments_enabled, profile.version) ==
                     (owner, PROFILE, STATUS, 'members', 'approval_required', True, 1), 'Exact reviewed profile required')
        posts = {row.body: row for row in db.query(NetworkPost).all()}
        base.require(set(posts) == {OWN_POST, CROSS_POST}, 'Exactly two synthetic posts required')
        mine, cross = posts[OWN_POST], posts[CROSS_POST]
        photo = db.query(NetworkMedia).one()
        base.require((mine.profile_user_id, mine.author_id, mine.status, mine.media_ids) == (owner, owner, 'visible', [])
                     and (cross.profile_user_id, cross.author_id, cross.status, cross.media_ids) ==
                     (owner, author, 'visible', [str(photo.id)]), 'Exact own and approved foreign posts required')
        base.require((photo.project_id, photo.update_id, photo.post_id, photo.uploader_id, photo.byte_count) ==
                     (None, None, cross.id, author, media['stored_bytes']), 'Photo must be confined to the foreign wall post')
        comment = db.query(NetworkPostComment).one()
        base.require((comment.post_id, comment.media_id, comment.author_id, comment.body, comment.hidden) ==
                     (cross.id, photo.id, reader, COMMENT, False), 'Exact visible photo comment required')
        block = db.query(NetworkBlock).one()
        base.require((block.blocker_id, block.blocked_id) == (owner, reader), 'Exact outgoing block required')
        base.require(Counter(row.action for row in db.query(NetworkAudit).all()) ==
                     Counter({'profile_saved': 1, 'approve': 1, 'blocked': 1}), 'Expected profile and moderation audit required')
        user_count = db.query(User).count()
        base.require(user_count == 3 and all(db.execute(text(f'SELECT count(*) FROM {table}')).scalar_one() == 0 for table in (
            'listings', 'professional_profiles', 'network_projects', 'network_project_updates', 'network_project_comments',
            'network_service_requests', 'lead_delivery_jobs', 'leads', 'network_history_shares', 'network_properties',
        )), 'Unrelated state must remain empty')
    return {'status': 'PASS', 'writes': len(observed), 'user_count': user_count, 'profiles': 1, 'posts': 2,
            'photo_comments': 1, 'blocks': 1, 'uploads': media['uploads'], 'objects': media['object_count'],
            'downloads': media['downloads'], 'external_transport_attempts': len(attempts),
            'unexpected_mutations': len(refused), 'unrelated_tables_empty': True}


def run():
    application = base.verified_checkout()
    base.advance('claim_database')
    url = base.claim_disposable_database()
    base.advance('configure_application')
    path = base.FIXTURE_FILE
    base.require(path.parent.resolve(strict=True) == path.parent and not path.exists() and not path.is_symlink(),
                 'Empty owned private fixture directory required')
    base.configure_application(application, url)
    base.advance('load_application')
    import httpx
    import requests
    import uvicorn

    observed, refused, attempts = [], [], []

    def refuse_transport(*_args, **_kwargs):
        attempts.append(True)
        raise RuntimeError('External transports are unavailable')

    with (
        patch.object(httpx.HTTPTransport, 'handle_request', refuse_transport),
        patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', refuse_transport),
        patch.object(requests.Session, 'send', refuse_transport),
        patch.object(urllib_request, 'urlopen', refuse_transport),
        install_private_storage_fixture() as storage,
    ):
        from main import app

        accounts, identities, codes = seed_accounts()
        base.advance('write_fixture')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        owned = os.fstat(fd)
        handlers = {}
        try:
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                stream.write(canonical({'accounts': accounts, 'codes': codes, 'profile': PROFILE,
                                        'status': STATUS, 'ownPost': OWN_POST, 'crossPost': CROSS_POST,
                                        'comment': COMMENT}) + '\n')
            base.advance('serve_application')
            server = uvicorn.Server(uvicorn.Config(restricted_app(app, accounts, identities, codes, observed, refused),
                host=base.HOST, port=base.API_PORT, lifespan='off', access_log=False, log_level='warning'))

            def finish_shutdown(_number, _frame):
                server.should_exit = True

            for number in (signal.SIGINT, signal.SIGTERM):
                handlers[number] = signal.signal(number, finish_shutdown)
            server.run()
            base.require(server.started, 'Exact synthetic server did not start')
            base.advance('verify_community')
            proof = verify_database(identities, observed, refused, attempts, storage)
        finally:
            try:
                if path.exists() or path.is_symlink():
                    current = path.lstat()
                    if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (owned.st_dev, owned.st_ino):
                        path.unlink()
            finally:
                for number, handler in handlers.items():
                    signal.signal(number, handler)
    base.require(not path.exists() and not path.is_symlink(), 'Owned login file survived fixture shutdown')
    print('MEMBER_COMMUNITY_FIXTURE_RESULT ' + canonical({**proof, 'login_file_absent': True}), flush=True)


if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        frames = [frame.lineno for frame in traceback.extract_tb(error.__traceback__)
                  if frame.filename in {__file__, base.__file__}]
        print('BROWSER_API_FIXTURE_FAILURE ' + canonical({'stage': base.stage,
              'error_type': type(error).__name__, 'fixture_line': frames[-1] if frames else None}), flush=True)
        raise SystemExit(1) from None

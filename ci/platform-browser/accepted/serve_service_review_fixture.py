"""Serve private review rehearsal cases on disposable, loopback-only Postgres."""

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


UNFINISHED_TITLE = 'Browser rehearsal unfinished pool service'
REVIEW_COMMENT = 'El trabajo se completó a tiempo y el área quedó limpia.'
COMPLETION_ACTIONS = (
    ('owner', 'propose_visit'), ('winner', 'accept_visit'),
    ('winner', 'request_completion'), ('owner', 'approve_completion'),
)


def exact_source():
    commit = os.environ.get('ILDR_REVIEW_APP_COMMIT', '')
    tree = os.environ.get('ILDR_REVIEW_APP_TREE', '')
    migration = os.environ.get('ILDR_REVIEW_MIGRATION_HEAD', '')
    fixture.require(all(re.fullmatch('[0-9a-f]{40}', value) for value in (commit, tree))
                    and re.fullmatch('[0-9a-f]{12}', migration), 'Exact service review source required')
    fixture.APP_COMMIT = commit
    fixture.APP_TREE = tree
    fixture.MIGRATION_HEAD = migration
    return fixture.verified_checkout()


def create_unfinished_award(listing_id, users):
    from database import SessionLocal
    from models.user import User
    from schemas.service_workflow import AwardWrite, ProposalTerms, QuoteWrite, RecipientAction, RequestCreate
    from services import service_workflow as workflow

    with SessionLocal() as db:
        owner = db.get(User, users['owner'])
        provider = db.get(User, users['winner'])
        created = workflow.create_request(db, owner, RequestCreate(
            submission_id=uuid4(), listing_id=listing_id, category_id='pool_service', property_class='house_villa',
            title=UNFINISHED_TITLE, brief='Synthetic pool service without completed work',
            requester_alias='Synthetic property owner', recipient_user_codes=[provider.user_code],
            shared_brief_acknowledged=True,
        ))
        request_id = UUID(created['id'])
        initial = workflow.get_request(db, provider, request_id, view='received')['recipient']
        recipient_id = UUID(initial['id'])
        accepted = workflow.recipient_action(db, provider, request_id, recipient_id, RecipientAction(
            submission_id=uuid4(), expected_recipient_version=initial['version'],
        ), 'recipient_accept')
        workflow.write_proposal(db, provider, request_id, recipient_id, QuoteWrite(
            submission_id=uuid4(), expected_recipient_version=accepted['recipient']['version'],
            terms=ProposalTerms(amount='125.00', currency='USD', pricing_basis='estimate',
                                scope_text='Synthetic quote for unfinished service',
                                valid_until=workflow.today_dr() + timedelta(days=10)),
        ))
        managed = workflow.get_request(db, owner, request_id, view='managed')
        recipient, = managed['recipients']
        proposal = recipient['current_proposal']
        awarded = workflow.close_request(db, owner, request_id, AwardWrite(
            submission_id=uuid4(), expected_request_version=managed['version'], recipient_id=recipient_id,
            expected_recipient_version=recipient['version'], proposal_id=UUID(proposal['id']),
            expected_proposal_version=proposal['version'], project_title='Browser rehearsal unfinished service project',
            project_summary='Synthetic approved scope awaiting work', project_scope_acknowledged=True,
        ), award=True)
        project_id = awarded['project']['id']
        received = workflow.get_request(db, provider, request_id, view='received')
        fixture.require(awarded['request']['status'] == 'awarded' and awarded['request']['project_id'] == project_id
                        and received['recipient']['status'] == 'awarded' and received['project_id'] == project_id,
                        'Genuine unfinished service award required')
        return {'requestId': str(request_id), 'projectId': project_id, 'listingId': str(listing_id),
                'winnerUserCode': provider.user_code, 'title': UNFINISHED_TITLE}


def complete_award(award, users):
    from database import SessionLocal
    from models.user import User
    from schemas.service_visit import ServiceWorkWrite
    from services import property_network as network, service_visit

    request_id = UUID(award['requestId'])
    clock = fixture.VisitClock()
    with SessionLocal() as db, patch.object(network, 'now', clock.now):
        actors = {role: db.get(User, users[role]) for role in ('owner', 'winner')}
        version = 0

        def act(actor, operation, **fields):
            nonlocal version
            result = service_visit.mutate_work(db, actors[actor], request_id, ServiceWorkWrite(
                submission_id=uuid4(), expected_version=version, operation=operation, **fields,
            ))
            version += 1
            fixture.require(result['version'] == version, 'Exact seeded work version required')
            return result

        act('owner', 'propose_visit', starts_at=fixture.CONFIRMED_START, duration_minutes=60)
        act('winner', 'accept_visit')
        fixture.require(clock.advance_once(), 'One confined seed clock advance required')
        act('winner', 'request_completion', note=fixture.COMPLETION, completed_on=fixture.COMPLETED_ON)
        result = act('owner', 'approve_completion', share_in_history=True)
        fixture.require(result['status'] == 'completed' and result['project_id'] == award['projectId']
                        and result['completion_update_id'] is not None, 'Actual owner approval required')
        return {'requestId': award['requestId'], 'projectId': award['projectId'], 'listingId': award['listingId'],
                'winnerUserCode': award['winnerUserCode'], 'title': award['title'],
                'completionUpdateId': result['completion_update_id']}


def restricted_app(app, completed, unfinished):
    from fastapi.responses import JSONResponse
    from routes.property_network import NO_STORE

    allowed_posts = {
        '/auth/login',
        f"/property-network/service-requests/{completed['requestId']}/review",
        f"/property-network/service-requests/{unfinished['requestId']}/review",
        f"/property-network/projects/{completed['projectId']}/members/{completed['winnerUserCode']}/revoke",
    }

    async def serve(scope, receive, send):
        if scope['type'] == 'http' and scope['method'] not in {'GET', 'HEAD', 'OPTIONS'}:
            if scope['method'] != 'POST' or scope['path'] not in allowed_posts:
                refused = JSONResponse({'detail': 'Mutation unavailable in browser rehearsal'}, status_code=405, headers=NO_STORE)
                return await refused(scope, receive, send)
        return await app(scope, receive, send)

    return serve


def verify_database(completed, unfinished, users, transport_attempts):
    from database import SessionLocal
    from models.property_network import NetworkProjectMember, NetworkProjectUpdate
    from models.service_review import NetworkServiceReview
    from models.service_visit import NetworkServiceWork, NetworkServiceWorkAction
    from models.service_workflow import NetworkServiceRequest

    request_id = UUID(completed['requestId'])
    project_id = UUID(completed['projectId'])
    unfinished_id = UUID(unfinished['requestId'])
    with SessionLocal() as db:
        review = db.query(NetworkServiceReview).one()
        fixture.require(review.request_id == request_id and review.reviewer_id == users['owner']
                        and review.provider_id == users['winner'] and review.stars == 5
                        and review.comment == REVIEW_COMMENT and review.created_at is not None,
                        'One exact private owner review required')
        work = db.query(NetworkServiceWork).one()
        fixture.require(work.request_id == request_id and work.status == 'completed'
                        and work.version == len(COMPLETION_ACTIONS)
                        and work.completion_update_id == UUID(completed['completionUpdateId']),
                        'Owner-approved completion and unchanged unfinished work required')
        actions = db.query(NetworkServiceWorkAction).all()
        fixture.require(len(actions) == len(COMPLETION_ACTIONS) and all(action.request_id == request_id for action in actions)
                        and {(action.actor_id, action.operation) for action in actions}
                        == {(users[actor], operation) for actor, operation in COMPLETION_ACTIONS},
                        'Exact owner and provider completion actions required')
        unfinished_request = db.get(NetworkServiceRequest, unfinished_id)
        fixture.require(unfinished_request.status == 'awarded' and unfinished_request.project_id == UUID(unfinished['projectId']),
                        'Separate unfinished award must remain intact')
        entry = db.query(NetworkProjectUpdate).one()
        fixture.require(entry.id == work.completion_update_id and entry.project_id == project_id
                        and entry.author_id == users['owner'] and entry.kind == 'progress'
                        and entry.body == fixture.COMPLETION and entry.occurred_on == fixture.COMPLETED_ON
                        and entry.correction_of is None and entry.media_ids == [],
                        'Only the original owner-approved completion belongs in property history')
        members = db.query(NetworkProjectMember).filter_by(user_id=users['winner'], role='professional').all()
        fixture.require(len(members) == 2 and {member.project_id: member.active for member in members}
                        == {project_id: False, UUID(unfinished['projectId']): True},
                        'Only the completed award membership must be revoked')
    fixture.require(not transport_attempts, 'No external transport allowed')
    return {'status': 'PASS', 'reviews': 1, 'review_stars': 5, 'progress_entries': 1,
            'review_in_history_entries': 0, 'winner_membership_revoked': True, 'external_transport_attempts': 0}


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
        award = original['serviceVisit']
        fixture.advance('seed_unfinished_review_award')
        unfinished = create_unfinished_award(UUID(award['listingId']), users)
        fixture.advance('seed_completed_review_award')
        completed = complete_award(award, users)
        fixture.advance('write_fixture')
        fd = os.open(fixture_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        owned = os.fstat(fd)
        previous_handlers = {}
        try:
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                json.dump({'accounts': original['accounts'], 'serviceReview': {'completed': completed, 'unfinished': unfinished}},
                          stream, ensure_ascii=True)
                stream.write('\n')
            fixture.advance('serve_application')
            server = uvicorn.Server(uvicorn.Config(restricted_app(app, completed, unfinished), host=fixture.HOST,
                                                   port=fixture.API_PORT, lifespan='off', access_log=False, log_level='warning'))

            def finish_shutdown(_number, _frame):
                server.should_exit = True

            for number in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[number] = signal.signal(number, finish_shutdown)
            server.run()
            fixture.require(server.started, 'Synthetic service review API failed to start')
            fixture.advance('verify_review')
            report = verify_database(completed, unfinished, users, transport_attempts)
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
    print('SERVICE_REVIEW_BROWSER_FIXTURE_RESULT ' + json.dumps(report, sort_keys=True, separators=(',', ':')), flush=True)


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

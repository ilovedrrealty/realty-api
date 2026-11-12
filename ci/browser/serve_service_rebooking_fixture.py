"""Serve a private rebooking rehearsal with disposable loopback Postgres."""

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
import serve_service_review_fixture as review
import serve_service_visit_fixture as visit


LISTING_TITLE = 'Browser rehearsal villa'
PROVIDER_ALIAS = 'Synthetic winner service provider'
ORIGINAL_BRIEF = 'Synthetic pool service scope'
NEW_TITLE = 'Nueva solicitud de piscina de prueba'
NEW_ALIAS = 'Propietaria de prueba'
NEW_BRIEF = 'Solicito una nueva limpieza de la piscina la próxima semana.'
PRIVATE_TITLE = 'Browser rehearsal private registry pool service'
FILLER_ROLES = {f'filler{index:02}': 'buyer' for index in range(1, 31)}
SNAPSHOT_TABLES = (
    'listings', 'network_properties', 'network_service_preferences', 'network_service_requests',
    'network_service_recipients', 'network_service_proposals', 'network_service_actions', 'network_projects',
    'network_project_members', 'network_service_work', 'network_service_work_actions', 'network_project_updates',
    'network_service_reviews', 'network_service_negotiation_messages', 'network_service_conversation_messages',
)


def exact_source():
    commit = os.environ.get('ILDR_REBOOKING_APP_COMMIT', '')
    tree = os.environ.get('ILDR_REBOOKING_APP_TREE', '')
    migration = os.environ.get('ILDR_REBOOKING_MIGRATION_HEAD', '')
    visit.require(all(re.fullmatch('[0-9a-f]{40}', value) for value in (commit, tree))
                  and re.fullmatch('[0-9a-f]{12}', migration), 'Exact service rebooking source required')
    visit.APP_COMMIT = commit
    visit.APP_TREE = tree
    visit.MIGRATION_HEAD = migration
    return visit.verified_checkout()


def seed_pagination(award, users):
    from database import SessionLocal
    from models.listing import Listing
    from models.user import User
    from routes.property_network import properties
    from schemas.service_workflow import PreferenceWrite
    from services import service_workflow as workflow

    with SessionLocal() as db:
        owner = db.get(User, users['owner'])
        for index, name in enumerate(FILLER_ROLES, start=1):
            provider = db.get(User, users[name])
            workflow.save_preference(db, provider, PreferenceWrite(
                submission_id=uuid4(), expected_version=0, service_opt_in=True,
                member_alias=f'Synthetic filler provider {index:02}', category_ids=['pool_service'],
                property_class_ids=['house_villa'],
            ))
        db.add_all(Listing(id=uuid4(), title=f'A{index:02} synthetic source', type='villa', transaction='rent',
                           price=1000, currency='USD', location='Synthetic service area', status='active',
                           images=[], submitted_by=owner.id, owner_id=owner.id)
                   for index in range(1, len(FILLER_ROLES) + 1))
        db.commit()
        first_providers = workflow.discovery(db, owner, 'pool_service', 'house_villa', 1)
        second_providers = workflow.discovery(db, owner, 'pool_service', 'house_villa', 2)
        first_listings = properties(page=1, user=owner, db=db)
        second_listings = properties(page=2, user=owner, db=db)
        provider = next((row for row in second_providers['items'] if row['user_code'] == award['winnerUserCode']), None)
        listing = next((row for row in second_listings['items'] if row['id'] == award['listingId']), None)
        visit.require(len(first_providers['items']) == len(first_listings['items']) == 30
                      and all(row['user_code'] != award['winnerUserCode'] for row in first_providers['items'])
                      and all(row['id'] != award['listingId'] for row in first_listings['items'])
                      and provider is not None and provider['member_alias'] == PROVIDER_ALIAS
                      and listing is not None and listing['title'] == LISTING_TITLE,
                      'Exact original provider and listing must both be absent from page one')


def create_private_award(users):
    from database import SessionLocal
    from models.user import User
    from schemas.property_network import RegistryCreate
    from schemas.service_workflow import AwardWrite, ProposalTerms, QuoteWrite, RecipientAction, RequestCreate
    from services import property_network as network, service_workflow as workflow

    with SessionLocal() as db:
        owner = db.get(User, users['owner'])
        winner = db.get(User, users['winner'])
        private = network.create_registry(db, owner, RegistryCreate(
            submission_id=uuid4(), title='Synthetic private registry source', location='Synthetic service area',
            property_class='house_villa', relationship='owner', authority_acknowledged=True,
        ))
        request = workflow.create_request(db, owner, RequestCreate(
            submission_id=uuid4(), property_id=UUID(private['id']), category_id='pool_service', property_class='house_villa',
            title=PRIVATE_TITLE, brief='Synthetic registry pool service scope', requester_alias='Synthetic property owner',
            recipient_user_codes=[winner.user_code], shared_brief_acknowledged=True,
        ))
        request_id = UUID(request['id'])
        initial = workflow.get_request(db, winner, request_id, view='received')['recipient']
        recipient_id = UUID(initial['id'])
        accepted = workflow.recipient_action(db, winner, request_id, recipient_id, RecipientAction(
            submission_id=uuid4(), expected_recipient_version=initial['version'],
        ), 'recipient_accept')
        workflow.write_proposal(db, winner, request_id, recipient_id, QuoteWrite(
            submission_id=uuid4(), expected_recipient_version=accepted['recipient']['version'],
            terms=ProposalTerms(amount='145.00', currency='USD', pricing_basis='estimate',
                                scope_text='Synthetic quote for private registry service',
                                valid_until=workflow.today_dr() + timedelta(days=10)),
        ))
        managed = workflow.get_request(db, owner, request_id, view='managed')
        recipient, = managed['recipients']
        proposal = recipient['current_proposal']
        award = workflow.close_request(db, owner, request_id, AwardWrite(
            submission_id=uuid4(), expected_request_version=managed['version'], recipient_id=recipient_id,
            expected_recipient_version=recipient['version'], proposal_id=UUID(proposal['id']),
            expected_proposal_version=proposal['version'], project_title='Synthetic private registry service project',
            project_summary='Synthetic approved registry scope', project_scope_acknowledged=True,
        ), award=True)
        return {'requestId': str(request_id), 'projectId': award['project']['id'], 'listingId': None,
                'propertyId': private['id'], 'winnerUserCode': winner.user_code, 'title': PRIVATE_TITLE}


def seed_cases():
    with patch.object(visit, 'ROLES', {**FILLER_ROLES, **visit.ROLES}):
        original, users = visit.migrate_and_seed()
    award = original['serviceVisit']
    visit.advance('seed_rebooking_pagination')
    seed_pagination(award, users)
    visit.advance('seed_rebooking_awards')
    unfinished = review.create_unfinished_award(UUID(award['listingId']), users)
    completed = review.complete_award(award, users)
    private_award = create_private_award(users)
    with patch.object(visit, 'CONFIRMED_START', visit.CONFIRMED_START - timedelta(minutes=90)):
        private = review.complete_award(private_award, users)
    del private['listingId']
    private['propertyId'] = private_award['propertyId']
    accounts = {name: original['accounts'][name] for name in visit.ROLES}
    cases = {'completed': completed, 'unfinished': unfinished, 'privateCompleted': private,
             'listingTitle': LISTING_TITLE, 'providerAlias': PROVIDER_ALIAS, 'originalBrief': ORIGINAL_BRIEF,
             'providerFirstPageAbsent': True, 'listingFirstPageAbsent': True}
    return {'accounts': accounts, 'serviceRebooking': cases}, users


def restricted_app(app, completed):
    from fastapi.responses import JSONResponse
    from routes.property_network import NO_STORE

    allowed_posts = {
        '/auth/login', '/property-network/service-requests',
        f"/property-network/projects/{completed['projectId']}/members/{completed['winnerUserCode']}/revoke",
    }

    async def serve(scope, receive, send):
        if scope['type'] == 'http' and scope['method'] not in {'GET', 'HEAD', 'OPTIONS'}:
            if scope['method'] != 'POST' or scope['path'] not in allowed_posts:
                refused = JSONResponse({'detail': 'Mutation unavailable in browser rehearsal'}, status_code=405, headers=NO_STORE)
                return await refused(scope, receive, send)
        return await app(scope, receive, send)

    return serve


def snapshot(db):
    from sqlalchemy import text

    return {table: db.execute(text(f'SELECT to_jsonb(record) FROM {table} AS record')).scalars().all()
            for table in SNAPSHOT_TABLES}


def verify_database(cases, users, original, transport_attempts):
    from database import SessionLocal
    from schemas.service_workflow import RequestCreate
    from services import service_workflow as workflow

    completed = cases['completed']
    listing_id, project_id = completed['listingId'], completed['projectId']
    original_ids = {row['id'] for row in original['network_service_requests']}
    with SessionLocal() as db:
        current = snapshot(db)
    fresh, = [row for row in current['network_service_requests'] if row['id'] not in original_ids]
    request_id = fresh['id']
    recipient, = [row for row in current['network_service_recipients'] if row['request_id'] == request_id]
    action, = [row for row in current['network_service_actions'] if row['request_id'] == request_id]
    winner = str(users['winner'])
    owner = str(users['owner'])
    code = completed['winnerUserCode']
    previous_keys = {row['submission_id'] for table in ('network_service_actions', 'network_service_work_actions')
                     for row in original[table]}
    visit.require(UUID(request_id).version == 4 and UUID(action['submission_id']).version == 4
                  and action['submission_id'] not in previous_keys and action['operation'] == 'request_create'
                  and action['actor_id'] == owner and action['target_id'] == request_id
                  and action['recipient_id'] is None and action['proposal_id'] is None,
                  'One fresh actor-bound create action required')
    expected_input = RequestCreate(submission_id=UUID(action['submission_id']), listing_id=UUID(listing_id),
        category_id='pool_service', property_class='house_villa', title=NEW_TITLE, brief=NEW_BRIEF,
        requester_alias=NEW_ALIAS, recipient_user_codes=[code], shared_brief_acknowledged=True)
    expected_digest = workflow.fingerprint('request_create', UUID(listing_id), expected_input)
    visit.require(fresh['listing_id'] == listing_id and fresh['property_id'] is None and fresh['project_id'] is None
                  and fresh['requester_id'] == fresh['custodian_id'] == owner
                  and fresh['status'] == 'open' and fresh['version'] == 1 and fresh['category_id'] == 'pool_service'
                  and fresh['property_class'] == 'house_villa' and fresh['title'] == NEW_TITLE and fresh['brief'] == NEW_BRIEF
                  and fresh['requester_alias'] == NEW_ALIAS and fresh['area_label'] == '' and fresh['desired_start_on'] is None
                  and fresh['recipient_user_codes'] == [code] and fresh['scope_sha256'] == action['creation_sha256'] == expected_digest
                  and recipient['provider_id'] == winner and recipient['user_code'] == code
                  and recipient['member_alias'] == PROVIDER_ALIAS and recipient['status'] == 'pending'
                  and recipient['version'] == 1 and recipient['current_proposal_id'] is None,
                  'Fresh request must contain only the explicitly entered scope and exact selected provider')

    current['network_service_requests'].remove(fresh)
    current['network_service_recipients'].remove(recipient)
    current['network_service_actions'].remove(action)
    prior_project, = [row for row in original['network_projects'] if row['id'] == project_id]
    after_project, = [row for row in current['network_projects'] if row['id'] == project_id]
    visit.require(after_project['version'] == prior_project['version'] + 1, 'Only one project revocation expected')
    current['network_projects'].remove(after_project)
    current['network_projects'].append({**after_project, 'version': prior_project['version'], 'updated_at': prior_project['updated_at']})
    member, = [row for row in current['network_project_members'] if row['project_id'] == project_id and row['user_id'] == winner]
    visit.require(member['active'] is False and member['role'] == 'professional', 'Exact original winner must be revoked')
    member['active'] = True
    for table in SNAPSHOT_TABLES:
        visit.require(sorted(json.dumps(row, sort_keys=True) for row in current[table])
                      == sorted(json.dumps(row, sort_keys=True) for row in original[table]),
                      'Prior synthetic application rows must otherwise stay unchanged')
    updates = original['network_project_updates']
    visit.require(len(updates) == 2 and {row['id'] for row in updates}
                  == {completed['completionUpdateId'], cases['privateCompleted']['completionUpdateId']}
                  and all(row['kind'] == 'progress' and row['author_id'] == owner and row['body'] == visit.COMPLETION for row in updates),
                  'The two original approved progress rows must remain separate and unchanged')
    visit.require(not transport_attempts, 'No external transport allowed')
    return {'status': 'PASS', 'requests_created': 1, 'new_recipient_count': 1,
            'original_listing_progress_entries': 1, 'original_private_progress_entries': 1,
            'original_award_unchanged': True, 'winner_membership_revoked': True,
            'provider_first_page_absent': True, 'listing_first_page_absent': True, 'external_transport_attempts': 0}


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

        fixture, users = seed_cases()
        cases = fixture['serviceRebooking']
        with SessionLocal() as db:
            original = snapshot(db)
        visit.advance('write_fixture')
        fd = os.open(fixture_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        owned = os.fstat(fd)
        previous_handlers = {}
        try:
            with os.fdopen(fd, 'w', encoding='ascii') as stream:
                json.dump(fixture, stream, ensure_ascii=True)
                stream.write('\n')
            visit.advance('serve_application')
            server = uvicorn.Server(uvicorn.Config(restricted_app(app, cases['completed']), host=visit.HOST,
                                                   port=visit.API_PORT, lifespan='off', access_log=False, log_level='warning'))

            def finish_shutdown(_number, _frame):
                server.should_exit = True

            for number in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[number] = signal.signal(number, finish_shutdown)
            server.run()
            visit.require(server.started, 'Synthetic service rebooking API failed to start')
            visit.advance('verify_rebooking')
            report = verify_database(cases, users, original, transport_attempts)
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
    print('SERVICE_REBOOKING_BROWSER_FIXTURE_RESULT ' + json.dumps(report, sort_keys=True, separators=(',', ':')), flush=True)


if __name__ == '__main__':
    try:
        run()
    except Exception as error:
        error_type = type(error).__name__
        frames = [frame.lineno for frame in traceback.extract_tb(error.__traceback__)
                  if frame.filename in {__file__, review.__file__, visit.__file__}]
        print('BROWSER_API_FIXTURE_FAILURE ' + json.dumps({'stage': visit.stage,
              'error_type': error_type if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', error_type) else 'UnhandledError',
              'fixture_line': frames[-1] if frames else None}), flush=True)
        raise SystemExit(1) from None

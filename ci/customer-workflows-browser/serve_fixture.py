"""Owned synthetic assignment UI; two sequential, independently marked databases.

No provider worker runs while the UI is served. Three explicit pre-baseline
MockTransport handoffs prepare real queue outcomes, using the production producer.
"""
from collections import Counter
from contextlib import closing, redirect_stdout, redirect_stderr
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from importlib.util import spec_from_file_location, module_from_spec
from io import StringIO
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
from uuid import UUID, uuid4

COMMIT = 'f9315a9fee76f46df7ecbf8e5d67d26de1bd2d4b'
TREE = 'e48d0e9b22bf66b5a921befb2ad2e892378e242f'
BASE_COMMIT = '26ca867a1008868f7dc5f322d28dc289a7763980'
BASE_TREE = '3a30b7e8c5b53827b97374c18806a7cb03ef9c5c'
HEAD = 'f7a9c3e2d114'
TOOLS = Path(__file__).resolve().parent.parent
FACTS = Path('/browser-fixtures/fixtures.json')
ROLES = {'admin':'admin','buyer':'buyer','owner':'owner','realtor':'realtor'}
NEW_TABLES = ('assignment_email_jobs', 'assignment_events')
INSIDE = ContextVar('owned_assignment_metadata', default=False)
STAGE = 'source'
EXTERNAL = []
GUARD = False
REPORTED = False
ERROR_TYPES = {'Failure','RuntimeError','ValueError','TypeError','AssertionError','KeyError','AttributeError','ImportError',
 'ModuleNotFoundError','OperationalError','ProgrammingError','IntegrityError','DataError','InterfaceError','InternalError',
 'TimeoutError','OSError','PermissionError','FileNotFoundError','CalledProcessError','TimeoutExpired','UnhandledError','HTTPException','RehearsalFailure'}


class Failure(RuntimeError):
    pass


def require(value, reason):
    if not value:
        raise Failure(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def load(path, expected, name):
    require(path.is_file() and not path.is_symlink() and os.statvfs(path).f_flag & os.ST_RDONLY
            and sha256(path.read_bytes()).hexdigest() == expected, 'exact_readonly_helper_required')
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def helpers():
    old = load(TOOLS/'signup-email-history-browser/serve_fixture.py',
        '45bfce8669f2efeefdfbb9e115da0462d0b94af99b993cc734b5ca80fb685bf3', 'assignment_signup_primitives')
    native = load(TOOLS/'native-inbox/verify.py',
        '388fcae33bf63234df9739ac7405c1a2d49b4914af2a32d56e9023fdb7ed144e', 'assignment_native_primitives')
    native.COMMIT, native.TREE = COMMIT, TREE
    base = native.helpers()
    return old, native, base


def claim(locale):
    import psycopg2
    nonce = os.environ.get('ILDR_BROWSER_CI_NONCE', '')
    require(re.fullmatch('[a-f0-9]{32}', nonce) and locale in ('en','es'), 'owned_nonce_locale_required')
    name = 'ildr_browser_ci_' + nonce
    marker = 'ildr-customer-workflows-browser-ci:' + nonce + ':' + locale
    with closing(psycopg2.connect(host='127.0.0.1', port=55449, dbname=name, user='postgres',
                                 password='', connect_timeout=4)) as db, db.cursor() as c:
        c.execute("""SELECT current_database(),host(inet_server_addr()),inet_server_port(),
          current_setting('server_version_num')::int,shobj_description(oid,'pg_database'),
          NOT EXISTS(SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'),
          NOT EXISTS(SELECT FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%%')
          FROM pg_database WHERE datname=current_database()""")
        require(c.fetchone() == (name,'127.0.0.1',55449,170006,marker,True,True), 'fresh_marked_database_required')
    return name, f'postgresql+psycopg2://postgres@127.0.0.1:55449/{name}'


def timestamp(value):
    return value.isoformat() if value is not None else None


def rows(catalog, database, table):
    # All callers use these finite, source-owned table names, never request text.
    require(table in ('users','leads','listings','professional_profiles','professional_profile_revisions','activity_log',*NEW_TABLES), 'row_table_allowlist_required')
    return catalog.query(database, 'SELECT to_jsonb(t) FROM public.' + table + ' t')


def upgrade106(base, url):
    root = Path('/baseline106')
    before = base.APP_COMMIT, base.APP_TREE
    try:
        base.APP_COMMIT, base.APP_TREE = BASE_COMMIT, BASE_TREE
        os.environ['ILDR_BROWSER_APPLICATION_ROOT'] = str(root)
        require(base.verified_checkout() == root, 'exact26ca_baseline_required')
    finally:
        base.APP_COMMIT, base.APP_TREE = before
        os.environ.pop('ILDR_BROWSER_APPLICATION_ROOT', None)
    source = "import sys;sys.path.insert(0,'/baseline106');from alembic import command;from alembic.config import Config;command.upgrade(Config('alembic.ini'),'head')"
    result = subprocess.run(['/usr/local/bin/python','-I','-B','-c',source], cwd=root,
        env=dict(os.environ,DATABASE_URL=url), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    require(result.returncode == 0 and len(result.stdout) <= 2*1024*1024, 'actual_baseline106_upgrade_required')


def seed(old, native, base, catalog, retained, database, url, locale):
    global STAGE
    STAGE = 'previous99'
    native.previous_start(base, url)
    legacy = tuple(sorted(catalog.OLD_TABLES + native.GROUPS))
    require(catalog.table_names(database) == legacy and catalog.query(database,'SELECT version_num FROM alembic_version')
            == [(native.OLD_HEAD,)], 'actual_previous99_required')
    require(retained.seed_old() == native.EXPECTED_SEED, 'retained_original_seed_required')
    old_schema, old_rows = catalog.catalog(database, legacy), catalog.snapshot(database, legacy)
    old_columns = {name:[row[1] for row in old_schema['columns'] if row[0] == name] for name in legacy}
    STAGE = 'baseline106'
    upgrade106(base, url)
    tables106 = tuple(sorted(legacy + native.INBOX))
    require(catalog.table_names(database) == tables106 and catalog.query(database,'SELECT version_num FROM alembic_version')
            == [('e8b5a3d1f907',)], 'actual_e8_106_required')
    require(catalog.snapshot(database, legacy, columns=old_columns) == old_rows, 'original99_old_columns_preserved_required')
    before = catalog.snapshot(database,tables106), catalog.catalog(database,tables106), old.security(catalog,database)
    STAGE = 'current108'
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config('alembic.ini'),'head')
    tables = tuple(sorted(tables106 + NEW_TABLES))
    require(catalog.table_names(database) == tables and len(tables) == 108
            and catalog.query(database,'SELECT version_num FROM alembic_version') == [(HEAD,)], 'actual_f7_108_required')
    require(catalog.snapshot(database,tables106) == before[0], 'upgrade106_rows_sequences_preserved_required')
    current = catalog.catalog(database,tables106)
    require(all(current[key] == value for key,value in before[1].items() if key != 'functions')
            and all(value in current['functions'] for value in before[1]['functions']), 'upgrade106_catalog_preserved_required')
    secure = old.security(catalog,database)
    require(all(secure[key] == value for key,value in before[2].items() if key not in ('functions_acl','public_types'))
            and all(value in secure['functions_acl'] for value in before[2]['functions_acl'])
            and all(value in secure['public_types'] for value in before[2]['public_types']), 'upgrade106_security_preserved_required')
    import models.trade_contact
    from database import Base, SessionLocal
    require(set(Base.metadata.tables) == set(tables)-{'alembic_version'} and len(Base.metadata.tables) == 107,
            'independent_exact107_model_names_required')
    from models.user import User
    from models.lead import Lead
    from models.lead_delivery import LeadDeliveryJob
    from models.seller_valuation import SellerValuationIntake
    from routes.leads import admin_assign_lead, AssignBody
    from starlette.requests import Request
    from config import settings
    from utils.security import hash_password
    from services import assignment_notification as notification
    import httpx
    identities = {actor:retained.IDS[actor] for actor in ROLES}
    accounts = {actor:{'email':'','password':secrets.token_urlsafe(24)} for actor in ROLES}
    facts = {'locale':locale,'ids':{key:str(value) for key,value in identities.items()},'roles':ROLES,
             'names':{},'codes':{},'emails':{},'leads':{},'unknown_id':str(UUID(int=999999)),'invalid_id':'not-a-uuid',
             'control_secret':secrets.token_urlsafe(32), 'seed_requests':[],
             'private_markers':['OWNED_CUSTOMER_WORKFLOWS_PRIVATE_BODY','owned-private-provider-id','synthetic-never-send@example.com']}
    STAGE = 'seed_signup'
    statuses = ('pending','processing','retry_wait','accepted','failed','needs_review','skipped','pending','accepted','failed','needs_review')
    steps = ['email_'+str(index) for index in range(1,11)]+['customer_email']
    permutation = (7,2,10,5,0,9,3,8,1,6,4)
    stamp = retained.HISTORY+timedelta(days=700)
    with SessionLocal.begin() as db:
        require(db.query(LeadDeliveryJob).filter(LeadDeliveryJob.user_id.isnot(None)).count() == 0, 'empty_signup_seed_required')
        for actor, identity in identities.items():
            user = db.get(User, identity)
            require(user.role == ROLES[actor] and user.status == 'active' and user.email_verified, 'unchanged_four_role_seed_required')
            accounts[actor]['email'] = user.email
            facts['emails'][actor] = user.email
            facts['private_markers'].append(user.email)
            user.password_hash = hash_password(accounts[actor]['password'])
            facts['names'][actor],facts['codes'][actor] = user.display_name,user.user_code
        require(db.get(User,identities['buyer']).assigned_realtor_id is None
            and db.get(User,identities['owner']).assigned_realtor_id is None, 'initial_accounts_unassigned_required')
        for index, step in enumerate(steps):
            state = statuses[index]
            attempted = state not in ('pending','skipped')
            payload = {'marker':'OWNED_CUSTOMER_WORKFLOWS_PRIVATE_BODY','to':'synthetic-never-send@example.com','html':'Synthetic private signup body','step':step}
            db.add(LeadDeliveryJob(id=UUID(int=81000+permutation[index]),user_id=identities['buyer'],lead_id=None,
                step=step,provider='resend',payload=payload,payload_sha256=sha256(canonical(payload).encode()).hexdigest(),
                status=state,attempt_count=int(attempted),safe_to_retry=False,outcome_uncertain=state=='needs_review',
                created_at=stamp,updated_at=stamp,next_attempt_at=stamp,first_attempt_at=stamp if attempted else None,
                last_attempt_at=stamp if attempted else None,accepted_at=stamp if state=='accepted' else None,
                error_code='resend_outcome_unknown' if state=='needs_review' else 'resend_http_429' if state=='failed' else None,
                provider_id='owned-private-provider-id-'+str(index) if state=='accepted' else None))
        for index,key in enumerate(('main','empty','valuation','seller')):
            identity = UUID(int=82000+index)
            name = 'Synthetic '+locale+' '+key+' assignment'
            db.add(Lead(id=identity,type='seller_interest' if key in ('valuation','seller') else 'buyer_interest',
                name=name,email='owned-'+key+'@example.com',message='OWNED_CUSTOMER_WORKFLOWS_PRIVATE_BODY',
                from_user_id=identities['buyer'] if key in ('valuation','seller') else None,status='new',ghl_contact_id=None))
            facts['leads'][key] = {'id':str(identity),'name':name}
        db.flush()
        db.add(SellerValuationIntake(lead_id=UUID(facts['leads']['valuation']['id']),
            subject={'property_type':'villa','location':'Synthetic owned location'}))
        db.flush()
        require(db.get(User,UUID(facts['unknown_id'])) is None and db.get(Lead,UUID(facts['unknown_id'])) is None,
                'owned_unused_unknown_id_required')
        facts['signup_items'] = [old.metadata_item(row) for row in db.query(LeadDeliveryJob).filter(
            LeadDeliveryJob.user_id==identities['buyer']).order_by(LeadDeliveryJob.created_at.desc(),LeadDeliveryJob.id.desc()).all()]
    STAGE = 'seed_assignment_queue'
    mock_calls = []
    request = Request({'type':'http','method':'PUT','headers':[(b'content-type',b'application/json'),
        (b'origin',b'http://127.0.0.1:4175')],'path':'/owned-seed','query_string':b''})
    saved_settings = settings.lead_delivery_enabled,settings.resend_api_key
    try:
        settings.lead_delivery_enabled,settings.resend_api_key = True,'synthetic-owned-mock-key'
        for index in range(11):
            request_id = UUID(int=83000+index)
            facts['seed_requests'].append(str(request_id))
            with SessionLocal() as db:
                admin_assign_lead(facts['leads']['main']['id'],AssignBody(realtor_id=str(identities['realtor']),request_id=str(request_id)),
                                  request,db.get(User,identities['admin']),db)
            if index < 3:
                async def transport(message, number=index):
                    require(message.method == 'POST' and str(message.url) == 'https://api.resend.com/emails', 'fixed_mock_provider_route_required')
                    mock_calls.append(number)
                    if number == 2:
                        raise httpx.ReadError('owned synthetic response loss',request=message)
                    return httpx.Response(200,json={'id':'owned-private-provider-id'}) if number == 0 else httpx.Response(422,json={'error':'owned refusal'})
                result = notification.run_batch(SessionLocal,max_jobs=1,transport=httpx.MockTransport(transport))
                require(result == {'enabled':True,'processed':1}, 'actual_prebaseline_mock_batch_required')
    finally:
        settings.lead_delivery_enabled,settings.resend_api_key = saved_settings
    require(mock_calls == [0,1,2] and settings.lead_delivery_enabled is False and not settings.resend_api_key,
            'three_mock_calls_and_disabled_ui_worker_required')
    seed_workflows(catalog, retained, database, facts, identities, locale)
    return tables,tables106,accounts,identities,facts


WORKFLOW_CONTROL_ORDER = ('own_version_advance','own_unverified','own_verified','managed_version_advance',
    'admin_unverified','admin_verified','manual_contact_change','manual_contact_restore',
    'manual_property_withdrawn','manual_property_restore','manual_assignee_admin','manual_assignee_restore',
    'manual_realtor_unverified','manual_realtor_verified')
FACETS = ('provider_kind_ids','property_class_ids','work_scope_ids')


def seed_workflows(catalog, retained, database, facts, identities, locale):
    """Declared synthetic additions precede the UI baseline; old46 drafts remain exact."""
    global STAGE
    STAGE='seed_workflows'
    from database import SessionLocal
    from models.professional_profile import ProfessionalProfile, ProfessionalProfileRevision
    from models.lead import Lead
    from models.listing import Listing
    from schemas.professionals import ProfessionalDraft
    from routes.professionals import professional_options
    from sqlalchemy import null
    from copy import deepcopy
    stamp = retained.HISTORY + timedelta(days=701)
    published_specs = {
        'owned':(['professional_practice'],['house_villa'],['advisory'],['cabarete'],['en','es']),
        'published_a':(['professional_practice'],['house_villa'],['design'],['cabarete'],['en','es']),
        'published_b':(['contractor'],['farm_rural'],['repair'],['sosua'],['es']),
        'published_c':(['supplier'],['house_villa'],['repair'],['santiago'],['en'])}
    selected = {'owned':(['contractor','supplier'],['house_villa','farm_rural'],['installation','repair']),
                'managed':(['contractor','supplier'],['hotel_hospitality','warehouse_industrial'],['repair','supply'])}
    workflow = {'profiles':{},'options':professional_options(),'manual':{},'control_order':list(WORKFLOW_CONTROL_ORDER)}
    before_profiles = rows(catalog,database,'professional_profiles')
    before_revisions = rows(catalog,database,'professional_profile_revisions')
    require(len(before_profiles)==len(before_revisions)==46, 'workflow_original46_private_profiles_required')
    with SessionLocal.begin() as db:
        listing=db.get(Listing,retained.LISTING)
        require(listing is not None and listing.status=='active', 'workflow_owned_public_listing_required')
        for index,key in enumerate(('owned','managed','published_a','published_b','published_c')):
            identity=UUID(int=86000+index)
            require(db.get(ProfessionalProfile,identity) is None, 'workflow_unused_profile_identity_required')
            draft=ProfessionalDraft(business_name='Synthetic '+locale+' private '+key,
                contact_name='Synthetic '+locale+' contact '+key,category_ids=['engineering'],
                service_area_ids=['cabarete'],languages=['en','es'],
                descriptions={'en':'Synthetic private business description '+key,'es':'Descripción privada sintética '+key},
                public_email='owned-'+key+'@example.com',public_contact_consent=key in published_specs,
                provider_kind_ids=['supplier'],property_class_ids=['warehouse_industrial'],work_scope_ids=['supply']).model_dump(mode='json')
            published=None
            if key in published_specs:
                providers,classes,scopes,areas,languages=published_specs[key]
                published=deepcopy(draft)
                published.update(business_name='Synthetic '+locale+' published '+key,
                    descriptions={'en':'Approved synthetic public description '+key,'es':'Descripción pública sintética aprobada '+key},
                    provider_kind_ids=providers,property_class_ids=classes,work_scope_ids=scopes,
                    service_area_ids=areas,languages=languages)
            profile=ProfessionalProfile(id=identity,user_id=identities['owner'] if key=='owned' else None,version=1,
                draft_data=draft,published_data=published if published is not None else null(),
                review_status='approved' if published is not None else 'draft',
                listing_status='published' if published is not None else 'hidden',
                review_note='PRIVATE_WORKFLOW_REVIEW_NOTE_'+key,reviewed_at=stamp if published else None,
                reviewed_by=identities['admin'] if published else None,published_at=stamp if published else None,
                created_at=stamp,updated_at=stamp)
            db.add(profile);db.flush()
            db.add(ProfessionalProfileRevision(id=UUID(int=86100+index),profile_id=identity,version=1,
                action='save' if key=='owned' else 'create_managed',actor_id=identities['owner'] if key=='owned' else identities['admin'],
                snapshot={'draft':deepcopy(draft),'published':deepcopy(published),
                          'review_status':profile.review_status,'listing_status':profile.listing_status},created_at=stamp))
            item={'id':str(identity),'version':1,'draft':draft,'published':published,'review_note':profile.review_note}
            if key in selected:
                item['selected_draft']=deepcopy(draft)
                item['selected_draft'].update(dict(zip(FACETS,selected[key])))
            workflow['profiles'][key]=item
        for index,key in enumerate(('valid','invalid')):
            identity=UUID(int=87000+index)
            phone='+18095550101' if key=='valid' else '+18095550101 ext 2'
            name='Synthetic '+locale+' follow-up '+key
            db.add(Lead(id=identity,type='property_inquiry',name=name,email='owned-followup-'+key+'@example.com',phone=phone,
                property_id=listing.id,assigned_realtor_id=identities['realtor'],status='assigned',assigned_at=stamp,
                message='PRIVATE_WORKFLOW_CUSTOMER_MESSAGE',tracking={'private_note':'PRIVATE_WORKFLOW_TRACKING'},created_at=stamp))
            workflow['manual'][key]={'id':str(identity),'name':name,'phone':phone,'email':'owned-followup-'+key+'@example.com',
                'property_id':str(listing.id),'property_title':listing.title,'assignee_id':str(identities['realtor'])}
    require(all(row in rows(catalog,database,'professional_profiles') for row in before_profiles)
        and all(row in rows(catalog,database,'professional_profile_revisions') for row in before_revisions),
        'workflow_old46_rows_exact_after_declared_seed_required')
    cases=[({},['owned','published_a','published_b','published_c']),
        ({'provider_kind':['professional_practice','contractor']},['owned','published_a','published_b']),
        ({'provider_kind':['professional_practice','contractor'],'property_class':['house_villa']},['owned','published_a']),
        ({'provider_kind':['professional_practice','contractor'],'property_class':['house_villa'],'work_scope':['design']},['published_a']),
        ({'provider_kind':['laboratory'],'property_class':['warehouse_industrial'],'work_scope':['supply']},[]),
        ({'provider_kind':['professional_practice','contractor'],'area':['cabarete'],'language':'es'},['owned','published_a'])]
    workflow['directory_cases']=[{'filters':filters,'ids':[workflow['profiles'][key]['id'] for key in keys]} for filters,keys in cases]
    facts['workflows']=workflow
    facts['private_markers'] += ['PRIVATE_WORKFLOW_REVIEW_NOTE','PRIVATE_WORKFLOW_CUSTOMER_MESSAGE','PRIVATE_WORKFLOW_TRACKING']


def event_specs(facts):
    ids,leads = facts['ids'],facts['leads']
    # Request order is independently matched before invoking the real route.
    return [('lead',leads['main']['id'],ids['realtor'],204),
            ('lead',leads['main']['id'],ids['admin'],204),
            ('lead',leads['main']['id'],ids['realtor'],204), # exact old UUID replay
            ('lead',leads['main']['id'],ids['realtor'],204),
            ('lead',leads['main']['id'],ids['realtor'],404),
            ('account',ids['owner'],ids['realtor'],204),('account',ids['owner'],None,204),
            ('lead',leads['valuation']['id'],ids['realtor'],204),('lead',leads['seller']['id'],ids['realtor'],204)]


def expected_payload(facts,kind,source,agent,notice):
    from utils import assignment_messages as messages
    key=next((key for key,value in facts['leads'].items() if value['id']==source),None)
    actor='admin' if agent==facts['ids']['admin'] else 'realtor'
    if notice=='lead_assigned_agent':
        from types import SimpleNamespace
        lead=SimpleNamespace(id=UUID(source),type='seller_interest' if key in ('valuation','seller') else 'buyer_interest',
            name=facts['leads'][key]['name'],email='owned-'+key+'@example.com',phone=None,
            message='OWNED_CUSTOMER_WORKFLOWS_PRIVATE_BODY')
        subject,html=messages.build_realtor_lead_assigned_notification(facts['names'][actor],lead,None,None)
    elif notice=='owner_upgrade_customer':
        subject,html=messages.build_upgrade_approved_notification(facts['names']['buyer'],'owner')
        actor='buyer'
    else:
        require(notice=='owner_assigned_agent','known_notice_template_required')
        subject,html=messages.build_realtor_assigned_owner_notification(facts['names'][actor],facts['names']['buyer'],facts['emails']['buyer'])
    address=facts['emails'][actor]
    return {'recipient_email':address,'body':{'from':'rehearsal@example.com','to':[address],'subject':subject,'html':html}}


def verify_events(catalog,database,facts,commands,seed_rows):
    events = [row[0] for row in rows(catalog,database,'assignment_events')]
    jobs = [row[0] for row in rows(catalog,database,'assignment_email_jobs')]
    expected_requests = set(facts['seed_requests']) | {item['body']['request_id'] for item in commands if item['status']==204}
    require(len(events) == len(expected_requests) == 18 and len(jobs) == 18
            and {row['request_id'] for row in events} == expected_requests, 'exact18_event_notice_request_set_required')
    seed_events,seed_jobs = seed_rows
    require(all(row in events for row in seed_events) and all(row in jobs for row in seed_jobs), 'immutable_seed_events_notices_required')
    ids,leads = facts['ids'],facts['leads']
    source_by_request = {request_id:('lead',leads['main']['id'],ids['realtor'],None if index==0 else ids['realtor'],1,None)
        for index,request_id in enumerate(facts['seed_requests'])}
    specs = event_specs(facts)
    for index,command in enumerate(commands):
        if index in (2,4):
            continue
        kind,source,agent,_status = specs[index]
        previous = ids['realtor'] if index in (0,1,6) else ids['admin'] if index==3 else None
        cycle = 2 if index in (1,6) else 3 if index==3 else 1
        source_by_request[command['body']['request_id']] = (kind,source,agent,previous,cycle,ids['buyer'] if index==8 else None)
    for row in events:
        kind,source,agent,previous,cycle,upgraded = source_by_request[row['request_id']]
        require(row['source_kind']==kind and row['lead_id']==(source if kind=='lead' else None)
            and row['user_id']==(source if kind=='account' else None) and row['actor_id']==ids['admin']
            and row['agent_id']==agent and row['previous_agent_id']==previous and row['cycle']==cycle
            and row['owner_upgraded_id']==upgraded and row['action']==('assigned' if agent else 'cleared')
            and row['request_sha256']==sha256(canonical({'source_kind':kind,'source_id':source,'agent_id':agent}).encode()).hexdigest(),
            'independent_event_actor_source_action_cycle_digest_required')
        notices = [job for job in jobs if job['event_id']==row['id']]
        kinds = [] if kind=='account' else ['lead_assigned_agent','owner_upgrade_customer','owner_assigned_agent'] if upgraded else ['lead_assigned_agent']
        require(sorted(job['kind'] for job in notices)==sorted(kinds), 'independent_notice_kind_set_required')
        for job in notices:
            recipient = upgraded if job['kind']=='owner_upgrade_customer' else agent
            require(job['recipient_user_id']==recipient and job['payload_sha256']==sha256(canonical(job['payload']).encode()).hexdigest()
                and job['payload']==expected_payload(facts,kind,source,agent,job['kind'])
                and job['created_at']==row['created_at'], 'notice_recipient_template_digest_event_time_required')
            if row['request_id'] not in facts['seed_requests']:
                require(job['status']=='pending' and job['attempt_count']==0 and not job['outcome_uncertain']
                    and all(job[key] is None for key in ('attempted_at','accepted_at','lease_token','lease_until','provider_id','error_code')),
                    'ui_seven_pending_notices_no_handoff_required')
    return events,jobs


def run():
    global STAGE,GUARD,REPORTED
    require(len(sys.argv)==2 and sys.argv[1] in ('en','es'), 'explicit_locale_required')
    locale = sys.argv[1]
    logging.disable(logging.CRITICAL)
    old,native,base = helpers()
    old.inventory()
    application = base.verified_checkout()
    database,url = claim(locale)
    base.configure(application,url)
    catalog,retained = base.frozen_helpers()
    start = catalog.query(database,'SELECT pg_postmaster_start_time()')
    patches,owned = old.provider_patches(),[]
    observed = {'logins':Counter(),'commands':[],'controls':[],'boundaries':Counter(),'pages':Counter(),'safe_selects':0,'refused':0,
        'profile_commands':[],'workflow_controls':[],'workflow_control_times':{},'workflow_boundaries':Counter(),
        'workflow_directory_queries':Counter(),'workflow_denial_routes':set(),'workflow_public_reads':[],
        'workflow_public_read_control_counts':[]}
    try:
        for item in patches:
            item.start()
        captured = StringIO()
        with redirect_stdout(captured),redirect_stderr(captured):
            tables,tables106,accounts,identities,facts = seed(old,native,base,catalog,retained,database,url,locale)
        require(len(captured.getvalue().encode()) <= 2*1024*1024, 'bounded_seed_output_required')
        baseline = catalog.snapshot(database,tables)
        schema,secure = catalog.catalog(database,tables),old.security(catalog,database)
        seed_rows = ([row[0] for row in rows(catalog,database,'assignment_events')],
                     [row[0] for row in rows(catalog,database,'assignment_email_jobs')])
        require(len(seed_rows[0])==11 and len(seed_rows[1])==11 and Counter(row['status'] for row in seed_rows[1])
            ==Counter({'accepted':1,'failed':1,'needs_review':1,'pending':8}), 'real_prebaseline_status_mix_required')
        for event_row in seed_rows[0]:
            index=facts['seed_requests'].index(event_row['request_id'])
            want=('accepted','failed','needs_review')[index] if index<3 else 'pending'
            notice=next(row for row in seed_rows[1] if row['event_id']==event_row['id'])
            require(notice['status']==want and notice['attempt_count']==int(index<3)
                and notice['outcome_uncertain'] is (index==2)
                and (notice['accepted_at'] is not None) is (index==0), 'exact_real_mock_outcome_request_binding_required')
        facts['seed_assignment_items'] = assignment_metadata(catalog,database, facts['leads']['main']['id'])
        from database import engine
        from sqlalchemy import event
        from main import app
        import uvicorn
        def select_guard(_connection,_cursor,statement,_parameters,_context,_many):
            if INSIDE.get() and statement.lstrip().lower().startswith('select') and re.search(r'\b(?:assignment_email_jobs|lead_delivery_jobs)\b',statement.lower()):
                require(not re.search(r'\b(?:payload|payload_sha256|recipient_user_id|provider_id)\b',statement.lower()), 'history_private_payload_select_forbidden')
                observed['safe_selects'] += 1
        event.listen(engine,'before_cursor_execute',select_guard)
        base.write_owned(FACTS,(canonical({'scope':'customer-workflows','accounts':accounts,'facts':facts})+'\n').encode(),owned)
        server = uvicorn.Server(uvicorn.Config(confined(app,old,base,catalog,database,accounts,identities,facts,observed,tables),
            host='127.0.0.1',port=8918,lifespan='off',access_log=False,log_level='warning'))
        previous = {}
        def shutdown(_number,_frame):
            server.should_exit = True
        try:
            for number in (signal.SIGTERM,signal.SIGINT):
                previous[number] = signal.signal(number,shutdown)
            STAGE = 'browser'
            server.run()
            require(server.started,'actual_api_server_required')
        finally:
            event.remove(engine,'before_cursor_execute',select_guard)
            for number,handler in previous.items():
                signal.signal(number,handler)
        STAGE = 'final_preservation'
        require(not GUARD and not observed['refused'] and not old.ATTEMPTS, 'no_guard_or_provider_attempt_required')
        require(observed['logins']==Counter({actor:1 for actor in ROLES}) and len(observed['commands'])==9
            and observed['controls']==['agent_unverified','agent_verified'], 'exact_login_command_control_ledger_required')
        verify_events(catalog,database,facts,observed['commands'],seed_rows)
        verify_preserved(catalog,database,facts,observed,baseline,schema,secure,old,tables)
        verify_workflows(catalog,database,facts,observed,baseline)
        require(catalog.query(database,'SELECT version_num FROM alembic_version')==[(HEAD,)]
            and catalog.query(database,'SELECT pg_postmaster_start_time()')==start, 'current_head_postmaster_unchanged_required')
        native.privacy(catalog,database)
        for table in NEW_TABLES:
            require(catalog.query(database,"SELECT relrowsecurity FROM pg_class WHERE oid=('public.'||%s)::regclass",(table,))==[(True,)]
                and catalog.query(database,"SELECT count(*) FROM pg_policies WHERE schemaname='public' AND tablename=%s",(table,))==[(0,)],
                'new_assignment_private_rls_required')
            for role in ('anon','authenticated','service_role'):
                require(catalog.query(database,"SELECT has_table_privilege(%s,'public.'||%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')",(role,table))==[(False,)],
                    'new_assignment_private_role_acl_required')
        require(all(catalog.query(database,'SELECT count(*) FROM '+name)==[(0,)] for name in native.INBOX), 'all7_inbox_empty_required')
        require(observed['safe_selects']>0, 'actual_payload_free_history_select_required')
        base.verified_checkout()
    except BaseException as error:
        # Record the real source-owned failure before cleanup can raise its own.
        diagnostic('CUSTOMER_WORKFLOWS_FIXTURE_FAILURE',error)
        REPORTED=True
        raise
    finally:
        for item in reversed(patches):
            item.stop()
        for path,device,inode in reversed(owned):
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode) and not path.is_symlink() and (info.st_dev,info.st_ino)==(device,inode), 'owned_facts_inode_required')
            path.unlink()
        require(not FACTS.exists() and not FACTS.is_symlink(), 'private_facts_absence_required')
    require(len(owned)==1, 'exact_one_private_facts_removed_required')
    print('CUSTOMER_WORKFLOWS_FIXTURE_RESULT '+canonical({'status':'PASS','locale':locale,'catalog_tables':108,'model_tables':107,
        'genuine99_to106_to108':True,'all_unrelated106_rows_unchanged':True,'all_catalog_sequences_acl_roles_unchanged':True,
        'current_head_unchanged':True,'server_start_unchanged':True,'private_rls_acl':True,'protected_owner_contacts':82,
        'private_profiles':46,'original_accepted_jobs':9,'signup_jobs_unchanged':11,'cms_version':7,'floorplans':2,
        'real_logins':4,'assignment_requests':9,'new_events':7,'new_notices':7,'final_events':18,'final_notices':18,
        'exact_replays':1,'agent_refusals':1,'account_events':2,'account_notices':0,'valuation_notices':1,'seller_notices':3,
        'owner_promotions':1,'assignment_mirrors':1,'activity_additions':2,'controls':2,'controls_restored':True,
        'boundaries':16,'seed_mock_calls':3,'ui_provider_dispatch':False,'external_provider_attempts':0,
        'unexpected_mutations':0,'inbox_writes':0,'private_files_removed':1,
        'workflow_profile_requests':10,'workflow_profile_saves':6,'workflow_legacy_omissions':2,
        'workflow_stale_refusals':2,'workflow_auth_refusals':2,'workflow_control_saves':2,
        'workflow_new_revisions':8,'workflow_controls':14,'workflow_boundaries':12,
        'workflow_old46_profiles_revisions_unchanged':True,'workflow_published_snapshots_unchanged':True,
        'workflow_directory_cases':6,'workflow_list_map_snapshots_exact':True,'workflow_detected_denial_routes':4,
        'workflow_manual_mutations':0,'workflow_controls_restored':True,
        'workflow_public_reads':3,'workflow_withdrawn_refusal_complete':True}),flush=True)


# The functions below keep all HTTP facts independent of the UI receipt. The
# response is forwarded unchanged; successful history rows are independently
# rebuilt from the bounded stored metadata columns and the known command ledger.
def assignment_metadata(catalog,database,target,kind='lead'):
    column = 'lead_id' if kind=='lead' else 'user_id'
    result = []
    for identity,action,created in catalog.query(database,'SELECT id,action,created_at FROM assignment_events WHERE '+column+'=%s ORDER BY created_at DESC,id DESC',(target,)):
        notices = catalog.query(database,"""SELECT kind,status,attempt_count,created_at,attempted_at,accepted_at,outcome_uncertain
            FROM assignment_email_jobs WHERE event_id=%s ORDER BY CASE kind WHEN 'lead_assigned_agent' THEN 0 WHEN 'owner_upgrade_customer' THEN 1 ELSE 2 END""",(identity,))
        result.append({'id':str(identity),'action':action,'created_at':timestamp(created),'notices':[
            dict(zip(('kind','status','attempts','created_at','attempted_at','accepted_at','outcome_uncertain'),
                (k,s,a,timestamp(c),timestamp(t),timestamp(p),u))) for k,s,a,c,t,p,u in notices]})
    return result


def diagnostic(prefix,error,phase=None):
    name=type(error).__name__
    code=getattr(getattr(error,'orig',error),'pgcode',None)
    print(prefix+' '+canonical({'stage':phase or STAGE,'error_type':name if name in ERROR_TYPES else 'UnhandledError',
        'reason':str(error) if isinstance(error,Failure) and re.fullmatch('[a-z_0-9]{1,100}',str(error)) else 'unexpected_failure',
        'sqlstate':code if isinstance(code,str) and re.fullmatch('[A-Z0-9]{5}',code) else 'unavailable'}),flush=True)


def verify_preserved(catalog,database,facts,observed,baseline,schema,secure,old,tables):
    current = catalog.snapshot(database,tables)
    # These two additional tables have a separate exact row/revision ledger;
    # they are never broadly exempted from preservation in verify_workflows.
    changed = {'users','leads','activity_log','professional_profiles','professional_profile_revisions',*NEW_TABLES}
    require(all(current[key]==value for key,value in baseline.items() if key not in changed)
        and catalog.catalog(database,tables)==schema and old.security(catalog,database)==secure, 'all_other_rows_sequences_catalog_security_preserved_required')
    user_before = {row['id']:row for row in json.loads(baseline['users'])}
    user_after = {row['id']:row for row in json.loads(current['users'])}
    expected = json.loads(canonical(user_before))
    expected[facts['ids']['buyer']]['role']='owner'
    expected[facts['ids']['buyer']]['assigned_realtor_id']=facts['ids']['realtor']
    require(user_after==expected, 'exact_one_owner_promotion_mirror_all_users_preserved_required')
    lead_before = {row['id']:row for row in json.loads(baseline['leads'])}
    lead_after = {row['id']:row for row in json.loads(current['leads'])}
    require(lead_before.keys()==lead_after.keys(), 'no_lead_insertion_deletion_required')
    mutable = {facts['leads'][key]['id'] for key in ('main','valuation','seller')}
    for identity,before in lead_before.items():
        after=lead_after[identity]
        if identity not in mutable:
            require(after==before, 'unrelated_lead_unchanged_required')
        else:
            require(after['status']=='assigned' and after['assigned_realtor_id']==facts['ids']['realtor']
                and all(after[key]==value for key,value in before.items() if key not in ('status','assigned_realtor_id','assigned_at')),
                'exact_assignment_lead_columns_required')
            if before['assigned_at'] is not None:
                require(after['assigned_at']==before['assigned_at'], 'existing_assignment_timestamp_preserved_required')
            else:
                require(isinstance(after['assigned_at'],str) and any(command['target']==identity and command['start']<=datetime.fromisoformat(after['assigned_at']).timestamp()<=command['end']
                    for command in observed['commands']), 'actual_assignment_time_within_command_required')
    before_activity=json.loads(baseline['activity_log'])
    after_activity=json.loads(current['activity_log'])
    additions=[row for row in after_activity if row not in before_activity]
    require(all(row in after_activity for row in before_activity) and len(additions)==2, 'exact_two_account_activity_rows_required')
    for index,event_type in ((5,'realtor_assigned'),(6,'realtor_unassigned')):
        command=observed['commands'][index]
        matches=[row for row in additions if row['event_type']==event_type]
        name=facts['names']['owner']
        description=('Realtor '+facts['names']['realtor']+' assigned to account '+name) if index==5 else 'Realtor unassigned from account '+name
        require(len(matches)==1 and matches[0]['actor_id']==facts['ids']['admin'] and matches[0]['description']==description
            and command['start']<=datetime.fromisoformat(matches[0]['created_at']).timestamp()<=command['end'], 'independent_account_activity_fields_required')
    require(observed['boundaries']==Counter({key:1 for key in boundary_specs(facts)}), 'exact_sixteen_boundaries_required')
    require(observed['pages'][('main',10,0)]>=1 and observed['pages'][('main',10,10)]>=1
        and observed['pages'][('signup',10,0)]>=1 and observed['pages'][('signup',10,10)]>=1, 'actual_assignment_signup10_1_required')


def boundary_specs(facts):
    result={}
    for scope,target in (('lead',facts['leads']['main']['id']),('account',facts['ids']['owner'])):
        for actor,status in (('admin',200),(None,401),('buyer',403),('owner',403),('realtor',403)):
            result[scope+'_'+(actor or 'anonymous')]=(actor,scope,target,'',status)
        result[scope+'_unknown']=('admin',scope,facts['unknown_id'],'',404)
        result[scope+'_invalid']=('admin',scope,facts['invalid_id'],'',422)
        result[scope+'_bounds']=('admin',scope,target,'limit=51' if scope=='lead' else 'offset=10001',422)
    return result


def confined(app,old,base,catalog,database,accounts,identities,facts,observed,tables):
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from utils.auth import _decode_token
    from database import SessionLocal
    from models.user import User
    actors={str(value):key for key,value in identities.items()}
    login_bodies={canonical(value):key for key,value in accounts.items()}
    specs=event_specs(facts)
    boundaries=boundary_specs(facts)
    lead_ids={item['id']:key for key,item in facts['leads'].items()}
    async def handle(scope,receive,send):
        global GUARD
        if scope['type']!='http':
            return await app(scope,receive,send)
        request=Request(scope,receive)
        actor=actors.get(_decode_token(request.cookies['ildr_token'])) if request.cookies.get('ildr_token') else None
        method,path=scope['method'],scope['path']
        phase='read' if method in ('GET','HEAD','OPTIONS') else 'mutation'
        try:
            if await workflow_request(app,scope,receive,send,request,actor,base,catalog,database,facts,observed,tables):
                return
            if method in ('GET','HEAD','OPTIONS'):
                match=re.fullmatch('/admin/(leads|users)/([^/]+)/(assignment-history|communications)',path)
                if match:
                    kind='lead' if match[1]=='leads' else 'account'
                    target=match[2]
                    signup=match[3]=='communications'
                    require(target in set(actors)|set(lead_ids)|{facts['unknown_id'],facts['invalid_id']}, 'owned_history_target_required')
                    label=request.headers.get('x-ci-boundary')
                    expected=401 if actor is None else 403 if actor!='admin' else 422 if target==facts['invalid_id'] else 404 if target==facts['unknown_id'] else 200
                    if label:
                        require(label in boundaries and boundaries[label][:3]==(actor,kind,target)
                            and scope['query_string'].decode()==boundaries[label][3]
                            and not request.headers.get('authorization') and (actor is not None or not request.headers.get('cookie')),
                            'exact_boundary_actor_target_query_required')
                        expected=boundaries[label][4]
                    pairs=list(request.query_params.multi_items())
                    require(len(pairs)==len(dict(pairs)) and set(dict(pairs))<={'limit','offset'}, 'finite_history_query_required')
                    limit=int(request.query_params.get('limit','25' if signup else '10'))
                    offset=int(request.query_params.get('offset','0'))
                    require((1<=limit<=50 and 0<=offset<=10000) or label and expected==422, 'bounded_history_paging_required')
                    chunks=[];starts=[]
                    async def capture(message):
                        if message['type']=='http.response.start':
                            require(message['status']==expected and dict(message['headers']).get(b'cache-control')==b'no-store', 'actual_history_status_no_store_required')
                            starts.append(message['status'])
                        elif message['type']=='http.response.body':
                            chunks.append(message.get('body',b''))
                            require(sum(map(len,chunks))<=131072, 'bounded_metadata_response_required')
                        await send(message)
                    marker=INSIDE.set(True)
                    try:
                        await app(scope,receive,capture)
                    finally:
                        INSIDE.reset(marker)
                    require(starts==[expected], 'one_actual_history_response_required')
                    body=json.loads(b''.join(chunks))
                    require(not any(marker in canonical(body) for marker in facts['private_markers']), 'no_private_payload_projection_required')
                    if expected==200:
                        items=(facts['signup_items'] if target==facts['ids']['buyer'] else []) if signup else assignment_metadata(catalog,database,target,kind)
                        want={'total':len(items),'limit':limit,'offset':offset,'tracked':bool(items),'items':items[offset:offset+limit]}
                        require(old.normalized(body)==old.normalized(want), 'exact_current_metadata_order_projection_required')
                        key='signup' if signup and target==facts['ids']['buyer'] else lead_ids.get(target,target)
                        observed['pages'][(key,limit,offset)]+=1
                    if label:
                        observed['boundaries'][label]+=1
                    return
                return await app(scope,receive,send)
            raw=await request.body()
            if method=='POST' and path=='/auth/login':
                key=login_bodies.get(canonical(json.loads(raw)))
                require(len(raw)<=8192 and key in ROLES and observed['logins'][key]==0, 'exact_four_login_bodies_required')
                async def login_send(message):
                    if message['type']=='http.response.start':
                        require(message['status']==200, 'real_login_no_limiter_bypass_required')
                        observed['logins'][key]+=1
                    await send(message)
                return await app(scope,base.replay_body(raw,receive),login_send)
            if method=='POST' and path=='/__ci/control':
                phase='control'
                require(actor=='admin' and request.headers.get('x-ci-control')==facts['control_secret'] and len(raw)<=256,
                        'owned_authenticated_control_required')
                value=json.loads(raw)
                order=('agent_unverified','agent_verified')
                require(set(value)=={'action'} and len(observed['controls'])<2 and value['action']==order[len(observed['controls'])],
                        'exact_control_order_required')
                require(len(observed['commands'])==(4 if value['action']=='agent_unverified' else 5), 'control_command_binding_required')
                with SessionLocal.begin() as db:
                    user=db.get(User,identities['realtor'])
                    require(user.email_verified is (value['action']=='agent_unverified'), 'control_immediate_prestate_required')
                    user.email_verified=value['action']=='agent_verified'
                observed['controls'].append(value['action'])
                return await JSONResponse({'status':'PASS'})(scope,base.replay_body(raw,receive),send)
            match=re.fullmatch('/admin/(leads|users)/([a-f0-9-]{36})/(assign|assign-realtor)',path)
            if method=='PUT' and match:
                index=len(observed['commands'])
                require(index<len(specs) and actor=='admin' and len(raw)<=256, 'bounded_owned_assignment_required')
                kind,target,agent,status=specs[index]
                body=json.loads(raw)
                require((match[1],match[2],match[3])==('leads' if kind=='lead' else 'users',target,'assign' if kind=='lead' else 'assign-realtor')
                    and set(body)=={'realtor_id','request_id'} and body['realtor_id']==agent
                    and str(UUID(body['request_id']))==body['request_id'], 'exact_intent_body_target_agent_required')
                require((index==2 and body==observed['commands'][0]['body']) or (index!=2 and body['request_id'] not in
                    facts['seed_requests']+[row['body']['request_id'] for row in observed['commands']]), 'exact_replay_or_fresh_request_required')
                before=catalog.snapshot(database,tables)
                command={'kind':kind,'target':target,'body':body,'status':status,'start':datetime.now(timezone.utc).timestamp()}
                starts=[]
                async def assignment_send(message):
                    if message['type']=='http.response.start':
                        require(message['status']==status, 'actual_assignment_status_required')
                        starts.append(message['status'])
                    await send(message)
                await app(scope,base.replay_body(raw,receive),assignment_send)
                command['end']=datetime.now(timezone.utc).timestamp()
                require(starts==[status], 'one_real_assignment_response_required')
                after=catalog.snapshot(database,tables)
                check_command_delta(before,after,index,command,facts)
                observed['commands'].append(command)
                return
            observed['refused']+=1
            return await JSONResponse({'detail':'owned_scope_refused'},status_code=405)(scope,base.replay_body(raw,receive),send)
        except Exception as error:
            if not GUARD:
                GUARD=True
                diagnostic('CUSTOMER_WORKFLOWS_FIXTURE_GUARD',error,phase)
            raise
    return handle


def check_command_delta(before,after,index,command,facts):
    if index in (2,4):
        require(after==before,'replay_refusal_no_database_change_required')
        return
    allowed={'assignment_events','assignment_email_jobs'}
    if index in (1,3,7,8):
        allowed.add('leads')
    if index in (5,6,8):
        allowed.add('users')
    if index in (5,6):
        allowed.add('activity_log')
    require(all(after[key]==value for key,value in before.items() if key not in allowed), 'per_command_unrelated108_rows_sequences_preserved_required')
    for table,count in (('assignment_events',1),('assignment_email_jobs',0 if index in (5,6) else 3 if index==8 else 1)):
        previous,current=map(json.loads,(before[table],after[table]))
        require(all(row in current for row in previous) and len(current)==len(previous)+count, 'per_command_exact_append_only_rows_required')
    if 'users' in allowed:
        previous={row['id']:row for row in json.loads(before['users'])}
        current={row['id']:row for row in json.loads(after['users'])}
        expected=json.loads(canonical(previous))
        actor='buyer' if index==8 else 'owner'
        expected[facts['ids'][actor]]['assigned_realtor_id']=None if index==6 else facts['ids']['realtor']
        if index==8:
            expected[facts['ids']['buyer']]['role']='owner'
        require(current==expected, 'per_command_exact_user_assignment_role_change_required')
    if 'leads' in allowed:
        previous={row['id']:row for row in json.loads(before['leads'])}
        current={row['id']:row for row in json.loads(after['leads'])}
        expected=json.loads(canonical(previous))
        target=expected[command['target']]
        target['assigned_realtor_id']=command['body']['realtor_id']
        target['status']='assigned'
        if target['assigned_at'] is None:
            actual=current[command['target']]['assigned_at']
            require(isinstance(actual,str) and command['start']<=datetime.fromisoformat(actual).timestamp()<=command['end'],
                    'per_command_assigned_time_bound_required')
            target['assigned_at']=actual
        require(current==expected, 'per_command_exact_lead_assignment_columns_required')
    events_before=json.loads(before['assignment_events'])
    new_event=next(row for row in json.loads(after['assignment_events']) if row not in events_before)
    require(new_event['request_id']==command['body']['request_id'] and command['start']<=datetime.fromisoformat(new_event['created_at']).timestamp()<=command['end'],
            'per_command_request_event_time_binding_required')


def workflow_profile_spec(facts, index):
    require(0<=index<10, 'workflow_profile_request_count_required')
    key='owned' if index<5 else 'managed'
    number=index%5
    item=facts['workflows']['profiles'][key]
    draft=json.loads(canonical(item['selected_draft']))
    if number>=2:
        for field in FACETS: draft[field]=[]
    if number==1:
        for field in FACETS: draft.pop(field)
    if number in (3,4):
        draft['business_name']=item['draft']['business_name']+(' stale' if number==3 else ' refused')
    return key,item,number,{'expected_version':(1,2,3,4,5)[number],'draft':draft},(200,200,200,409,403)[number]


def workflow_profile_delta(before, after, key, draft, facts, start, end):
    """One exact real save: known row+one source revision, all other108 state exact."""
    changed={'professional_profiles','professional_profile_revisions'}
    require(all(after[name]==value for name,value in before.items() if name not in changed),
            'workflow_save_unrelated108_rows_sequences_exact_required')
    prior={row['id']:row for row in json.loads(before['professional_profiles'])}
    current={row['id']:row for row in json.loads(after['professional_profiles'])}
    identity=facts['workflows']['profiles'][key]['id']
    require(prior.keys()==current.keys(), 'workflow_no_profile_insertion_deletion_required')
    expected=json.loads(canonical(prior));row=expected[identity]
    normalized=json.loads(canonical(draft))
    for field in FACETS:
        if field not in normalized: normalized[field]=row['draft_data'].get(field,[])
    row.update(version=row['version']+1,draft_data=normalized,review_status='draft',submitted_at=None,review_note=None)
    if not normalized['public_contact_consent'] and row['listing_status']!='suspended':row['listing_status']='hidden'
    actual=current[identity]
    require(start<=datetime.fromisoformat(actual['updated_at']).timestamp()<=end,
            'workflow_save_timestamp_command_binding_required')
    row['updated_at']=actual['updated_at']
    require(current==expected, 'workflow_exact_profile_fields_required')
    oldrows,newrows=map(json.loads,(before['professional_profile_revisions'],after['professional_profile_revisions']))
    additions=[value for value in newrows if value not in oldrows]
    require(len(additions)==1 and len(newrows)==len(oldrows)+1 and all(value in newrows for value in oldrows),
            'workflow_one_revision_append_only_required')
    revision=additions[0]
    expected_snapshot={'draft':row['draft_data'],'published':row['published_data'],
                       'review_status':row['review_status'],'listing_status':row['listing_status']}
    require(set(revision)=={'id','profile_id','version','action','snapshot','note','actor_id','created_at'}
        and str(UUID(revision['id']))==revision['id'] and revision['profile_id']==identity
        and revision['version']==row['version'] and revision['action']==('save' if key=='owned' else 'admin_save') and revision['note'] is None
        and revision['actor_id']==facts['ids']['owner' if key=='owned' else 'admin']
        and revision['snapshot']==expected_snapshot
        and start<=datetime.fromisoformat(revision['created_at']).timestamp()<=end,
        'workflow_exact_revision_actor_version_snapshot_required')


def workflow_boundary_specs(facts):
    profiles=facts['workflows']['profiles'];manual=facts['workflows']['manual']
    return {
        'directory_public_approved':('owner','/professionals/'+profiles['published_a']['id'],200),
        'directory_private_hidden':('owner','/professionals/'+profiles['managed']['id'],404),
        'directory_admin_private':('admin','/admin/professionals/'+profiles['managed']['id'],200),
        'directory_buyer_admin403':('buyer','/admin/professionals/'+profiles['managed']['id'],403),
        'directory_realtor_admin403':('realtor','/admin/professionals/'+profiles['managed']['id'],403),
        'directory_anonymous401':(None,'/professionals',401),
        'manual_buyer_admin403':('buyer','/admin/leads',403),
        'manual_owner_realtor403':('owner','/realtor/leads',403),
        'manual_anonymous401':(None,'/admin/leads',401),
        'manual_admin_valid200':('admin','/admin/leads',200),
        'manual_assignee_valid200':('realtor','/realtor/leads',200),
        'manual_public_link200':(None,'/listings/'+manual['valid']['property_id'],200)}


def workflow_directory_matches(facts, parameters):
    """Independent finite published-snapshot oracle; no private draft or API rows."""
    supported={'provider_kind','property_class','work_scope','category','area','language','q','location_status','map_area','limit','offset'}
    require(set(parameters)<=supported, 'workflow_finite_directory_query_required')
    filters={key:value for key,value in parameters.items() if key not in ('limit','offset')
        and value not in ([],[''],['all'])}
    expected=[]
    for item in facts['workflows']['profiles'].values():
        published=item['published']
        if published is None:continue
        keep=True
        for query,field in (('provider_kind','provider_kind_ids'),('property_class','property_class_ids'),
                            ('work_scope','work_scope_ids'),('category','category_ids'),('area','service_area_ids')):
            if filters.get(query):keep=keep and bool(set(filters[query])&set(published[field]))
        if filters.get('language'):keep=keep and filters['language']==[filters['language'][0]] and filters['language'][0] in published['languages']
        if filters.get('q'):
            require(len(filters['q'])==1, 'workflow_single_directory_search_required')
            needle=filters['q'][0].strip().casefold()
            keep=keep and any(needle in value.casefold() for value in [published['business_name'],published['contact_name'],*published['descriptions'].values()])
        # UI map references here represent declared service areas, never inferred office/GPS.
        if filters.get('map_area'):keep=keep and filters['map_area'][0] in ['DO:'+area for area in published['service_area_ids']]
        require(filters.get('location_status', ['all'])[0] in ('all','mapped','unmapped'), 'workflow_known_map_mode_required')
        if filters.get('location_status')==['unmapped']:keep=False  # all four declared seed areas have existing references
        if keep:expected.append(item)
    return filters,sorted(expected,key=lambda item:(item['published']['business_name'].lower(),item['id']))


async def workflow_directory_read(app,scope,receive,send,request,actor,catalog,database,facts,observed):
    path=scope['path']
    if scope['method']!='GET' or path not in ('/professionals','/professionals/map','/professional-profile','/admin/professionals'):
        return False
    from models.user import User
    from database import SessionLocal
    if actor is None:expected_status=401
    else:
        with SessionLocal() as db:
            user=db.get(User,UUID(facts['ids'][actor]));verified=user.status=='active' and user.email_verified
        expected_status=403 if not verified or path=='/admin/professionals' and actor!='admin' else 200
    starts=[];chunks=[]
    async def capture(message):
        if message['type']=='http.response.start':
            require(message['status']==expected_status and dict(message['headers']).get(b'cache-control')==b'no-store',
                    'workflow_current_directory_read_status_required')
            starts.append(message['status'])
        elif message['type']=='http.response.body':
            chunks.append(message.get('body',b''));require(sum(map(len,chunks))<=262144, 'workflow_bounded_response_required')
        await send(message)
    await app(scope,receive,capture)
    require(starts==[expected_status], 'workflow_single_directory_read_response_required')
    if expected_status==403:
        names={'/professional-profile':'own_private','/professionals':'member_directory','/admin/professionals':'admin_directory'}
        if path in names:observed['workflow_denial_routes'].add(names[path])
        return True
    if expected_status!=200 or path not in ('/professionals','/professionals/map'):return True
    parameters={}
    for key,value in request.query_params.multi_items():parameters.setdefault(key,[]).append(value)
    filters,items=workflow_directory_matches(facts,parameters);body=json.loads(b''.join(chunks))
    require(body['total']==len(items), 'workflow_independent_directory_total_required')
    if path=='/professionals':
        limit=int(request.query_params.get('limit','24'));offset=int(request.query_params.get('offset','0'))
        require(body['limit']==limit and body['offset']==offset and [row['id'] for row in body['items']]
            ==[item['id'] for item in items[offset:offset+limit]], 'workflow_independent_list_order_scope_required')
        for row,item in zip(body['items'],items[offset:offset+limit]):
            for field in ('business_name','contact_name',*FACETS,'category_ids','service_area_ids','languages','descriptions'):
                require(row[field]==item['published'][field], 'workflow_independent_public_fields_required')
            require('draft' not in row and 'review_note' not in row, 'workflow_no_private_draft_projection_required')
    else:
        counts=Counter('DO:'+area for item in items for area in item['published']['service_area_ids'])
        require(body['complete'] is True and body['mapped_total']==len(items) and body['unmapped_total']==0
            and {area['area_key']:area['record_count'] for area in body['areas']}==dict(counts)
            and all(area['purpose']=='service_area' and area['precision']=='area_reference' for area in body['areas']),
            'workflow_independent_map_reference_counts_required')
    require(not any(marker in canonical(body) for marker in ('PRIVATE_WORKFLOW_REVIEW_NOTE','Synthetic '+facts['locale']+' private')),
            'workflow_private_draft_never_public_required')
    observed['workflow_directory_queries'][(path,canonical(filters))]+=1
    return True


async def workflow_request(app, scope, receive, send, request, actor, base, catalog, database, facts, observed, tables):
    from database import SessionLocal
    from models.user import User
    from models.lead import Lead
    from models.listing import Listing
    from models.professional_profile import ProfessionalProfile
    from schemas.professionals import SaveProfessionalBody
    from routes.professionals import save_profile,save_managed_profile
    from starlette.responses import JSONResponse
    method,path=scope['method'],scope['path']
    profiles=facts['workflows']['profiles']
    if method=='GET' and request.headers.get('x-ci-workflow-boundary'):
        label=request.headers['x-ci-workflow-boundary'];specs=workflow_boundary_specs(facts)
        require(not observed['commands'] and not observed['profile_commands'] and not observed['workflow_controls'],
                'workflow_boundaries_before_role_promotion_required')
        if actor is not None:
            with SessionLocal() as db:
                user=db.get(User,UUID(facts['ids'][actor]))
                require(user.role==ROLES[actor] and user.status=='active' and user.email_verified,
                        'workflow_boundary_actual_initial_role_required')
        require(label in specs and (actor,path)==specs[label][:2] and not scope['query_string']
            and not request.headers.get('authorization') and (actor is not None or not request.headers.get('cookie')),
            'workflow_exact_boundary_actor_path_required')
        starts=[];chunks=[]
        async def capture(message):
            if message['type']=='http.response.start':
                require(message['status']==specs[label][2], 'workflow_actual_boundary_status_required')
                if path.startswith('/professionals') or path.startswith('/admin/professionals') or path.startswith('/listings/'):
                    require(dict(message['headers']).get(b'cache-control')==b'no-store', 'workflow_private_public_no_store_required')
                starts.append(message['status'])
            elif message['type']=='http.response.body':
                chunks.append(message.get('body',b''));require(sum(map(len,chunks))<=262144, 'workflow_bounded_response_required')
            await send(message)
        await app(scope,receive,capture)
        require(starts==[specs[label][2]], 'workflow_single_boundary_response_required')
        if label=='directory_public_approved':
            value=json.loads(b''.join(chunks));expected=profiles['published_a']['published']
            for field in ('business_name',*FACETS,'descriptions'):
                require(value[field]==expected[field], 'workflow_approved_snapshot_projection_required')
            require('draft' not in value and 'review_note' not in value, 'workflow_no_private_draft_projection_required')
        observed['workflow_boundaries'][label]+=1
        return True
    # Only the three real anonymous UI listing reads; tagged boundary reads above
    # keep their existing independent ledger. Forward the SAME ASGI messages.
    if method=='GET' and path=='/listings/'+facts['workflows']['manual']['valid']['property_id']:
        expected=(200,200,404)
        ordinal=len(observed['workflow_public_reads'])
        require(ordinal<len(expected) and actor is None and not scope['query_string']
            and 'cookie' not in request.headers and 'authorization' not in request.headers
            and 'sec-purpose' not in request.headers, 'workflow_exact_anonymous_public_read_required')
        withdrawn='manual_property_withdrawn' in observed['workflow_controls']
        restored='manual_property_restore' in observed['workflow_controls']
        chronology=(ordinal==2)==withdrawn and not restored
        if not chronology:
            print('CUSTOMER_WORKFLOWS_FIXTURE_PUBLIC_CHRONOLOGY '+canonical({
                'ordinal':ordinal,'prior_slot_statuses':[item['status'] for item in observed['workflow_public_reads']],
                'completed_count':sum(item['complete'] is True for item in observed['workflow_public_reads']),
                'withdrawn':withdrawn,'restored':restored,'control_count':len(observed['workflow_controls']),
                'prior_read_control_counts':list(observed['workflow_public_read_control_counts']),
                'tagged_boundary':bool(request.headers.get('x-ci-workflow-boundary'))}),flush=True)
        require(chronology, 'workflow_public_withdrawal_chronology_required')
        receipt={'status':expected[ordinal],'complete':False}
        observed['workflow_public_reads'].append(receipt)
        observed['workflow_public_read_control_counts'].append(len(observed['workflow_controls']))
        starts=[];final=False;refusal=bytearray();declared_length=None
        async def public_send(message):
            nonlocal final,declared_length
            if message['type']=='http.response.start':
                headers=dict(message['headers'])
                require(not starts and message['status']==expected[ordinal]
                    and headers.get(b'cache-control')==b'no-store', 'workflow_actual_public_status_no_store_required')
                starts.append(message['status'])
                if ordinal==2:declared_length=headers.get(b'content-length')
            elif message['type']=='http.response.body':
                require(starts==[expected[ordinal]] and not final, 'workflow_public_response_frame_order_required')
                # Never store successful listing bodies, including public contacts.
                if ordinal==2:
                    require(len(refusal)+len(message.get('body',b''))<=2048, 'workflow_bounded_public_refusal_required')
                    refusal.extend(message.get('body',b''))
                final=message.get('more_body',False) is False
            await send(message)
        await app(scope,receive,public_send)
        require(starts==[expected[ordinal]] and final, 'workflow_actual_public_final_frame_required')
        if ordinal==2:
            require(re.fullmatch(rb'[ \t\r\n]*\{[ \t\r\n]*"detail"[ \t\r\n]*:[ \t\r\n]*"Listing not found"[ \t\r\n]*\}[ \t\r\n]*',refusal)
                and declared_length is not None and re.fullmatch(rb'[0-9]{1,4}',declared_length)
                and int(declared_length)==len(refusal), 'workflow_exact_public_refusal_body_length_required')
        receipt['complete']=True
        return True
    if await workflow_directory_read(app,scope,receive,send,request,actor,catalog,database,facts,observed):return True
    managed_path='/admin/professionals/'+profiles['managed']['id']
    if method=='PUT' and path in ('/professional-profile',managed_path):
        raw=await request.body();require(len(raw)<=32768, 'workflow_bounded_profile_body_required')
        index=len(observed['profile_commands']);key,item,number,expected,status=workflow_profile_spec(facts,index)
        require(actor==('owner' if key=='owned' else 'admin') and path==('/professional-profile' if key=='owned' else managed_path)
            and json.loads(raw)==expected, 'workflow_exact_declared_profile_intent_required')
        require((request.headers.get('x-ci-workflow-legacy')==key) is (number==1), 'workflow_explicit_legacy_omission_binding_required')
        before=catalog.snapshot(database,tables);start=datetime.now(timezone.utc).timestamp();starts=[]
        async def mutation_send(message):
            if message['type']=='http.response.start':
                require(message['status']==status and dict(message['headers']).get(b'cache-control')==b'no-store',
                        'workflow_actual_profile_status_no_store_required')
                starts.append(message['status'])
            await send(message)
        await app(scope,base.replay_body(raw,receive),mutation_send)
        end=datetime.now(timezone.utc).timestamp();after=catalog.snapshot(database,tables)
        require(starts==[status], 'workflow_single_profile_response_required')
        if status==200:workflow_profile_delta(before,after,key,expected['draft'],facts,start,end)
        else:require(after==before, 'workflow_refused_profile_no_database_change_required')
        if status==403:observed['workflow_denial_routes'].add('own_private' if key=='owned' else 'managed_private')
        observed['profile_commands'].append({'key':key,'number':number,'status':status,'start':start,'end':end})
        return True
    if method=='POST' and path=='/__ci/workflow-control':
        raw=await request.body();require(actor=='admin' and request.headers.get('x-ci-control')==facts['control_secret']
            and len(raw)<=256, 'workflow_owned_authenticated_control_required')
        value=json.loads(raw);index=len(observed['workflow_controls'])
        require(set(value)=={'action'} and index<len(WORKFLOW_CONTROL_ORDER) and value['action']==WORKFLOW_CONTROL_ORDER[index],
                'workflow_exact_control_order_required')
        action=value['action'];before=catalog.snapshot(database,tables);start=datetime.now(timezone.utc).timestamp()
        required_commands={'own_version_advance':3,'own_unverified':4,'own_verified':5,
            'managed_version_advance':8,'admin_unverified':9,'admin_verified':10}
        require(len(observed['profile_commands'])==required_commands.get(action,10), 'workflow_control_profile_chronology_required')
        if action.endswith('_version_advance'):
            key='owned' if action.startswith('own_') else 'managed';item=profiles[key]
            draft=json.loads(canonical(item['draft']));draft.update({field:[] for field in FACETS})
            body=SaveProfessionalBody(expected_version=4,draft=draft)
            with SessionLocal() as db:
                user=db.get(User,UUID(facts['ids']['owner' if key=='owned' else 'admin']))
                if key=='owned':save_profile(request,body,user,db)
                else:save_managed_profile(request,UUID(item['id']),body,user,db)
            end=datetime.now(timezone.utc).timestamp()
            workflow_profile_delta(before,catalog.snapshot(database,tables),key,draft,facts,start,end)
        else:
            change=None
            if action in ('own_unverified','own_verified','admin_unverified','admin_verified','manual_realtor_unverified','manual_realtor_verified'):
                key='owner' if action.startswith('own_') else 'admin' if action.startswith('admin_') else 'realtor'
                change=('users',facts['ids'][key],'email_verified',action.endswith('_verified'))
            elif action in ('manual_contact_change','manual_contact_restore'):
                item=facts['workflows']['manual']['valid'];change=('leads',item['id'],'name',item['name']+' changed' if action.endswith('_change') else item['name'])
            elif action in ('manual_property_withdrawn','manual_property_restore'):
                change=('listings',facts['workflows']['manual']['valid']['property_id'],'status','archived' if action.endswith('_withdrawn') else 'active')
            elif action in ('manual_assignee_admin','manual_assignee_restore'):
                change=('leads',facts['workflows']['manual']['valid']['id'],'assigned_realtor_id',facts['ids']['admin'] if action.endswith('_admin') else facts['ids']['realtor'])
            require(change is not None, 'workflow_known_control_required')
            table,identity,column,newvalue=change
            previous=json.loads(before[table]);expected=json.loads(canonical(previous))
            target=next(row for row in expected if row['id']==identity)
            expected_old=(not newvalue) if column=='email_verified' else facts['workflows']['manual']['valid']['name'] if action=='manual_contact_change' else facts['workflows']['manual']['valid']['name']+' changed' if action=='manual_contact_restore' else 'active' if action=='manual_property_withdrawn' else 'archived' if action=='manual_property_restore' else facts['ids']['realtor'] if action=='manual_assignee_admin' else facts['ids']['admin']
            require(target[column]==expected_old, 'workflow_control_immediate_prestate_required');target[column]=newvalue
            models={'users':User,'leads':Lead,'listings':Listing}
            with SessionLocal.begin() as db:
                row=db.get(models[table],UUID(identity));setattr(row,column,UUID(newvalue) if column=='assigned_realtor_id' else newvalue)
            after=catalog.snapshot(database,tables)
            require(all(after[name]==text for name,text in before.items() if name!=table)
                and sorted(json.loads(after[table]),key=lambda row:row['id'])==sorted(expected,key=lambda row:row['id']),
                'workflow_exact_control_single_field_delta_required')
            end=datetime.now(timezone.utc).timestamp()
        observed['workflow_controls'].append(action);observed['workflow_control_times'][action]=(start,end)
        await JSONResponse({'status':'PASS'})(scope,base.replay_body(raw,receive),send)
        return True
    return False


def verify_workflows(catalog,database,facts,observed,baseline):
    require(observed['workflow_public_reads']==[{'status':status,'complete':True} for status in (200,200,404)],
            'workflow_three_public_reads_same_response_complete_required')
    require(len(observed['profile_commands'])==10 and observed['workflow_controls']==list(WORKFLOW_CONTROL_ORDER),
            'workflow_exact_command_control_totals_required')
    require(observed['workflow_boundaries']==Counter({name:1 for name in workflow_boundary_specs(facts)}),
            'workflow_exact_twelve_boundaries_required')
    for case in facts['workflows']['directory_cases']:
        normalized={key:value if isinstance(value,list) else [value] for key,value in case['filters'].items()}
        require(all(observed['workflow_directory_queries'][(path,canonical(normalized))]>=1
                    for path in ('/professionals','/professionals/map')), 'workflow_all_six_real_list_map_cases_required')
    # Four genuine denial families: own/managed PUT plus member/Admin directory GET.
    require(observed['workflow_denial_routes']=={'own_private','member_directory','admin_directory','managed_private'},
            'workflow_current_denial_read_routes_required')
    before={row['id']:row for row in json.loads(baseline['professional_profiles'])}
    after={record[0]['id']:record[0] for record in rows(catalog,database,'professional_profiles')}
    require(before.keys()==after.keys() and len(before)==51, 'workflow_exact51_profile_rowset_required')
    for identity,prior in before.items():
        key=next((key for key in ('owned','managed') if facts['workflows']['profiles'][key]['id']==identity),None)
        if key is None:require(after[identity]==prior, 'workflow_all_other49_profiles_exact_required');continue
        expected=json.loads(canonical(prior));draft=json.loads(canonical(facts['workflows']['profiles'][key]['draft']))
        draft.update({field:[] for field in FACETS})
        expected.update(version=5,draft_data=draft,review_status='draft',submitted_at=None,review_note=None)
        if not draft['public_contact_consent']:expected['listing_status']='hidden'
        stamp=after[identity]['updated_at'];start,end=observed['workflow_control_times']['own_version_advance' if key=='owned' else 'managed_version_advance']
        require(start<=datetime.fromisoformat(stamp).timestamp()<=end, 'workflow_final_version_time_required');expected['updated_at']=stamp
        require(after[identity]==expected, 'workflow_final_exact_two_profile_rows_required')
    revisions=[row[0] for row in rows(catalog,database,'professional_profile_revisions')]
    original=json.loads(baseline['professional_profile_revisions']);extra=[row for row in revisions if row not in original]
    require(len(original)==51 and len(extra)==8 and len(revisions)==59 and all(row in revisions for row in original),
            'workflow_all51_seed_revisions_exact_plus8_required')
    for key in ('owned','managed'):
        identity=facts['workflows']['profiles'][key]['id'];items=[row for row in extra if row['profile_id']==identity]
        require(sorted(row['version'] for row in items)==[2,3,4,5], 'workflow_exact_revision_versions_required')
        for row in items:
            draft=json.loads(canonical(facts['workflows']['profiles'][key]['selected_draft'] if row['version'] in (2,3) else facts['workflows']['profiles'][key]['draft']))
            if row['version']>=4:draft.update({field:[] for field in FACETS})
            expected_snapshot={'draft':draft,'published':before[identity]['published_data'],'review_status':'draft',
                'listing_status':before[identity]['listing_status'] if draft['public_contact_consent'] else 'hidden'}
            require(row['snapshot']==expected_snapshot and row['actor_id']==facts['ids']['owner' if key=='owned' else 'admin']
                and row['action']==('save' if key=='owned' else 'admin_save') and row['note'] is None, 'workflow_final_independent_revision_snapshot_required')


if __name__=='__main__':
    try:
        run()
    except BaseException as error:
        if not REPORTED:
            diagnostic('CUSTOMER_WORKFLOWS_FIXTURE_FAILURE',error)
        sys.exit(1)

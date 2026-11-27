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

COMMIT = '4880569cd1efefcc63b129bffa9e31373d5d5960'
TREE = 'e1476f7efa3ea3a270234da23122916aff86751a'
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
    marker = 'ildr-assignment-history-browser-ci:' + nonce + ':' + locale
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
    require(table in ('users','leads','activity_log',*NEW_TABLES), 'row_table_allowlist_required')
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
             'private_markers':['OWNED_ASSIGNMENT_PRIVATE_BODY','owned-private-provider-id','synthetic-never-send@example.com']}
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
            payload = {'marker':'OWNED_ASSIGNMENT_PRIVATE_BODY','to':'synthetic-never-send@example.com','html':'Synthetic private signup body','step':step}
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
                name=name,email='owned-'+key+'@example.com',message='OWNED_ASSIGNMENT_PRIVATE_BODY',
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
    return tables,tables106,accounts,identities,facts


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
            message='OWNED_ASSIGNMENT_PRIVATE_BODY')
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
    observed = {'logins':Counter(),'commands':[],'controls':[],'boundaries':Counter(),'pages':Counter(),'safe_selects':0,'refused':0}
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
        base.write_owned(FACTS,(canonical({'scope':'assignment-history-only','accounts':accounts,'facts':facts})+'\n').encode(),owned)
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
        diagnostic('ASSIGNMENT_FIXTURE_FAILURE',error)
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
    print('ASSIGNMENT_FIXTURE_RESULT '+canonical({'status':'PASS','locale':locale,'catalog_tables':108,'model_tables':107,
        'genuine99_to106_to108':True,'all_unrelated106_rows_unchanged':True,'all_catalog_sequences_acl_roles_unchanged':True,
        'current_head_unchanged':True,'server_start_unchanged':True,'private_rls_acl':True,'protected_owner_contacts':82,
        'private_profiles':46,'original_accepted_jobs':9,'signup_jobs_unchanged':11,'cms_version':7,'floorplans':2,
        'real_logins':4,'assignment_requests':9,'new_events':7,'new_notices':7,'final_events':18,'final_notices':18,
        'exact_replays':1,'agent_refusals':1,'account_events':2,'account_notices':0,'valuation_notices':1,'seller_notices':3,
        'owner_promotions':1,'assignment_mirrors':1,'activity_additions':2,'controls':2,'controls_restored':True,
        'boundaries':16,'seed_mock_calls':3,'ui_provider_dispatch':False,'external_provider_attempts':0,
        'unexpected_mutations':0,'inbox_writes':0,'private_files_removed':1}),flush=True)


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
    changed = {'users','leads','activity_log',*NEW_TABLES}
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
                diagnostic('ASSIGNMENT_FIXTURE_GUARD',error,phase)
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


if __name__=='__main__':
    try:
        run()
    except BaseException as error:
        if not REPORTED:
            diagnostic('ASSIGNMENT_FIXTURE_FAILURE',error)
        sys.exit(1)

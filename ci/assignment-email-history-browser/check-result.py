"""Strict, payload-free receipts. Diagnostics never satisfy acceptance."""
from pathlib import Path
import json
import re
import sys

API = {'status':'PASS','catalog_tables':108,'model_tables':107,'genuine99_to106_to108':True,
 'all_unrelated106_rows_unchanged':True,'all_catalog_sequences_acl_roles_unchanged':True,
 'current_head_unchanged':True,'server_start_unchanged':True,'private_rls_acl':True,'protected_owner_contacts':82,
 'private_profiles':46,'original_accepted_jobs':9,'signup_jobs_unchanged':11,'cms_version':7,'floorplans':2,
 'real_logins':4,'assignment_requests':9,'new_events':7,'new_notices':7,'final_events':18,'final_notices':18,
 'exact_replays':1,'agent_refusals':1,'account_events':2,'account_notices':0,'valuation_notices':1,'seller_notices':3,
 'owner_promotions':1,'assignment_mirrors':1,'activity_additions':2,'controls':2,'controls_restored':True,
 'boundaries':16,'seed_mock_calls':3,'ui_provider_dispatch':False,'external_provider_attempts':0,
 'unexpected_mutations':0,'inbox_writes':0,'private_files_removed':1}
BROWSER = {'status':'PASS','journeys':16,'geometry':26,'contexts':5,'presence':2,'actual_logins':4,
 'assignment_requests':9,'successful_responses':8,'unique_request_ids':8,'persisted_assignment_intents':7,'exact_replays':1,'uncertain_commits':1,
 'agent_refusals':1,'known204_get_only_recoveries':1,'account_no_email_events':2,'valuation_notices':1,'seller_notices':3,
 'control_transitions':2,'api_boundary_checks':16,'assignment_ui_page_checks':2,'signup_ui_page_checks':2,
 'canceled_history_reads':2,'signup_read_recoveries':1,'auth_loss_checks':1,'keyboard_saves':1,'closed_contexts':5,
 'page_errors':0,'unexpected_mutations':0,'external_writes':0,'tracking_requests':0}
LABELS = {'lead_controls','assignment_loading','assignment_history','assignment_uncertain','assignment_current_error',
 'assignment_refused','account_no_email','seller_three_notices','users','signup_loading','signup_tracked','signup_untracked','signup_error'}
JOURNEYS = {'real_sessions_boundaries','lazy_keyboard_entry','loading_paging','history_hide_abort','target_close_abort',
 'uncertain_commit','later_assignment_old_replay','saved_read_recovery','agent_refusal','account_no_email','valuation_notice',
 'seller_promotion','signup_paging','signup_untracked','signup_error_recovery','auth_loss_clear'}
# Populated from the actual source-owned assertion literals by file-only controls.
BROWSER_REASONS = set(['ACTUAL_ACCOUNT_CLEAR_REQUIRED', 'ACTUAL_ACTOR_ROLE_REQUIRED', 'ACTUAL_ADMIN_UI_LOGIN_REQUIRED', 'ACTUAL_AGENT_SELECTION_REQUIRED', 'ACTUAL_AUTH_LOSS_CLEARS_CURRENT_HISTORY_REQUIRED', 'ACTUAL_BOUNDARY_STATUS_REQUIRED', 'ACTUAL_ENABLED_AGENT_SELECT_REQUIRED', 'ACTUAL_NOTICE_DATES_REQUIRED', 'ACTUAL_OWNED_LEAD_PANEL_REQUIRED', 'ACTUAL_OWNER_PROMOTION_MIRROR_REQUIRED', 'ACTUAL_RECORDED_ATTEMPTS_REQUIRED', 'ACTUAL_REQUEST_UUID_REQUIRED', 'ACTUAL_STORED_PAGE_ORDER_REQUIRED', 'ACTUAL_UNCERTAIN_FREEZE_REQUIRED', 'ALL_LOCALE_CONTEXTS_CLOSED_REQUIRED', 'ALL_SIXTEEN_ORDERED_JOURNEYS_REQUIRED', 'ASSIGNMENT_ACTUAL_DATE_REQUIRED', 'ASSIGNMENT_ACTUAL_LOADING_REQUIRED', 'ASSIGNMENT_HISTORY_NO_DELIVERY_ACTION_REQUIRED', 'ASSIGNMENT_METADATA_ONLY_NO_IDENTIFIERS_REQUIRED', 'ASSIGNMENT_REAL_SECOND_PAGE_REQUIRED', 'ASSIGNMENT_VISIBLE_PAGE_LENGTH_REQUIRED', 'AUTHORITATIVE_CURRENT_AGENT_REQUIRED', 'BAD_AGENT404_RETAINS_VALID_CURRENT_TARGET_REQUIRED', 'BROWSER_PRIVATE_NO_STORE_REQUIRED', 'CLIENT_UNMOUNT_ABORTS_OWNED_REQUEST_REQUIRED', 'CONTROL_OVERFLOW', 'EXACT_ASSIGNMENT_ACTION_COUNTS_REQUIRED', 'EXACT_FIXTURE_SCOPE_REQUIRED', 'EXACT_OWNED_CONTROL_REQUIRED', 'EXACT_PAGE_LENGTH_REQUIRED', 'EXACT_PAGING_RECOVERY_KEYBOARD_COUNTS_REQUIRED', 'EXACT_SCOPED_REPORT_REQUIRED', 'EXACT_SESSION_GEOMETRY_BOUNDARY_COUNTS_REQUIRED', 'EXACT_UUID_BODY_REPLAY_REQUIRED', 'EXACT_VISIBLE_NOTICE_COUNT_REQUIRED', 'FINITE_PHASE_REQUIRED', 'FIXED_OWNED_READ_REQUIRED', 'FOUR_SYNTHETIC_ROLES_REQUIRED', 'HIDE_REMOVES_CACHED_HISTORY_REQUIRED', 'HISTORY_LAZY_NO_READ_BEFORE_OPEN_REQUIRED', 'INVALID_INPUT_NOT_ECHOED_REQUIRED', 'KEYBOARD_FOCUS_REQUIRED', 'KNOWN204_GET_ONLY_LOCK_REQUIRED', 'LOCALIZED_COPY_REQUIRED', 'METADATA_WHITELIST_REQUIRED', 'NO_PRIVATE_CONTENT_OR_MACHINE_REFERENCE_REQUIRED', 'OLD_TARGET_RESPONSE_CANNOT_POPULATE_NEW_TARGET', 'OWNED_ACCOUNT_ID_REQUIRED', 'OWNED_BOUNDED_FIXTURE_REQUIRED', 'OWNED_BROWSER_ISOLATION_REQUIRED', 'OWNED_LOOPBACK_PRESENCE_REQUIRED', 'PRIVATE_NO_STORE_REQUIRED', 'PROCESS_ERROR', 'READONLY_NO_SEND_RETRY_REQUIRED', 'REAL_COMMITTED204_BEFORE_ABORT_REQUIRED', 'REAL_COOKIE_LOGIN_REQUIRED', 'REFRESH_CURRENT_MUST_NOT_REPEAT_PUT', 'SEED_ACTUAL_STORED_PAGING_REQUIRED', 'SELLER_EXACT_THREE_NOTICE_KINDS_REQUIRED', 'SOURCE_BOUND_PRIVATE_SEED_FACTS_REQUIRED', 'SOURCE_CLOSE_CONTROL_REQUIRED', 'STAGE_DEADLINE', 'TIMEOUT', 'TWO_ACCOUNT_ACTIONS_EXPLICIT_ZERO_EMAIL_REQUIRED', 'UNCHANGED_DEFAULT10_REQUIRED', 'UNCHANGED_SIGNUP_ERROR_CLEARS_ROWS_REQUIRED', 'UNCHANGED_UNTRACKED_SIGNUP_REQUIRED', 'UNIQUE_ACCOUNT_HISTORY_REQUIRED', 'UNIQUE_GEOMETRY_REQUIRED', 'UNIQUE_JOURNEY_REQUIRED', 'UNIQUE_OWNED_USER_CARD_REQUIRED', 'UNIQUE_USER_PANEL_REQUIRED', 'VALUATION_DOES_NOT_PROMOTE_BUYER_REQUIRED', 'VALUATION_EXACT_ONE_NOTICE_REQUIRED', 'VIEWPORT_OVERFLOW', 'VISIBLE_DATE_ISOLATION_REQUIRED', 'VISIBLE_PAGE_LENGTH_REQUIRED', 'VISIBLE_STATUS_ISOLATION_REQUIRED'])
API_REASONS = set(['actual_api_server_required', 'actual_assignment_signup10_1_required', 'actual_assignment_status_required', 'actual_assignment_time_within_command_required', 'actual_baseline106_upgrade_required', 'actual_e8_106_required', 'actual_f7_108_required', 'actual_history_status_no_store_required', 'actual_payload_free_history_select_required', 'actual_prebaseline_mock_batch_required', 'actual_previous99_required', 'all7_inbox_empty_required', 'all_other_rows_sequences_catalog_security_preserved_required', 'bounded_history_paging_required', 'bounded_metadata_response_required', 'bounded_owned_assignment_required', 'bounded_seed_output_required', 'control_command_binding_required', 'control_immediate_prestate_required', 'current_head_postmaster_unchanged_required', 'empty_signup_seed_required', 'exact18_event_notice_request_set_required', 'exact26ca_baseline_required', 'exact_assignment_lead_columns_required', 'exact_boundary_actor_target_query_required', 'exact_control_order_required', 'exact_current_metadata_order_projection_required', 'exact_four_login_bodies_required', 'exact_intent_body_target_agent_required', 'exact_login_command_control_ledger_required', 'exact_one_owner_promotion_mirror_all_users_preserved_required', 'exact_one_private_facts_removed_required', 'exact_readonly_helper_required', 'exact_real_mock_outcome_request_binding_required', 'exact_replay_or_fresh_request_required', 'exact_sixteen_boundaries_required', 'exact_two_account_activity_rows_required', 'existing_assignment_timestamp_preserved_required', 'explicit_locale_required', 'finite_history_query_required', 'fixed_mock_provider_route_required', 'fresh_marked_database_required', 'history_private_payload_select_forbidden', 'immutable_seed_events_notices_required', 'independent_account_activity_fields_required', 'independent_event_actor_source_action_cycle_digest_required', 'independent_exact107_model_names_required', 'independent_notice_kind_set_required', 'initial_accounts_unassigned_required', 'known_notice_template_required', 'new_assignment_private_rls_required', 'new_assignment_private_role_acl_required', 'no_guard_or_provider_attempt_required', 'no_lead_insertion_deletion_required', 'no_private_payload_projection_required', 'notice_recipient_template_digest_event_time_required', 'one_actual_history_response_required', 'one_real_assignment_response_required', 'original99_old_columns_preserved_required', 'owned_authenticated_control_required', 'owned_facts_inode_required', 'owned_history_target_required', 'owned_nonce_locale_required', 'owned_unused_unknown_id_required', 'per_command_assigned_time_bound_required', 'per_command_exact_append_only_rows_required', 'per_command_exact_lead_assignment_columns_required', 'per_command_exact_user_assignment_role_change_required', 'per_command_request_event_time_binding_required', 'per_command_unrelated108_rows_sequences_preserved_required', 'private_facts_absence_required', 'real_login_no_limiter_bypass_required', 'real_prebaseline_status_mix_required', 'replay_refusal_no_database_change_required', 'retained_original_seed_required', 'row_table_allowlist_required', 'three_mock_calls_and_disabled_ui_worker_required', 'ui_seven_pending_notices_no_handoff_required', 'unchanged_four_role_seed_required', 'unrelated_lead_unchanged_required', 'upgrade106_catalog_preserved_required', 'upgrade106_rows_sequences_preserved_required', 'upgrade106_security_preserved_required'])
STAGES = {'source','previous99','baseline106','current108','seed_signup','seed_assignment_queue','browser','final_preservation','read','mutation','control'}
CLASSES = {'Failure','RuntimeError','ValueError','TypeError','AssertionError','KeyError','AttributeError','ImportError',
 'ModuleNotFoundError','OperationalError','ProgrammingError','IntegrityError','DataError','InterfaceError','InternalError',
 'TimeoutError','OSError','PermissionError','FileNotFoundError','CalledProcessError','TimeoutExpired','UnhandledError','HTTPException','RehearsalFailure'}


def require(value):
    if not value:
        raise ValueError('receipt_refused')


def pairs(items):
    result={}
    for key,value in items:
        require(key not in result)
        result[key]=value
    return result


def decoded(raw,cap):
    require(len(raw.encode('utf-8'))<=cap)
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    require(type(value) is dict)
    return value


def exact(value,expected):
    require(type(value) is dict and value.keys()==expected.keys())
    require(all(type(value[key]) is type(want) and value[key]==want for key,want in expected.items()))


def browser_phases(locale):
    return {'fixture','final'}|{locale+'_'+name for name in JOURNEYS}|{
        locale+'_'+label+'_geometry_'+str(width) for label in LABELS for width in (320,390)}


def safe_record(prefix,raw,locale):
    if prefix=='ASSIGNMENT_BROWSER_GEOMETRY':
        value=decoded(raw,256)
        require(value.keys()=={'label','width','document'} and value['label'] in {locale+'_'+label for label in LABELS}
            and type(value['width']) is int and value['width'] in (320,390) and type(value['document']) is int
            and 1<=value['document']<=100000)
        return value
    if prefix=='ASSIGNMENT_BROWSER_PRESENCE':
        value=decoded(raw,256)
        require(value.keys()=={'locale','page','before','modeled','after','visible'} and value['locale']==locale
            and value['page'] in ('main','later') and all(type(value[key]) is bool for key in ('before','modeled','after','visible')))
        require(value['modeled'] is (not value['before']) and value['after'] is True and value['visible'] is True)
        return value
    if prefix=='ASSIGNMENT_BROWSER_FAILURE':
        require(len(raw)<=200)
        stage,reason=raw.split(' ')
        require(stage in browser_phases(locale) and reason in BROWSER_REASONS)
        return raw
    if prefix=='ASSIGNMENT_BROWSER_OBSERVATION':
        value=decoded(raw,256)
        require(value.keys()=={'kind','status'} and value['kind'] in ('read','save','control','login')
            and type(value['status']) is int and 100<=value['status']<=599)
        return value
    if prefix=='ASSIGNMENT_BROWSER_FRAMES':
        value=decoded(raw,512)
        require(value.keys()=={'frames'} and type(value['frames']) is list and 1<=len(value['frames'])<=8)
        for frame in value['frames']:
            require(type(frame) is dict and frame.keys()=={'line','column'} and all(type(n) is int and 1<=n<=99999 for n in frame.values()))
        return value
    if prefix=='ASSIGNMENT_BROWSER_CONTROL_CENSUS':
        value=decoded(raw,2048)
        require(value.keys()=={'positive_count','outside_count','rows'} and type(value['positive_count']) is int
            and 0<=value['positive_count']<=1000000 and type(value['outside_count']) is int
            and 0<=value['outside_count']<=value['positive_count'] and type(value['rows']) is list and len(value['rows'])<=8)
        for row in value['rows']:
            require(type(row) is dict and row.keys()=={'ordinal','tagCode','left','right','width'}
                and all(type(number) is int for number in row.values()) and 0<=row['ordinal']<value['positive_count']
                and 0<=row['tagCode']<=3 and -100000<=row['left']<=row['right']<=100000 and 0<=row['width']<=100000)
        return value
    if prefix in ('ASSIGNMENT_FIXTURE_GUARD','ASSIGNMENT_FIXTURE_FAILURE'):
        value=decoded(raw,512)
        require(value.keys()=={'stage','error_type','reason','sqlstate'} and value['stage'] in STAGES
            and value['error_type'] in CLASSES and value['reason'] in API_REASONS|{'unexpected_failure'}
            and (value['sqlstate']=='unavailable' or re.fullmatch('[0-9A-Z]{5}',value['sqlstate'])))
        return value
    raise ValueError('unknown_marker')


def parse(mode,raw,native,locale):
    require(mode in ('browser','api') and locale in ('en','es') and type(native) is int and native==0
        and len(raw)<=4*1024*1024)
    text=raw.decode('utf-8',errors='strict')
    expected_prefix='ASSIGNMENT_BROWSER_RESULT' if mode=='browser' else 'ASSIGNMENT_FIXTURE_RESULT'
    result=[];geometry={};presence={}
    for line in text.splitlines():
        if 'ASSIGNMENT_' not in line:
            continue
        require(line.startswith('ASSIGNMENT_') and ' ' in line)
        prefix,body=line.split(' ',1)
        if prefix==expected_prefix:
            result.append(decoded(body,2048))
        elif mode=='browser' and prefix in ('ASSIGNMENT_BROWSER_GEOMETRY','ASSIGNMENT_BROWSER_PRESENCE'):
            value=safe_record(prefix,body,locale)
            if prefix.endswith('GEOMETRY'):
                key=(value['label'],value['width'])
                require(key not in geometry and value['document']<=value['width']+1)
                geometry[key]=value
            else:
                require(value['page'] not in presence)
                presence[value['page']]=value
        else:
            # Even a valid failure/census/observation/frames record can never pass.
            safe_record(prefix,body,locale)
            raise ValueError('failure_marker')
    require(len(result)==1)
    value=result[0]
    expected={**(BROWSER if mode=='browser' else API),'locale':locale}
    if mode=='browser':
        require(type(value.get('blocked_external_requests')) is int and 0<=value['blocked_external_requests']<=999)
        expected['blocked_external_requests']=value['blocked_external_requests']
        require(set(geometry)=={(locale+'_'+label,width) for label in LABELS for width in (320,390)}
            and presence.keys()=={'main','later'})
    exact(value,expected)
    return value


def emit_safe(raw,locale):
    seen=CounterLike()
    for line in raw.decode('utf-8',errors='replace').splitlines():
        if not line.startswith('ASSIGNMENT_') or ' ' not in line:
            continue
        prefix,body=line.split(' ',1)
        if prefix.endswith('_RESULT'):
            continue
        try:
            value=safe_record(prefix,body,locale)
            seen.add(prefix)
            cap=26 if prefix.endswith('GEOMETRY') else 2 if prefix.endswith('PRESENCE') else 1
            require(seen.values[prefix]<=cap)
            print(prefix+' '+(value if isinstance(value,str) else json.dumps(value,separators=(',',':'),sort_keys=True)),flush=True)
        except (ValueError,TypeError,KeyError,RecursionError,UnicodeError):
            pass


class CounterLike:
    def __init__(self):
        self.values={}
    def add(self,key):
        self.values[key]=self.values.get(key,0)+1


def main():
    require(len(sys.argv)==5 and sys.argv[1] in ('browser','api') and sys.argv[4] in ('en','es'))
    mode,filename,native,locale=sys.argv[1:]
    path=Path(filename)
    require(path.is_file() and not path.is_symlink() and path.stat().st_size<=4*1024*1024)
    raw=path.read_bytes()
    emit_safe(raw,locale)
    try:
        result=parse(mode,raw,int(native),locale)
    except (ValueError,TypeError,KeyError,RecursionError,UnicodeError):
        print('ASSIGNMENT_RECEIPT_REFUSED mode='+mode+' locale='+locale,flush=True)
        return 1
    print(('ASSIGNMENT_BROWSER_RESULT' if mode=='browser' else 'ASSIGNMENT_FIXTURE_RESULT')+' '+json.dumps(result,sort_keys=True,separators=(',',':')),flush=True)
    return 0


if __name__=='__main__':
    sys.exit(main())

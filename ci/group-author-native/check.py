"""Original134 checker plus exact isolated current group28; no partial acceptance."""
from hashlib import sha256
from importlib.util import module_from_spec,spec_from_file_location
import json
from pathlib import Path
import re,sys
EXPECTED=json.loads('{"status":"PASS","tests":28,"group_input_tests":4,"social_group_tests":24,"source":"6ef69b249c573eedaea962dad530eaed0a612b24","tree":"f1ef617e51e0d8a900d9f4bfc15a559bf676276f","schema_tables":106,"model_tables":105,"head":"e8b5a3d1f907","separate_database_and_process":true,"genuine_previous99_baseline106_current_migration":true,"current106_catalog_unchanged":true,"canonical106_rows_catalog_sequences_unchanged":true,"canonical_profile_private_guards_unchanged":true,"canonical_server_start_unchanged":true,"committed_synthetic_group_rows_present":true,"inbox_tables_empty":true,"external_provider_attempts":0}')
REASONS=set(['actual_committed_group_and_chosen_avatar_fixtures_required', 'actual_group_fixture_enabled_required', 'all_exact_current_group_cases_required', 'bounded_source_output_required', 'canonical106_rows_catalog_all_sequences_unchanged_required', 'canonical_current106_required', 'canonical_database_marker_required', 'canonical_postmaster_start_unchanged_required', 'current_group106_catalog_unchanged_required', 'exact_group_database_and_disabled_provider_workers_required', 'exact_owned_nonce_required', 'exact_readonly_profile_helper_required', 'external_failure', 'fresh_marked_group_database_required', 'group_does_not_grant_or_write_inbox_required', 'real_current_group_catalog_parity_required', 'real_group_current106_model105_required', 'real_group_previous99_required', 'separate_fresh_application_process_required', 'unchanged_dependency_inventory_required', 'zero_provider_bounded_capture_required'])
METHODS={'group_inputs': ['test_link_and_member_roles_are_bounded', 'test_post_photos_are_unique_and_content_is_required', 'test_post_requires_captured_group_version', 'test_private_defaults_and_explicit_alias'], 'social_groups': ['test_archive_and_stale_version_do_not_resurrect_membership', 'test_author_selection_replacement_and_removal_never_use_account_or_cover', 'test_cached_parent_is_refreshed_after_another_session_hides_it', 'test_closed_group_managers_can_revoke_private_access_without_new_grants', 'test_closed_groups_allow_withdrawal_but_no_new_activity', 'test_direct_invitation_decline_revoke_and_expiry', 'test_group_moderator_permissions_and_hidden_photo_comments', 'test_joining_group_never_grants_existing_project_access', 'test_last_link_use_is_transactionally_serialized', 'test_link_hash_only_idempotency_revocation_and_ban', 'test_manager_reload_retains_versions_for_unblock_and_reinvite', 'test_moderator_demotion_conceals_hidden_comment_receipt', 'test_new_group_tables_are_private_and_receipts_retained', 'test_open_join_group_admin_boundaries_and_explicit_unblock', 'test_opted_authors_keep_group_alias_and_independent_profile_privacy', 'test_optin_and_privacy_replay_change_only_current_projection', 'test_pending_members_cannot_read_private_group_images', 'test_private_group_create_replay_and_targeted_consent', 'test_private_photo_revocation_applies_to_original_uploader', 'test_private_post_draft_rejects_new_group_audience_but_committed_replay_survives', 'test_real_route_auth_cookie_origin_and_no_store', 'test_real_routes_optin_all_seven_paths_without_breaking_cached_clients', 'test_registered_discovery_request_cancel_approval_and_leave', 'test_visibility_withdrawal_never_republishes_private_history']}
SUITES=[('group_inputs',4),('social_groups',24)]
STAGES={'source','previous99','baseline106','current106','group_inputs4','social_groups24','preservation'}
def require(value,reason):
    if not value:raise AssertionError(reason)
def pairs(items):
    result={}
    for key,value in items:
        require(key not in result,'duplicate_json_key');result[key]=value
    return result
def parse(line,prefix,limit):
    require(len(line)<=limit,'bounded_record_required')
    value=json.loads(line[len(prefix):],object_pairs_hook=pairs)
    require(type(value) is dict,'object_required');return value
def main(args):
    require(len(args)==10,'exact_two_process_arguments_required')
    old=Path(__file__).resolve().parent.parent/'profile-customization-native/check.py'
    require(old.is_file() and not old.is_symlink() and sha256(old.read_bytes()).hexdigest()=='1c68fc3aea5965d500f749cf5327c39e00eb4f531213ad97e013d331f0986bd9','exact_original134_checker_required')
    spec=spec_from_file_location('group_fresh_original134_checker',old);profile=module_from_spec(spec);spec.loader.exec_module(profile)
    profile.main(args[:5])
    filename,client,native,running,oom=args[5:]
    require(all(re.fullmatch(r'[0-9]{1,3}',n) and int(n)<=255 for n in (client,native)) and running in {'true','false'} and oom in {'true','false'},'finite_process_states_required')
    p=Path(filename);require(p.is_file() and not p.is_symlink() and 0<p.stat().st_size<=2*1024*1024,'bounded_group_log_required')
    counts=[];results=[];failed=False;failure_count=0
    for line in p.read_bytes().splitlines():
        if line.startswith(b'GROUP_AUTHOR_NATIVE_COUNTS '):
            value=parse(line,b'GROUP_AUTHOR_NATIVE_COUNTS ',512)
            require(set(value)=={'suite','tests','failures','errors','skipped','provider_attempts'} and type(value['suite']) is str and value['suite'] in dict(SUITES),'exact_suite_keys_required')
            require(all(type(v) is int and 0<=v<=999 for k,v in value.items() if k!='suite'),'finite_suite_counts_required')
            require(len(counts)<2,'exact_two_suite_records_required');counts.append(value)
            print('GROUP_AUTHOR_NATIVE_COUNTS '+json.dumps(value,sort_keys=True))
        elif line.startswith(b'GROUP_AUTHOR_NATIVE_FAILED_METHOD '):
            require(len(line)<=320,'bounded_method_required')
            match=re.fullmatch(rb'GROUP_AUTHOR_NATIVE_FAILED_METHOD (group_inputs|social_groups) ([a-z0-9_]+)',line)
            require(match is not None,'finite_failed_method_required')
            suite,method=(v.decode('ascii') for v in match.groups())
            require(method=='fixture_setup_or_cleanup' or method in METHODS[suite],'source_owned_method_required')
            print(line.decode('ascii'));failed=True
        elif line.startswith(b'GROUP_AUTHOR_NATIVE_FAILURE '):
            value=parse(line,b'GROUP_AUTHOR_NATIVE_FAILURE ',640);failure_count+=1
            require(failure_count==1 and set(value)=={'stage','error_type','reason','sqlstate'},'exact_single_failure_required')
            require(type(value['stage']) is str and value['stage'] in STAGES and type(value['reason']) is str and value['reason'] in REASONS,'finite_failure_scope_required')
            require(type(value['error_type']) is str and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}',value['error_type']) and type(value['sqlstate']) is str and (value['sqlstate']=='unavailable' or re.fullmatch(r'[0-9A-Z]{5}',value['sqlstate'])),'finite_failure_metadata_required')
            print('GROUP_AUTHOR_NATIVE_FAILURE '+json.dumps(value,sort_keys=True));failed=True
        elif line.startswith(b'GROUP_AUTHOR_NATIVE_RESULT '):
            require(not results,'single_result_required');results.append(parse(line,b'GROUP_AUTHOR_NATIVE_RESULT ',2048))
        elif line.startswith(b'GROUP_AUTHOR_NATIVE_') or line.startswith(b'PROFILE_CUSTOMIZATION_NATIVE_') or line.startswith(b'INBOX_RESILIENCE_NATIVE_'):
            raise AssertionError('unknown_or_wrong_scope_marker')
    require(client==native=='0' and running==oom=='false' and not failed,'actual_group_process_success_required')
    require(counts==[{'suite':label,'tests':n,'failures':0,'errors':0,'skipped':0,'provider_attempts':0} for label,n in SUITES],'all_exact28_current_cases_required')
    require(results==[EXPECTED] and set(results[0])==set(EXPECTED) and all(type(results[0][k]) is type(v) for k,v in EXPECTED.items()),'exact_current_group_result_required')
    print('GROUP_AUTHOR_NATIVE_RESULT '+json.dumps(EXPECTED,sort_keys=True))
    print('GROUP_AUTHOR_NATIVE_ACCEPTED profile_cases=134 group_input_cases=4 social_group_cases=24 tests=162')
if __name__=='__main__':main(sys.argv[1:])

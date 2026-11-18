#!/usr/bin/env bash
set -euo pipefail
die() { printf 'CUSTOMER_FOLLOWUP_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ $# == 6 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api ]] || die CI_REQUIRED
[[ ${GITHUB_REF:-} == refs/heads/codex-ci-network-live-core-native-20261118-v4 && ${GITHUB_RUN_ATTEMPT:-} == 1 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die EXACT_EVENT_REQUIRED
[[ ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 ]] || die HOSTED_LINUX_REQUIRED
ops="$1"; application="$2"; prior="$3"; recipe="$4"; legacy="$5"; live="$6"
[[ "$ops" == "$GITHUB_WORKSPACE/followup-ops" && "$application" == "$GITHUB_WORKSPACE/followup-app" &&
   "$prior" == "$GITHUB_WORKSPACE/communications-recipe" && "$recipe" == "$GITHUB_WORKSPACE/migration-recipe" &&
   "$legacy" == "$GITHUB_WORKSPACE/legacy-inquiry-recipe" && "$live" == "$GITHUB_WORKSPACE/live-app" ]] \
  || die EXACT_CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$application" "$prior" "$recipe" "$legacy" "$live"; do
  [[ $(realpath "$checkout") == "$checkout" && -z $(git -C "$checkout" status --porcelain=v1 --untracked-files=all) ]] \
    || die EXACT_CLEAN_CHECKOUTS_REQUIRED
done
app_commit=$(git -C "$application" rev-parse HEAD)
app_tree=$(git -C "$application" rev-parse 'HEAD^{tree}')
[[ "$app_commit" == 01695530df670a94ac28f8b3bf36cc34b5b7a942 && "$app_tree" == e88ea0c57f9fac39545322532a5992d5a2927676 &&
   $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" && $(git -C "$ops" show -s --format=%P HEAD) == d284ff5ac9e4da093a39fa21d10f126da1222351 &&
   ! -e "$application/.env" && ! -L "$application/.env" ]] || die EXACT_IMMUTABLE_PRODUCT_REQUIRED
# The successor scopes one historical migration fixture. Product and deployed
# migration blobs retain the reviewed 081868 behavior; both test repairs are explicit.
[[ $(git -C "$application" show -s --format=%P HEAD) == 796f25ff52724db56313916e129df4b252a682fd &&
   $(git -C "$application" diff --name-only HEAD^ HEAD) == tests/test_service_workflow_migration.py &&
   $(git -C "$application" diff --numstat HEAD^ HEAD) == $'7\t2\ttests/test_service_workflow_migration.py' &&
   $(git -C "$application" diff --name-only 081868373f318498ddaa835de6496aa6e62d6f57 HEAD) == $'tests/test_professionals.py\ntests/test_service_workflow_migration.py' ]] ||
  die EXACT_TEST_ONLY_SOURCE_SUCCESSOR_REQUIRED
[[ $(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) == $'.github/workflows/network-live-core-native.yml\nci/network-live-core/run-hosted-customer-followup.sh\nci/network-live-core/verify-customer-followup.py\nci/network-live-core/verify-live-delivery-rollback.py\nci/network-live-core/verify-live-delivery-schema.py' ]] ||
  die EXACT_OPERATIONS_DELTA_REQUIRED
[[ $(git -C "$prior" rev-parse HEAD) == e660040e0c514aca3f7f5b7d7a5007e4fdc596c4 &&
   $(git -C "$prior" rev-parse 'HEAD^{tree}') == 4e230dcfd86ad1a3353ca3b30efe4a425e60f884 &&
   $(git -C "$recipe" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 &&
   $(git -C "$legacy" rev-parse HEAD) == 2a9ecd58a8f22c11476f268bacd9b1c410c782a3 ]] || die EXACT_ACCEPTED_RECIPES_REQUIRED
[[ $(git -C "$live" rev-parse HEAD) == 7f63fec927f75af3f901f74297973253ad5c743b &&
   $(git -C "$live" rev-parse 'HEAD^{tree}') == 1a87c3d4c96e2df0c956e18f0f26c982e7ef3289 &&
   ! -e "$live/.env" && ! -L "$live/.env" ]] || die EXACT_LIVE_BASE_REQUIRED
prior_tools="$prior/ci/browser"
tools="$ops/ci/network-live-core"
dockerfile="$recipe/ci/migration/services163.Dockerfile"
[[ -f "$tools/verify-customer-followup.py" && ! -L "$tools/verify-customer-followup.py" ]] || die EXACT_TEST_REQUIRED
printf '%s  %s\n' \
  e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9 "$dockerfile" \
  31d9792c4fc11f924af0b0ab2db3247f77eb083c1274fab94fb99c75197db60b "$prior_tools/test_cumulative_communications_acceptance.py" \
  fd7079170b9307ef4d523a3692b83bba2490bf16b7eb26036e4eb718a00edba7 "$prior_tools/serve_cumulative_communications_fixture.py" \
  f29933d32dae31449fe9f56ed3e63946ed302e71fa7184a7117e8fda9a15ebfd "$legacy/ci/legacy-inquiry/run-legacy-inquiry-tests.py" \
  | sha256sum --check --status || die ACCEPTED_RECIPE_HASH_CHANGED

umask 077
[[ -n ${RUNNER_TEMP:-} && -d "$RUNNER_TEMP" && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die RUNNER_SCRATCH_REQUIRED
nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_RUN_NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-customer-followup.XXXXXXXX")
run_key="$GITHUB_RUN_ID-$nonce"
label=org.ilovedrrealty.customer-followup
image="ildr-customer-followup:$run_key"
python_digest=sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
postgres='docker.io/library/postgres:17.6@sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409'
database=ildr_platform_test_20261025
pg=''

owned() {
  [[ "$1" =~ ^[a-f0-9]{64}$ && $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$1") == "$run_key" ]]
}

cleanup() {
  local code=$? clean=1 ids images id tagged
  trap - EXIT
  set +e
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  for id in $ids; do
    [[ "$id" != "$pg" ]] || continue
    if owned "$id"; then bounded 20s docker rm -f -v "$id" >/dev/null 2>&1 || clean=0; else clean=0; fi
  done
  if [[ -n "$pg" ]]; then
    if owned "$pg"; then bounded 20s docker rm -f -v "$pg" >/dev/null 2>&1 || clean=0; else clean=0; fi
  fi
  images=$(bounded 15s docker image ls -q --no-trunc --filter "label=$label=$run_key" | sort -u) || clean=0
  if [[ -n "$images" ]]; then
    tagged=$(bounded 15s docker image inspect --format '{{.Id}}' "$image") || clean=0
    if [[ "$images" =~ ^sha256:[a-f0-9]{64}$ && "$images" == "$tagged" ]]; then
      bounded 15s docker image rm "$image" >/dev/null 2>&1 || clean=0
    else clean=0; fi
  fi
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  images=$(bounded 15s docker image ls -q --no-trunc --filter "label=$label=$run_key") || clean=0
  [[ -z "$ids" && -z "$images" ]] || clean=0
  if [[ "$state" == "$RUNNER_TEMP"/ildr-customer-followup.* && -d "$state" && ! -L "$state" && $(realpath "$state") == "$state" ]]; then
    rm -rf -- "$state" || clean=0
  else clean=0; fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'CUSTOMER_FOLLOWUP_OWNED_CLEANUP commit=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT

# The online build receives only the pinned dependency recipe and requirements.
mkdir "$state/build"
mkdir "$state/rollback"
cp "$application/requirements.txt" "$state/build/"
git -C "$live" archive --format=tar HEAD | tar -xf - -C "$state/rollback" || die EXACT_ROLLBACK_ARCHIVE_REQUIRED
python3 -I -B - "$application" "$live" "$state/rollback" <<'PY_ROLLBACK' || die EXACT_ROLLBACK_MIGRATIONS_REQUIRED
from pathlib import Path, PurePosixPath
import subprocess, sys
application, live, target = map(Path, sys.argv[1:])
paths = subprocess.check_output(["git", "-C", str(application), "diff", "--name-only", "--diff-filter=A",
    "7f63fec927f75af3f901f74297973253ad5c743b", "01695530df670a94ac28f8b3bf36cc34b5b7a942", "--", "alembic/versions"], text=True).splitlines()
assert len(paths) == 32
for name in paths:
    path = PurePosixPath(name)
    assert path.parts[:2] == ("alembic", "versions") and len(path.parts) == 3 and path.suffix == ".py"
    output = target / name
    assert not output.exists()
    with output.open("xb") as stream:
        stream.write((application / name).read_bytes())
PY_ROLLBACK
for base in "docker.io/library/python:3.11-slim@$python_digest" "$postgres"; do
  bounded 180s docker pull --platform linux/amd64 "$base" >/dev/null || die PINNED_IMAGE_PULL_FAILED
  [[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$base") == linux/amd64 ]] || die EXACT_IMAGE_PLATFORM_REQUIRED
done
bounded 600s docker build --platform linux/amd64 --pull=false --quiet --label "$label=$run_key" \
  --build-arg "PYTHON_DIGEST=$python_digest" --file "$dockerfile" --tag "$image" "$state/build" >"$state/build.log" 2>&1 \
  || die DEPENDENCY_IMAGE_BUILD_FAILED
pg=$(bounded 25s docker create --label "$label=$run_key" --network none --security-opt no-new-privileges:true \
  --memory 768m --memory-swap 768m --pids-limit 96 --cpus 1 --tmpfs /var/lib/postgresql/data:rw,size=384m \
  --env POSTGRES_HOST_AUTH_METHOD=trust --env "POSTGRES_DB=$database" \
  "$postgres" postgres -p 55449 -h 127.0.0.1) || die POSTGRES_CREATE_FAILED
owned "$pg" && [[ $(bounded 15s docker inspect --format '{{.HostConfig.NetworkMode}}' "$pg") == none ]] \
  || die OWNED_ISOLATED_POSTGRES_REQUIRED
bounded 25s docker start "$pg" >/dev/null || die POSTGRES_START_FAILED
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  if bounded 3s docker exec "$pg" pg_isready -h 127.0.0.1 -p 55449 -U postgres -d "$database" >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 1
done
[[ "$ready" == 1 && $(bounded 5s docker exec "$pg" ls /sys/class/net) == lo ]] || die LOOPBACK_POSTGRES_REQUIRED
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d "$database" -v ON_ERROR_STOP=1 \
  -c 'CREATE ROLE anon NOLOGIN NOINHERIT; CREATE ROLE authenticated NOLOGIN NOINHERIT; CREATE ROLE service_role NOLOGIN NOINHERIT' \
  -c "COMMENT ON DATABASE $database IS 'ildr-communications-ci:$nonce'" >/dev/null || die DATABASE_MARKER_REQUIRED

test_container=$(bounded 25s docker create --label "$label=$run_key" --network "container:$pg" --user "$(id -u):$(id -g)" \
  --read-only --init --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 192 --cpus 2 \
  --memory 1536m --memory-swap 1536m --tmpfs /tmp:rw,exec,size=128m \
  --env GITHUB_ACTIONS=true --env GITHUB_REPOSITORY=ilovedrrealty/realty-api --env "ILDR_BROWSER_CI_NONCE=$nonce" \
  --env "SEARCH_FILTER_TEST_DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:55449/$database" \
  --mount "type=bind,src=$application,dst=/application,readonly" --mount "type=bind,src=$tools,dst=/tools,readonly" \
  --mount "type=bind,src=$prior_tools,dst=/prior,readonly" \
  --mount "type=bind,src=$legacy/ci/legacy-inquiry,dst=/legacy,readonly" \
  "$image" /usr/local/bin/python -I -B /tools/verify-customer-followup.py) || die TEST_CONTAINER_CREATE_FAILED
owned "$test_container" || die OWNED_TEST_CONTAINER_REQUIRED
test_exit=0
bounded 1200s docker start --attach "$test_container" >"$state/test.log" 2>&1 || test_exit=$?
test_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$test_container")
[[ "$test_state" =~ ^[0-9]+\ (true|false)\ (true|false)$ ]] || die NATIVE_TEST_STATE_REQUIRED
printf 'CUSTOMER_FOLLOWUP_NATIVE client=%s state=%s\n' "$test_exit" "$test_state"
python3 -I -B - "$state/test.log" "$app_commit" "$app_tree" <<'PY'
from pathlib import Path
import json
import re
import sys

EXPECTED_CONTRACTS = {'customer_delivery': 19, 'lead_delivery': 53, 'owner_leads': 8, 'property_inquiry': 10, 'professionals': 27, 'professional_catalog': 7, 'property_network': 22, 'private_property_registry': 16, 'private_property_registry_migration': 5, 'property_custody': 5, 'history_share': 5, 'service_workflow': 35, 'service_workflow_migration': 5, 'service_visit': 5, 'service_conversation': 5, 'service_negotiation': 5, 'service_review': 5, 'service_rebooking': 3, 'service_favorites': 4, 'service_referrals': 5, 'maintenance_reminders': 5, 'due_work_reminders': 6, 'service_operations': 5, 'property_management': 19, 'transaction_room': 9, 'transaction_documents': 5, 'transaction_document_storage': 7, 'listing_floor_plans': 4, 'admin_trade_contacts': 3, 'legacy_rollout': 1}
path = Path(sys.argv[1])
assert path.stat().st_size <= 262144
prefix = b'CUSTOMER_FOLLOWUP_RESULT '
rows = [json.loads(line[len(prefix):]) for line in path.read_bytes().splitlines() if line.startswith(prefix)]
assert len(rows) == 1
report = rows[0]
if report.get('status') == 'FAIL':
    assert set(report) <= {'status', 'stage', 'exception_type', 'reason'}
    assert all(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_]{1,90}', value) for value in report.values())
else:
    assert report == {
        'status': 'PASS', 'application_commit': sys.argv[2], 'application_tree': sys.argv[3], 'alembic_head': 'b19d8e4c720a',
        'event_kinds': ['signup', 'buyer_interest', 'seller_interest', 'rental_interest', 'property_inquiry'],
        'primary_owner_acceptances': 20, 'primary_customer_acceptances': 5, 'owner_only_guard_case_acceptances': 12,
        'accepted_mock_provider_requests': 37, 'crm_rows_skipped': 28, 'customer_locales': ['en', 'es'],
        'external_transport_attempts': 0, 'real_inbox_delivery_verified': False,
        'customer_link_acceptances': {'en': 2, 'es': 3}, 'team_notices_checked': 32,
        'exact_public_whatsapp_url': True, 'frontend_built': False,
        'candidate_contracts': EXPECTED_CONTRACTS,
        'candidate_contracts_passed': 313, 'triggered_sms_or_automatic_whatsapp': False,
        'focused_legacy_string_capture_passed': 1,
        'postgres_version_num': 170006,
    }
diagnostic_prefix = b'NETWORK_CONTRACT_DIAGNOSTICS '
diagnostics = [json.loads(line[len(diagnostic_prefix):]) for line in path.read_bytes().splitlines()
               if line.startswith(diagnostic_prefix)]
assert len(diagnostics) <= 1
if diagnostics:
    diagnostic = diagnostics[0]
    expected_suites = [('customer_delivery', 'CustomerDeliveryTests', 19),
 ('lead_delivery', 'LeadDeliveryTests', 53),
 ('owner_leads', 'OwnerLeadFeedTests', 8),
 ('property_inquiry', 'PropertyInquiryTests', 10),
 ('legacy_inquiry', 'LegacyInquiryRolloutTests', 1),
 ('contact_leads', 'LeadContactTests', 1),
 ('professionals', 'ProfessionalTests', 27),
 ('professional_catalog', 'ProfessionalCatalogTests', 7),
 ('property_network', 'PropertyNetworkRoutes', 18),
 ('property_network', 'PrivateImageContracts', 4),
 ('private_property_registry', 'PrivateRegistryRoutes', 16),
 ('private_property_registry_migration', 'PrivateRegistryMigration', 5),
 ('property_custody', 'PrivatePropertyCustody', 5),
 ('history_share', 'PrivatePropertyHistoryShares', 5),
 ('service_workflow', 'PrivateServiceWorkflow', 35),
 ('service_workflow_migration', 'PrivateServiceMigration', 5),
 ('service_visit', 'PrivateAwardedServiceWork', 5),
 ('service_conversation', 'PrivateAwardedServiceConversation', 5),
 ('service_negotiation', 'PrivateServiceNegotiationConversation', 5),
 ('service_review', 'PrivateCompletedServiceReview', 5),
 ('service_rebooking', 'PrivateCompletedServiceRebooking', 3),
 ('service_favorites', 'PrivateServiceFavorites', 4),
 ('service_referrals', 'PrivateServiceReferrals', 5),
 ('maintenance_reminders', 'PrivateMaintenanceReminders', 5),
 ('due_work_reminders', 'PrivateDueWorkReminders', 6),
 ('service_operations', 'PrivateServiceOperations', 5),
 ('property_management', 'PropertyManagementTests', 19),
 ('transaction_room', 'TransactionRoomTests', 9),
 ('transaction_documents', 'TransactionDocumentTests', 5),
 ('transaction_document_storage', 'TransactionDocumentStorageTests', 7),
 ('listing_floor_plans', 'ListingFloorPlanTests', 4),
 ('admin_trade_contacts', 'TradeContactsTest', 3)]
    safe_exception_types = {'OperationalError', 'ImportError', 'PermissionError', 'CountMismatch', 'SkipTest', 'ForeignKeyViolation', 'DBAPIError', 'AttributeError', 'UnexpectedSuccess', 'ProgrammingError', 'TypeError', 'NameError', 'ValueError', 'DataError', 'InternalError', 'RuntimeError', 'ModuleNotFoundError', 'IntegrityError', 'ValidationError', 'OtherException', 'KeyError', 'TimeoutError', 'NotNullViolation', 'AssertionError'}
    assert set(diagnostic) == {'status', 'required_cases', 'required_suites', 'run_cases', 'suite_count',
        'unexpected_transport_attempts', 'schema_cleanup_verified', 'suites', 'issues'}
    assert diagnostic['status'] in {'PASS', 'FAIL'}
    assert diagnostic['required_cases'] == 314 and diagnostic['required_suites'] == 32
    assert diagnostic['suite_count'] == len(diagnostic['suites']) == 32
    assert type(diagnostic['schema_cleanup_verified']) is bool
    assert type(diagnostic['unexpected_transport_attempts']) is int and 0 <= diagnostic['unexpected_transport_attempts'] <= 100000
    assert [(row['module'], row['class'], row['expected']) for row in diagnostic['suites']] == expected_suites
    fields = {'module', 'class', 'expected', 'discovered', 'run', 'failures', 'errors', 'skipped',
              'expected_failures', 'unexpected_successes'}
    for row in diagnostic['suites']:
        assert set(row) == fields
        assert all(type(row[key]) is int and 0 <= row[key] <= 100000 for key in fields - {'module', 'class'})
    assert diagnostic['run_cases'] == sum(row['run'] for row in diagnostic['suites'])
    pairs = {(module, class_name) for module, class_name, _ in expected_suites}
    kinds = {'failure', 'error', 'expected_failure', 'skip', 'unexpected_success', 'load_error',
             'harness_error', 'discovered_count_mismatch', 'run_count_mismatch'}
    for item in diagnostic['issues']:
        assert set(item) == {'module', 'class', 'method', 'kind', 'exception_type'}
        assert (item['module'], item['class']) in pairs and item['kind'] in kinds
        assert item['exception_type'] in safe_exception_types
        assert item['method'] == 'setup_or_cleanup' or re.fullmatch(r'test_[a-z_]{1,180}', item['method'])
    clean = (not diagnostic['issues'] and diagnostic['run_cases'] == 314 and
             diagnostic['unexpected_transport_attempts'] == 0 and diagnostic['schema_cleanup_verified'] and
             all(row['discovered'] == row['run'] == row['expected'] and
                 not any(row[key] for key in ('failures','errors','skipped','expected_failures','unexpected_successes'))
                 for row in diagnostic['suites']))
    assert (diagnostic['status'] == 'PASS') is clean
    print(diagnostic_prefix.decode() + json.dumps(diagnostic, sort_keys=True, separators=(',', ':')))
if report['status'] == 'PASS':
    assert len(diagnostics) == 1 and diagnostics[0]['status'] == 'PASS'
print(prefix.decode() + json.dumps(report, sort_keys=True, separators=(',', ':')))
assert report['status'] == 'PASS'
PY
[[ "$test_exit" == 0 && "$test_state" == '0 false false' ]] || die NATIVE_TEST_FAILED

schema_container=$(bounded 25s docker create --label "$label=$run_key" --network "container:$pg" \
  --user "$(id -u):$(id -g)" --read-only --init --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 192 --cpus 2 --memory 1536m --memory-swap 1536m --tmpfs /tmp:rw,exec,size=128m \
  --env GITHUB_ACTIONS=true --env GITHUB_REPOSITORY=ilovedrrealty/realty-api --env "ILDR_BROWSER_CI_NONCE=$nonce" \
  --mount "type=bind,src=$application,dst=/application,readonly" \
  --mount "type=bind,src=$live,dst=/live,readonly" \
  --mount "type=bind,src=$state/rollback,dst=/rollback,readonly" \
  --mount "type=bind,src=$tools,dst=/tools,readonly" \
  "$image" /usr/local/bin/python -I -B /tools/verify-live-delivery-schema.py) || die SCHEMA_CONTAINER_CREATE_FAILED
owned "$schema_container" || die OWNED_SCHEMA_CONTAINER_REQUIRED
schema_exit=0
bounded 720s docker start --attach "$schema_container" >"$state/schema.log" 2>&1 || schema_exit=$?
schema_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$schema_container")
[[ "$schema_state" =~ ^[0-9]+\ (true|false)\ (true|false)$ ]] || die SCHEMA_NATIVE_STATE_REQUIRED
printf 'LIVE_DELIVERY_SCHEMA_NATIVE client=%s state=%s\n' "$schema_exit" "$schema_state"
python3 -I -B - "$state/schema.log" "$app_commit" "$app_tree" <<'PY_SCHEMA'
from pathlib import Path
import json, re, sys
path = Path(sys.argv[1])
assert path.stat().st_size <= 131072
prefix = b'LIVE_DELIVERY_SCHEMA_RESULT '
rows = [json.loads(line[len(prefix):]) for line in path.read_bytes().splitlines() if line.startswith(prefix)]
assert len(rows) == 1
report = rows[0]
if report.get('status') == 'FAIL':
    assert set(report) <= {'status', 'stage', 'error_type', 'reason'}
    assert all(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_]{1,100}', value) for value in report.values())
else:
    expected = {
        'status': 'PASS', 'application_commit': sys.argv[2], 'application_tree': sys.argv[3],
        'live_commit': '7f63fec927f75af3f901f74297973253ad5c743b', 'alembic_head': 'b19d8e4c720a',
        'table_count': 92, 'fresh_upgrade_catalog_equal': True, 'old_column_rows_sequences_unchanged': True,
        'reviewed_added_columns': 2, 'reviewed_added_old_table_triggers': 4, 'new_private_tables': 67,
        'repeat_normal_startup_preserved': True, 'compatible_old_source_started': True,
        'retained_accepted_jobs': 9, 'retained_worker_rows': 1, 'external_provider_requests': 0,
        'baseline': {'status': 'PASS', 'stage': 'seed_live', 'historical_users': 4, 'historical_leads': 3, 'historical_inquiries': 1, 'historical_bookings': 1, 'configured_team_recipients': 4, 'retained_accepted_jobs': 9, 'worker_rows': 1, 'owner_trade_contacts': 82, 'trade_contacts': 85, 'trade_sources': 86, 'private_profiles': 46, 'cms_version': 7}, 'new_code_data': {'status': 'PASS', 'stage': 'seed_candidate', 'phone_only_inquiries': 1, 'email_only_inquiries': 1, 'consented_property_inquiries': 1, 'verified_signups': 1, 'linked_bookings': 1, 'delivery_jobs': 39, 'customer_email_jobs': 3, 'network_records': 8}, 'compatible_old_runtime': {'status': 'PASS', 'stage': 'verify_old', 'role_read_checks': 13, 'old_client_intakes': 1, 'fake_owner_notices': 1, 'head_preserved': True, 'delivery_jobs_unchanged': True, 'no_historical_replay': True},
    }
    assert re.fullmatch(r'[a-f0-9]{64}', report.get('full_catalog_sha256', ''))
    expected['full_catalog_sha256'] = report['full_catalog_sha256']
    assert report == expected
print(prefix.decode() + json.dumps(report, sort_keys=True, separators=(',', ':')))
assert report['status'] == 'PASS'
PY_SCHEMA
[[ "$schema_exit" == 0 && "$schema_state" == '0 false false' ]] || die SCHEMA_RUNTIME_FAILED
# Full public custom archive uses the accepted integrated92-table recipe.
source_db="ildr_f01_upgrade_$nonce"
restore_db="ildr_f01_restore_$nonce"
[[ "$source_db" =~ ^ildr_f01_upgrade_[a-f0-9]{32}$ && "$restore_db" =~ ^ildr_f01_restore_[a-f0-9]{32}$ ]] || die EXACT_ARCHIVE_SCOPE_REQUIRED
psql=(docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -v ON_ERROR_STOP=1)
bounded 10s "${psql[@]}" -d postgres -c "CREATE DATABASE $restore_db TEMPLATE template0" \
  -c "COMMENT ON DATABASE $restore_db IS 'ildr-live-communications-ci:$nonce:restore'" \
  >"$state/archive-create.log" 2>&1 || die OWNED_RESTORE_DATABASE_REQUIRED
bounded 75s docker exec "$pg" pg_dump -h 127.0.0.1 -p 55449 -U postgres --format=custom \
  --schema=public --strict-names --lock-wait-timeout=15000 --dbname="$source_db" \
  >"$state/full-public.dump" 2>"$state/archive-dump.log" || die FULL_PUBLIC_ARCHIVE_FAILED
[[ -f "$state/full-public.dump" && ! -L "$state/full-public.dump" && $(head -c 5 "$state/full-public.dump") == PGDMP &&
   $(stat -c %s "$state/full-public.dump") -le 16777216 ]] || die BOUNDED_CUSTOM_ARCHIVE_REQUIRED
bounded 90s docker exec -i "$pg" pg_restore -h 127.0.0.1 -p 55449 -U postgres --exit-on-error \
  --single-transaction --clean --if-exists --dbname="$restore_db" <"$state/full-public.dump" \
  >"$state/archive-restore.log" 2>&1 || die FULL_PUBLIC_RESTORE_FAILED
restore_container=$(bounded 25s docker create --label "$label=$run_key" --network "container:$pg" \
  --user "$(id -u):$(id -g)" --read-only --init --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 192 --cpus 2 --memory 1536m --memory-swap 1536m --tmpfs /tmp:rw,exec,size=128m \
  --env GITHUB_ACTIONS=true --env GITHUB_REPOSITORY=ilovedrrealty/realty-api --env "ILDR_BROWSER_CI_NONCE=$nonce" \
  --mount "type=bind,src=$application,dst=/application,readonly" --mount "type=bind,src=$live,dst=/live,readonly" \
  --mount "type=bind,src=$state/rollback,dst=/rollback,readonly" --mount "type=bind,src=$tools,dst=/tools,readonly" \
  "$image" /usr/local/bin/python -I -B /tools/verify-live-delivery-schema.py restore) || die ARCHIVE_VERIFY_CONTAINER_FAILED
owned "$restore_container" || die OWNED_ARCHIVE_CONTAINER_REQUIRED
restore_exit=0
bounded 180s docker start --attach "$restore_container" >"$state/archive-verify.log" 2>&1 || restore_exit=$?
restore_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$restore_container")
python3 -I -B - "$state/archive-verify.log" <<'PY_RESTORE'
from pathlib import Path
import json, re, sys
path = Path(sys.argv[1]); assert path.stat().st_size <= 131072
prefix = b'LIVE_DELIVERY_SCHEMA_RESULT '
rows = [json.loads(line[len(prefix):]) for line in path.read_bytes().splitlines() if line.startswith(prefix)]
assert len(rows) == 1
report = rows[0]
if report.get('status') == 'FAIL':
    assert set(report) <= {'status', 'stage', 'error_type', 'reason'}
    assert all(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_]{1,100}', value) for value in report.values())
else:
    assert report == {'status': 'PASS', 'alembic_head': 'b19d8e4c720a', 'tables': 92,
        'all_rows_sequences_equal': True, 'all_catalogs_equal': True, 'floor_plan_binary_rows': 2,
        'retained_accepted_jobs': 9, 'retained_private_profiles': 46, 'retained_owner_trade_contacts': 82}
print('NETWORK_FULL_PUBLIC_RESTORE_RESULT ' + json.dumps(report, sort_keys=True, separators=(',', ':')))
assert report['status'] == 'PASS'
PY_RESTORE
[[ "$restore_exit" == 0 && "$restore_state" == '0 false false' ]] || die FULL_ARCHIVE_VERIFICATION_REQUIRED
for source in "$application" "$prior" "$recipe" "$legacy" "$live"; do
  [[ -z $(git -C "$source" status --porcelain=v1 --untracked-files=all) ]] || die MOUNTED_SOURCE_CHANGED
done
printf 'NETWORK_LIVE_CORE_NATIVE_ACCEPTANCE commit=%s application=%s tree=%s run=%s attempt=1 contracts=314 fake_requests=37 tables=92 archive=verified rollback=verified\n' \
  "$GITHUB_SHA" "$app_commit" "$app_tree" "$GITHUB_RUN_ID"

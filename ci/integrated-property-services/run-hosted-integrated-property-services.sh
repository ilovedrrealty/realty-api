#!/usr/bin/env bash
# Run reviewed listing room tours and prior service regressions on disposable Postgres.
set -euo pipefail
die() { printf 'INTEGRATED_PROPERTY_SERVICES_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ $# == 3 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api ]] || die CI_REQUIRED
[[ ${GITHUB_REF:-} == refs/heads/codex-ci-integrated-property-services-api-20261112 && ${GITHUB_RUN_ATTEMPT:-} == 1 ]] || die EXACT_EVENT_REQUIRED
[[ ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die HOSTED_LINUX_REQUIRED
ops="$1"; app="$2"; recipe="$3"
[[ "$ops" == "$GITHUB_WORKSPACE/integrated-ops" && "$app" == "$GITHUB_WORKSPACE/integrated-app" &&
   "$recipe" == "$GITHUB_WORKSPACE/integrated-recipe" ]] || die EXACT_CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$app" "$recipe"; do
  [[ $(realpath "$checkout") == "$checkout" && -z $(git -C "$checkout" status --porcelain=v1 --untracked-files=all) ]] \
    || die EXACT_CLEAN_CHECKOUTS_REQUIRED
done
app_commit=$(git -C "$app" rev-parse HEAD)
app_tree=$(git -C "$app" rev-parse 'HEAD^{tree}')
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" && $(git -C "$ops" rev-parse HEAD^) == "$app_commit" ]] \
  || die EXACT_APPLICATION_PARENT_REQUIRED
[[ $(git -C "$recipe" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 ]] || die EXACT_IMAGE_RECIPE_REQUIRED
dockerfile="$recipe/ci/migration/services163.Dockerfile"
printf '%s  %s\n' e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9 "$dockerfile" \
  | sha256sum --check --status || die IMAGE_RECIPE_HASH_CHANGED
helper="$ops/ci/integrated-property-services"
[[ -f "$helper/run-integrated-property-services-tests.py" && ! -L "$helper/run-integrated-property-services-tests.py" ]] || die TEST_HELPER_REQUIRED

umask 077
[[ -n ${RUNNER_TEMP:-} && -d "$RUNNER_TEMP" && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die RUNNER_SCRATCH_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-integrated-property-services.XXXXXXXX")
nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_RUN_NONCE_REQUIRED
run_key="$GITHUB_RUN_ID-$nonce"
label=org.ilovedrrealty.integrated.property-services
image="ildr-integrated-property-services:$run_key"
python_digest=sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
postgres='docker.io/library/postgres:17@sha256:3b0cda93be7c8dba2ed484d19393d93845dc3dafb88973e248ba8e6d99d784e1'
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
    else
      clean=0
    fi
  fi
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  images=$(bounded 15s docker image ls -q --no-trunc --filter "label=$label=$run_key") || clean=0
  [[ -z "$ids" && -z "$images" ]] || clean=0
  if [[ "$state" == "$RUNNER_TEMP"/ildr-integrated-property-services.* && -d "$state" && ! -L "$state" && $(realpath "$state") == "$state" ]]; then
    rm -rf -- "$state" || clean=0
  else
    clean=0
  fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'INTEGRATED_PROPERTY_SERVICES_OWNED_CLEANUP commit=%s run=%s attempt=1\n' "$app_commit" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT

mkdir "$state/build"
cp "$app/requirements.txt" "$state/build/"
bounded 180s docker pull --platform linux/amd64 "docker.io/library/python:3.11-slim@$python_digest" >/dev/null || die PYTHON_PULL_FAILED
bounded 180s docker pull --platform linux/amd64 "$postgres" >/dev/null || die POSTGRES_PULL_FAILED
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
    ready=1
    break
  fi
  sleep 1
done
[[ "$ready" == 1 && $(bounded 5s docker exec "$pg" ls /sys/class/net) == lo ]] || die LOOPBACK_POSTGRES_REQUIRED
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d "$database" -v ON_ERROR_STOP=1 \
  -c "COMMENT ON DATABASE $database IS 'ildr-integrated-property-services-ci:$nonce'" >/dev/null || die DATABASE_MARKER_REQUIRED
test_container=$(bounded 25s docker create --label "$label=$run_key" --network "container:$pg" --user "$(id -u):$(id -g)" \
  --read-only --init --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 192 --cpus 2 \
  --memory 1536m --memory-swap 1536m --tmpfs /tmp:rw,exec,size=128m \
  --env GITHUB_ACTIONS=true --env GITHUB_REPOSITORY=ilovedrrealty/realty-api \
  --env "ILDR_INTEGRATED_PROPERTY_SERVICES_COMMIT=$app_commit" --env "ILDR_INTEGRATED_PROPERTY_SERVICES_TREE=$app_tree" \
  --env "ILDR_INTEGRATED_PROPERTY_SERVICES_CI_NONCE=$nonce" \
  --env "SEARCH_FILTER_TEST_DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:55449/$database" \
  --mount "type=bind,src=$app,dst=/application,readonly" --mount "type=bind,src=$helper,dst=/tools,readonly" \
  "$image" /usr/local/bin/python -I -B /tools/run-integrated-property-services-tests.py) || die TEST_CONTAINER_CREATE_FAILED
owned "$test_container" || die OWNED_TEST_CONTAINER_REQUIRED
test_exit=0
bounded 180s docker start --attach "$test_container" >"$state/test.log" 2>&1 || test_exit=$?
test_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$test_container")
[[ "$test_state" =~ ^[0-9]+\ (true|false)\ (true|false)$ ]] || die NATIVE_TEST_STATE_REQUIRED
printf 'INTEGRATED_PROPERTY_SERVICES_NATIVE client=%s state=%s\n' "$test_exit" "$test_state"
python3 -I -B - "$state/test.log" "$app_commit" "$app_tree" "$app" <<'PY'
from pathlib import Path
import hashlib, json, re, sys

path = Path(sys.argv[1])
assert path.stat().st_size <= 131072
prefix = b'INTEGRATED_PROPERTY_SERVICES_BACKEND_RESULT '
rows = [json.loads(line[len(prefix):]) for line in path.read_bytes().splitlines() if line.startswith(prefix)]
assert len(rows) == 1
result = rows[0]
def safe(value):
    if isinstance(value, dict): return len(value) <= 25 and all(re.fullmatch(r'(?:[a-z][a-z0-9_]+|PrivateAwardedService(?:Work|Conversation)|PrivateServiceNegotiationConversation|PrivateCompletedServiceReview|PrivateCompletedServiceRebooking|PrivateServiceFavorites|PrivateMaintenanceReminders|PrivateDueWorkReminders|ListingRoomPhotoTourTests|LocationReviewTests|PrivateServiceOperations)', k) and safe(v) for k,v in value.items())
    if isinstance(value, list): return len(value) <= 74 and all(safe(v) for v in value)
    if isinstance(value, str): return re.fullmatch(r'[A-Za-z0-9_ .-]{1,150}', value) is not None
    return value is None or type(value) in (bool,int)
assert safe(result)
print(prefix.decode() + json.dumps(result, sort_keys=True, separators=(',', ':')))
assert result['status'] == 'PASS' and result['application_commit'] == sys.argv[2] and result['application_tree'] == sys.argv[3]
assert result['class_counts'] == {'PrivateAwardedServiceWork': 5, 'PrivateAwardedServiceConversation': 5, 'PrivateServiceNegotiationConversation': 5, 'PrivateCompletedServiceReview': 5, 'PrivateCompletedServiceRebooking': 3, 'PrivateServiceFavorites': 4, 'PrivateMaintenanceReminders': 5, 'PrivateDueWorkReminders': 6, 'ListingRoomPhotoTourTests': 5, 'LocationReviewTests': 26, 'PrivateServiceOperations': 5}
assert result['tests_run'] == len(result['test_cases']) == 74 and result['database_empty_after'] is True
expected_hashes = {module: hashlib.sha256((Path(sys.argv[4]) / 'tests' / f'{module}.py').read_bytes()).hexdigest()
                   for module in ('test_service_operations', 'test_listing_room_tour', 'test_location_review', 'test_due_work_reminders', 'test_maintenance_reminders', 'test_service_favorites', 'test_service_rebooking', 'test_service_review', 'test_service_negotiation', 'test_service_conversation', 'test_service_visit')}
assert result['test_source_sha256'] == expected_hashes
assert all(result[key] == 0 for key in ('tests_failed','tests_errored','tests_skipped',
    'tests_expected_failure','tests_unexpected_success','external_transport_attempts'))
PY
[[ "$test_exit" == 0 && "$test_state" == '0 false false' ]] || die NATIVE_TEST_FAILED
[[ -z $(git -C "$app" status --porcelain=v1 --untracked-files=all) ]] || die APPLICATION_SOURCE_CHANGED
printf 'INTEGRATED_PROPERTY_SERVICES_ACCEPTANCE commit=%s tree=%s run=%s attempt=1\n' "$app_commit" "$app_tree" "$GITHUB_RUN_ID"

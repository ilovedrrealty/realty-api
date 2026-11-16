#!/usr/bin/env bash
# Rehearse the exact deployed-to-candidate migration in disposable Postgres.
set -euo pipefail
die() { printf 'CUMULATIVE_MIGRATION_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ $# == 4 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api ]] || die CI_REQUIRED
[[ ${GITHUB_REF:-} == refs/heads/codex-ci-cumulative-network-migration-20261116 && ${GITHUB_RUN_ATTEMPT:-} == 1 ]] || die EXACT_EVENT_REQUIRED
[[ ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die HOSTED_LINUX_REQUIRED
ops="$1"; app="$2"; baseline="$3"; recipe="$4"
[[ "$ops" == "$GITHUB_WORKSPACE/migration-ops" && "$app" == "$GITHUB_WORKSPACE/migration-app" &&
   "$baseline" == "$GITHUB_WORKSPACE/migration-baseline" && "$recipe" == "$GITHUB_WORKSPACE/migration-recipe" ]] || die EXACT_CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$app" "$baseline" "$recipe"; do
  [[ $(realpath "$checkout") == "$checkout" && -z $(git -C "$checkout" status --porcelain=v1 --untracked-files=all) ]] \
    || die EXACT_CLEAN_CHECKOUTS_REQUIRED
done
app_commit=$(git -C "$app" rev-parse HEAD)
app_tree=$(git -C "$app" rev-parse 'HEAD^{tree}')
[[ $app_commit == b38d477c0e2e50f87f1f2e4cc1ceb3c707af92c5 && $app_tree == a53b3f829f70717069f5c637c826fb8f92c02f7c ]] || die EXACT_PRODUCT_REQUIRED
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" && $(git -C "$ops" rev-parse HEAD^) == "$app_commit" ]] \
  || die EXACT_APPLICATION_PARENT_REQUIRED
[[ $(git -C "$recipe" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 ]] || die EXACT_IMAGE_RECIPE_REQUIRED
baseline_commit=$(git -C "$baseline" rev-parse HEAD)
baseline_tree=$(git -C "$baseline" rev-parse 'HEAD^{tree}')
[[ $baseline_commit == 0811946ea02a310b03116960663e3ba6871a07d0 && $baseline_tree == 18b2087a31853a275c5c6ca791a68214b1cd38ef ]] \
  || die EXACT_BASELINE_REQUIRED
[[ $(git -C "$ops" show -s --format=%P HEAD) == "$app_commit" ]] || die SOLE_APPLICATION_PARENT_REQUIRED
for source in "$app" "$baseline"; do
  [[ ! -e "$source/.env" && ! -L "$source/.env" ]] || die APP_ENV_FILE_REFUSED
  git -C "$source" show HEAD:Dockerfile | grep -Fqx 'CMD ["sh", "-c", "python bootstrap_db.py && alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]' \
    || die PRODUCTION_STARTUP_CONTRACT_CHANGED
done
dockerfile="$recipe/ci/migration/services163.Dockerfile"
printf '%s  %s\n' e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9 "$dockerfile" \
  | sha256sum --check --status || die IMAGE_RECIPE_HASH_CHANGED
helper="$ops/ci/cumulative-network-migration"
[[ -f "$helper/run-cumulative-migration.py" && ! -L "$helper/run-cumulative-migration.py" ]] || die TEST_HELPER_REQUIRED

umask 077
[[ -n ${RUNNER_TEMP:-} && -d "$RUNNER_TEMP" && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die RUNNER_SCRATCH_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-cumulative-network-migration.XXXXXXXX")
nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_RUN_NONCE_REQUIRED
run_key="$GITHUB_RUN_ID-$nonce"
label=org.ilovedrrealty.cumulative-migration
image="ildr-cumulative-network-migration:$run_key"
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
  if [[ "$state" == "$RUNNER_TEMP"/ildr-cumulative-network-migration.* && -d "$state" && ! -L "$state" && $(realpath "$state") == "$state" ]]; then
    rm -rf -- "$state" || clean=0
  else
    clean=0
  fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'CUMULATIVE_MIGRATION_OWNED_CLEANUP commit=%s run=%s attempt=1\n' "$app_commit" "$GITHUB_RUN_ID"
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
  -c "COMMENT ON DATABASE $database IS 'ildr-cumulative-migration-ci:$nonce'" >/dev/null || die DATABASE_MARKER_REQUIRED
test_container=$(bounded 25s docker create --label "$label=$run_key" --network "container:$pg" --user "$(id -u):$(id -g)" \
  --read-only --init --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 192 --cpus 2 \
  --memory 1536m --memory-swap 1536m --tmpfs /tmp:rw,exec,size=128m \
  --env GITHUB_ACTIONS=true --env GITHUB_REPOSITORY=ilovedrrealty/realty-api \
  --env "ILDR_CUMULATIVE_MIGRATION_NONCE=$nonce" \
  --mount "type=bind,src=$app,dst=/application,readonly" --mount "type=bind,src=$baseline,dst=/baseline,readonly" \
  --mount "type=bind,src=$helper,dst=/tools,readonly" --mount "type=bind,src=$recipe/ci/migration,dst=/prior,readonly" \
  "$image" /usr/local/bin/python -I -B /tools/run-cumulative-migration.py) || die TEST_CONTAINER_CREATE_FAILED
owned "$test_container" || die OWNED_TEST_CONTAINER_REQUIRED
test_exit=0
bounded 450s docker start --attach "$test_container" >"$state/test.log" 2>&1 || test_exit=$?
test_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$test_container")
[[ "$test_state" =~ ^[0-9]+\ (true|false)\ (true|false)$ ]] || die NATIVE_TEST_STATE_REQUIRED
printf 'CUMULATIVE_MIGRATION_NATIVE client=%s state=%s\n' "$test_exit" "$test_state"
python3 -I -B - "$state/test.log" "$app_commit" "$app_tree" "$baseline_commit" "$baseline_tree" <<'PY'
from pathlib import Path
import json
import re
import sys

path = Path(sys.argv[1])
assert path.stat().st_size <= 16384
prefix = b'CUMULATIVE_MIGRATION_RESULT '
rows = [json.loads(line[len(prefix):]) for line in path.read_bytes().splitlines() if line.startswith(prefix)]
assert len(rows) == 1
report = rows[0]
assert isinstance(report, dict) and len(report) <= 27
assert all(re.fullmatch(r'[a-z0-9_]{1,50}', key) and
           (type(value) in (bool, int) or isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_]{1,80}', value))
           for key, value in report.items())
print(prefix.decode() + json.dumps(report, sort_keys=True, separators=(',', ':')))
assert report['status'] == 'PASS'
for key, expected in zip(('application_commit', 'application_tree', 'baseline_commit', 'baseline_tree'), sys.argv[2:]):
    assert report[key] == expected
assert report['postgres_major'] == 17 and report['baseline_revisions'] == 45 and report['application_revisions'] == 62
assert report['baseline_head'] == '52b2edc810ad' and report['application_head'] == 'f4b27d6e913c'
assert report['migration_delta'] == 17 and report['private_table_count'] == 40
assert report['private_draft_profile_count'] == 46 and report['historical_lead_count'] == 1
assert all(report[name] is True for name in ('legacy_rows_preserved', 'legacy_schema_preserved',
    'room_tour_null', 'new_schema_matches', 'database_catalog_matches', 'nonvacuous_grant_fixture',
    'no_outbox_backfill', 'startup_idempotent', 'database_cleanup'))
assert report['external_transport_attempts'] == 0
PY
[[ "$test_exit" == 0 && "$test_state" == '0 false false' ]] || die NATIVE_TEST_FAILED
for source in "$app" "$baseline" "$recipe"; do
  [[ -z $(git -C "$source" status --porcelain=v1 --untracked-files=all) ]] || die MOUNTED_SOURCE_CHANGED
done
printf 'CUMULATIVE_MIGRATION_ACCEPTANCE commit=%s tree=%s run=%s attempt=1\n' "$app_commit" "$app_tree" "$GITHUB_RUN_ID"

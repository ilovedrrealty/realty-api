#!/usr/bin/env bash
set -euo pipefail
die() { printf 'CUSTOMER_FOLLOWUP_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ $# == 6 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api ]] || die CI_REQUIRED
[[ ${GITHUB_REF:-} == refs/heads/codex-ci-network-live-core-acl-diagnostic-20261118 && ${GITHUB_RUN_ATTEMPT:-} == 1 &&
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
   $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" && $(git -C "$ops" show -s --format=%P HEAD) == 2fc16dcaf16d4634648d44314f2d4c88970f026f &&
   ! -e "$application/.env" && ! -L "$application/.env" ]] || die EXACT_IMMUTABLE_PRODUCT_REQUIRED
# The successor scopes one historical migration fixture. Product and deployed
# migration blobs retain the reviewed 081868 behavior; both test repairs are explicit.
[[ $(git -C "$application" show -s --format=%P HEAD) == 796f25ff52724db56313916e129df4b252a682fd &&
   $(git -C "$application" diff --name-only HEAD^ HEAD) == tests/test_service_workflow_migration.py &&
   $(git -C "$application" diff --numstat HEAD^ HEAD) == $'7\t2\ttests/test_service_workflow_migration.py' &&
   $(git -C "$application" diff --name-only 081868373f318498ddaa835de6496aa6e62d6f57 HEAD) == $'tests/test_professionals.py\ntests/test_service_workflow_migration.py' ]] ||
  die EXACT_TEST_ONLY_SOURCE_SUCCESSOR_REQUIRED
[[ $(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) == $'.github/workflows/network-live-core-native.yml\nci/network-live-core/run-hosted-customer-followup.sh\nci/network-live-core/verify-live-delivery-schema.py' ]] ||
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

# Diagnostic-only branch. The314/37 receipt is retained from V5; this
# operation runs fresh metadata inspection and makes no release acceptance claim.
schema_container=$(bounded 25s docker create --label "$label=$run_key" --network "container:$pg" \
  --user "$(id -u):$(id -g)" --read-only --init --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 192 --cpus 2 --memory 1536m --memory-swap 1536m --tmpfs /tmp:rw,exec,size=128m \
  --env GITHUB_ACTIONS=true --env GITHUB_REPOSITORY=ilovedrrealty/realty-api --env "ILDR_BROWSER_CI_NONCE=$nonce" \
  --mount "type=bind,src=$application,dst=/application,readonly" \
  --mount "type=bind,src=$live,dst=/live,readonly" \
  --mount "type=bind,src=$state/rollback,dst=/rollback,readonly" \
  --mount "type=bind,src=$tools,dst=/tools,readonly" \
  "$image" /usr/local/bin/python -I -B /tools/verify-live-delivery-schema.py acl) || die SCHEMA_CONTAINER_CREATE_FAILED
owned "$schema_container" || die OWNED_SCHEMA_CONTAINER_REQUIRED
schema_exit=0
bounded 720s docker start --attach "$schema_container" >"$state/schema.log" 2>&1 || schema_exit=$?
schema_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$schema_container")
[[ "$schema_state" =~ ^[0-9]+\ (true|false)\ (true|false)$ ]] || die SCHEMA_NATIVE_STATE_REQUIRED
printf 'LIVE_DELIVERY_SCHEMA_NATIVE client=%s state=%s\n' "$schema_exit" "$schema_state"
python3 -I -B - "$state/schema.log" <<'PY_ACL'
from pathlib import Path
import json, re, sys
path = Path(sys.argv[1]); assert path.stat().st_size <= 262144
prefix = b'NETWORK_PRIVATE_ACL_DIAGNOSTICS '
rows = [json.loads(line[len(prefix):]) for line in path.read_bytes().splitlines() if line.startswith(prefix)]
assert len(rows) == 1
diagnostic = rows[0]
assert set(diagnostic) == {'scope','alembic_head','public_table_count','new_private_table_count',
    'row_security','direct_grants','policies','synthetic_roles'}
assert diagnostic['scope'] == 'synthetic_fresh_private_acl_only'
assert diagnostic['alembic_head'] == 'b19d8e4c720a'
assert diagnostic['public_table_count'] == 92 and diagnostic['new_private_table_count'] == 67
assert len(diagnostic['row_security']) == 67
names = set()
for row in diagnostic['row_security']:
    assert set(row) == {'table','enabled','forced'} and re.fullmatch(r'[a-z_]{1,100}', row['table'])
    assert type(row['enabled']) is bool and type(row['forced']) is bool
    names.add(row['table'])
assert len(names) == 67 and len(diagnostic['direct_grants']) <= 2000
for row in diagnostic['direct_grants']:
    assert set(row) == {'table','role','privilege','grantable'} and row['table'] in names
    assert row['role'] in {'PUBLIC','anon','authenticated','service_role'}
    assert row['privilege'] in {'SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER','MAINTAIN'}
    assert type(row['grantable']) is bool
for row in diagnostic['policies']:
    assert set(row) == {'table','name'} and row['table'] in names and re.fullmatch(r'[a-zA-Z0-9_]{1,100}', row['name'])
assert len(diagnostic['synthetic_roles']) == 3
for row in diagnostic['synthetic_roles']:
    assert set(row) == {'role','login','superuser','inherit','bypass_rls'}
    assert row['role'] in {'anon','authenticated','service_role'}
    assert all(type(row[key]) is bool for key in ('login','superuser','inherit','bypass_rls'))
print(prefix.decode() + json.dumps(diagnostic, sort_keys=True, separators=(',', ':')))
result_prefix = b'LIVE_DELIVERY_SCHEMA_RESULT '
reports = [json.loads(line[len(result_prefix):]) for line in path.read_bytes().splitlines() if line.startswith(result_prefix)]
assert len(reports) == 1
report = reports[0]
if report.get('status') == 'FAIL':
    assert set(report) <= {'status','stage','error_type','reason'}
    assert all(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_]{1,100}',value) for value in report.values())
else:
    assert report == {'status':'PASS','scope':'fresh_acl_only','alembic_head':'b19d8e4c720a',
        'public_table_count':92,'new_private_table_count':67}
print(result_prefix.decode() + json.dumps(report, sort_keys=True, separators=(',', ':')))
assert report['status'] == 'PASS'
PY_ACL
[[ "$schema_exit" == 0 && "$schema_state" == '0 false false' ]] || die FRESH_PRIVATE_ACL_STRICT_CHECK_FAILED
printf 'NETWORK_PRIVATE_ACL_DIAGNOSTIC_ONLY_COMPLETE ops=%s run=%s source=%s release_acceptance=false\n' \
  "$GITHUB_SHA" "$GITHUB_RUN_ID" "$app_commit"

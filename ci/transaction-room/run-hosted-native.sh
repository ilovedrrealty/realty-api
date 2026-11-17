#!/usr/bin/env bash
# No application or provider network. Only dependencies prepared before the sandbox use public registries.
set -euo pipefail

die() { printf 'TRANSACTION_ROOM_NATIVE_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 &&
   ${GITHUB_RUN_ATTEMPT:-} == 1 && ${GITHUB_REF:-} == refs/heads/codex-ci-private-service-referrals-20261117-v3 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die EXACT_HOSTED_EVENT_REQUIRED

ops="$GITHUB_WORKSPACE/rehearsal-ops"
backend="$GITHUB_WORKSPACE/transaction-backend"
baseline="$GITHUB_WORKSPACE/previous-backend"
relay="$GITHUB_WORKSPACE/migration-relay"
candidate_commit=0882f3c3d87aea03917fb8e155b9ec8ecc5cc7b8
candidate_tree=32cc38f5d4331c0daf5b629e01f0e5c51833ea93
baseline_commit=630f05b80384428bb1191d3475a765877def80a9
baseline_tree=7d491000a969029df024b908f85a857fd9b88b89

for checkout in "$ops" "$backend" "$baseline" "$relay"; do
  working_status=$(git -C "$checkout" status --porcelain=v1 --untracked-files=all) || die CLEAN_CHECKOUT_REQUIRED
  [[ $(realpath "$checkout") == "$checkout" && -z "$working_status" &&
     ! -e "$checkout/.env" && ! -L "$checkout/.env" ]] || die CLEAN_CHECKOUT_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" rev-parse 'HEAD^') == 723887de237b5834e90a9ea69291d61ffb90ad81 ]] || die EXACT_OPERATIONS_REQUIRED
operations_delta=$(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) || die EXACT_OPERATIONS_REQUIRED
[[ "$operations_delta" == $'.github/workflows/transaction-room-native.yml\nci/transaction-room/run-hosted-native.sh' ]] ||
  die EXACT_OPERATIONS_REQUIRED
[[ $(git -C "$backend" rev-parse HEAD) == "$candidate_commit" &&
   $(git -C "$backend" rev-parse 'HEAD^{tree}') == "$candidate_tree" &&
   $(git -C "$baseline" rev-parse HEAD) == "$baseline_commit" &&
   $(git -C "$baseline" rev-parse 'HEAD^{tree}') == "$baseline_tree" &&
   $(git -C "$relay" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 ]] || die EXACT_APPLICATION_REQUIRED
cmp -s "$backend/requirements.txt" "$baseline/requirements.txt" || die BASELINE_REQUIREMENTS_MISMATCH
dockerfile="$relay/ci/migration/services163.Dockerfile"
printf '%s  %s\n' e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9 "$dockerfile" |
  sha256sum --check --status || die HISTORICAL_TOOL_HASH_MISMATCH

umask 077
[[ -d "$RUNNER_TEMP" && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die HOST_SCRATCH_REQUIRED
nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-transaction-room.XXXXXXXX")
run_key="$GITHUB_RUN_ID-$nonce"
label='org.ilovedrrealty.transaction-room.rehearsal'
app_image="ildr-transaction-room:$run_key"
python_digest='sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60'
python_image="docker.io/library/python:3.11-slim@$python_digest"
postgres_image='docker.io/library/postgres:17.6@sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409'

owned() {
  [[ "$1" =~ ^[a-f0-9]{64}$ &&
     $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$1") == "$run_key" ]]
}

cleanup() {
  local code=$? clean=1 id inventory images tagged_id
  trap - EXIT
  set +e
  inventory=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  # Remove test processes before their PostgreSQL namespace.
  for id in $inventory; do
    [[ "$id" != "${pg:-}" ]] || continue
    if owned "$id"; then
      bounded 20s docker stop --time 5 "$id" >/dev/null 2>&1 || true
      bounded 15s docker rm -f -v "$id" >/dev/null 2>&1 || clean=0
    else
      clean=0
    fi
  done
  if [[ -n ${pg:-} ]]; then
    if owned "$pg"; then
      bounded 20s docker stop --time 5 "$pg" >/dev/null 2>&1 || true
      bounded 15s docker rm -f -v "$pg" >/dev/null 2>&1 || clean=0
    else
      clean=0
    fi
  fi
  images=$(bounded 15s docker image ls -q --no-trunc --filter "label=$label=$run_key" | sort -u) || clean=0
  if [[ -n "$images" ]]; then
    tagged_id=$(bounded 15s docker image inspect --format '{{.Id}}' "$app_image") || clean=0
    if [[ "$images" =~ ^sha256:[a-f0-9]{64}$ && "$images" == "$tagged_id" ]]; then
      bounded 15s docker image rm "$app_image" >/dev/null 2>&1 || clean=0
    else
      clean=0
    fi
  fi
  inventory=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  images=$(bounded 15s docker image ls -q --no-trunc --filter "label=$label=$run_key") || clean=0
  [[ -z "$inventory" && -z "$images" ]] || clean=0
  if [[ "$state" == "$RUNNER_TEMP"/ildr-transaction-room.* && -d "$state" &&
        ! -L "$state" && $(realpath "$state") == "$state" ]]; then
    rm -rf -- "$state" || clean=0
  else
    clean=0
  fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'TRANSACTION_ROOM_NATIVE_CLEANUP ops=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT

mkdir "$state/context"
cp "$backend/requirements.txt" "$state/context/requirements.txt"
for image in "$python_image" "$postgres_image"; do
  bounded 180s docker pull --platform linux/amd64 "$image" >"$state/pull.log" 2>&1 || die PINNED_IMAGE_PULL_FAILED
  [[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image") == linux/amd64 ]] ||
    die EXACT_IMAGE_PLATFORM_REQUIRED
done
bounded 600s docker build --platform linux/amd64 --pull=false --quiet --label "$label=$run_key" \
  --build-arg "PYTHON_DIGEST=$python_digest" --file "$dockerfile" --tag "$app_image" "$state/context" \
  >"$state/build.log" 2>&1 || die PYTHON_DEPENDENCY_BUILD_FAILED

pg=$(bounded 25s docker create --label "$label=$run_key" --network none \
  --security-opt no-new-privileges:true --memory 768m --memory-swap 768m --pids-limit 96 --cpus 1 \
  --tmpfs /var/lib/postgresql/data:rw,size=384m --env POSTGRES_HOST_AUTH_METHOD=trust \
  "$postgres_image" postgres -p 55449 -h 127.0.0.1) || die POSTGRES_CREATE_FAILED
owned "$pg" && [[ $(bounded 15s docker inspect --format '{{.HostConfig.NetworkMode}}' "$pg") == none ]] ||
  die OWNED_ISOLATED_POSTGRES_REQUIRED
bounded 25s docker start "$pg" >/dev/null || die POSTGRES_START_FAILED
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  if bounded 3s docker exec "$pg" pg_isready -h 127.0.0.1 -p 55449 -U postgres -d postgres >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
[[ "$ready" == 1 && $(bounded 5s docker exec "$pg" ls /sys/class/net) == lo ]] || die LOOPBACK_POSTGRES_REQUIRED
psql=(docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -v ON_ERROR_STOP=1)
bounded 10s "${psql[@]}" -d postgres \
  -c 'CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN' \
  >/dev/null || die SYNTHETIC_ROLES_REQUIRED
for db in "ildr_s03_fresh_$nonce" "ildr_s03_upgrade_$nonce" ildr_platform_test_20261025; do
  bounded 10s "${psql[@]}" -d postgres -c "CREATE DATABASE $db" \
    -c "COMMENT ON DATABASE $db IS 'ildr-s03-ci:$nonce'" >/dev/null || die DATABASE_IDENTITY_MARKER_FAILED
  bounded 10s "${psql[@]}" -d "$db" \
    -c 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO anon, authenticated, service_role' \
    >/dev/null || die SYNTHETIC_DEFAULT_ACL_REQUIRED
done

runtime=(--rm --label "$label=$run_key" --user "$(id -u):$(id -g)" --read-only --init
         --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 128 --cpus 2
         --memory 1g --memory-swap 1g --tmpfs /tmp:rw,size=64m
         --workdir /application --mount "type=bind,src=$ops/ci/transaction-room,dst=/runner,readonly")

phase() {
  local mode=$1 checkout=$2 commit=$3 tree=$4 network=$5 limit=$6 result=0
  bounded "$limit" docker run "${runtime[@]}" --network "$network" \
    --mount "type=bind,src=$checkout,dst=/application,readonly" "$app_image" \
    env -i PATH=/usr/local/bin:/usr/bin:/bin GITHUB_ACTIONS=true GITHUB_REPOSITORY=ilovedrrealty/realty-api \
    /usr/local/bin/python -I -B /runner/rehearse.py "$mode" "$nonce" "$commit" "$tree" \
    >"$state/$mode.log" 2>&1 || result=$?
  python3 -I -B - "$state/$mode.log" "$mode" "$result" <<'PY'
from pathlib import Path
import re, sys

path = Path(sys.argv[1])
mode, native = sys.argv[2:]
raw = path.read_bytes() if path.stat().st_size <= 131072 else b""
stages = rb"(?:checkout|disposable_db|bootstrap|migrate_core|migrate_documents|migrate_referrals|catalog|tests|test_cleanup)"
pass_line = re.compile(rb"TRANSACTION_ROOM_NATIVE " + mode.encode() +
    rb" head=(?:[0-9a-f]{12}|none) private_tables=[0-9] shape=(?:[0-9a-f]{64}|none) tests=[0-9]{1,2} pass=[0-9]{1,2}")
error_line = re.compile(rb"TRANSACTION_ROOM_NATIVE_FAILURE " + mode.encode() + rb" stage=" + stages +
    rb" class=[A-Za-z_][A-Za-z0-9_]{0,62} sqlstate=(?:[A-Z0-9]{5}|none) methods=(?:[a-z0-9_,]{1,768}|none)")
lines = raw.splitlines()
safe = [line for line in lines if pass_line.fullmatch(line) or error_line.fullmatch(line)]
if len(safe) == 1:
    print(safe[0].decode('ascii'))
if mode == "referrals" and native != "0":
    assertion = re.compile(rb"REFERRAL_NATIVE_ASSERTION test=[a-z0-9_]{1,100} line=(?:[0-9]{1,4}|none)"
                           rb" actual=(?:[1-5][0-9]{2}|none) expected=(?:[1-5][0-9]{2}|none)")
    sqlstates = re.compile(rb"REFERRAL_NATIVE_SQLSTATES codes=(?:[A-Z0-9]{5}(?:,[A-Z0-9]{5}){0,7}|none)")
    for line in [item for item in lines if assertion.fullmatch(item) or sqlstates.fullmatch(item)][:6]:
        print(line.decode('ascii'))
if native != "0" or len(safe) != 1 or not pass_line.fullmatch(safe[0]):
    print(f"TRANSACTION_ROOM_NATIVE_PHASE_FAILURE mode={mode} native={native}")
    raise SystemExit(1)
PY
}

phase adapter "$backend" "$candidate_commit" "$candidate_tree" none 60s || die FAKE_ADAPTER_REQUIRED
phase core "$backend" "$candidate_commit" "$candidate_tree" "container:$pg" 100s || die CORE_TESTS_REQUIRED
phase documents "$backend" "$candidate_commit" "$candidate_tree" "container:$pg" 100s || die DOCUMENT_TESTS_REQUIRED
phase service "$backend" "$candidate_commit" "$candidate_tree" "container:$pg" 130s || die SERVICE_MATCHES_TESTS_REQUIRED
phase referrals "$backend" "$candidate_commit" "$candidate_tree" "container:$pg" 110s || die PRIVATE_REFERRAL_TESTS_REQUIRED
phase baseline "$baseline" "$baseline_commit" "$baseline_tree" "container:$pg" 100s || die BASELINE_MIGRATION_REQUIRED
phase fresh "$backend" "$candidate_commit" "$candidate_tree" "container:$pg" 100s || die FRESH_MIGRATION_REQUIRED
phase upgrade "$backend" "$candidate_commit" "$candidate_tree" "container:$pg" 100s || die FORWARD_MIGRATION_REQUIRED
python3 -I -B - "$state/fresh.log" "$state/upgrade.log" <<'PY' || die FRESH_UPGRADE_SCHEMA_DIVERGENCE
from pathlib import Path
import re, sys

shapes = [
    re.findall(rb"(?m)^TRANSACTION_ROOM_NATIVE (?:fresh|upgrade) head=e12ac7d49f03 private_tables=9 shape=([0-9a-f]{64}) tests=0 pass=0$",
               Path(name).read_bytes()) for name in sys.argv[1:]
]
assert len(shapes[0]) == 1 and shapes[0] == shapes[1]
PY
printf 'PRIVATE_SERVICE_REFERRALS_NATIVE_PASS ops=%s run=%s backend=%s tree=%s postgres=17.6 referrals=5 service=35 core=9 documents=3 adapter=7 head=e12ac7d49f03 storage=fake\n' \
  "$GITHUB_SHA" "$GITHUB_RUN_ID" "$candidate_commit" "$candidate_tree"

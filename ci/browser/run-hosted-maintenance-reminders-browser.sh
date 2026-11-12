#!/usr/bin/env bash
# Hosted-only build and browser exercise. The application and browser share an
# owned network namespace with just loopback. The build is never published.
set -euo pipefail

die() { printf 'BROWSER_REHEARSAL_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ $# == 5 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api ]] || die CI_REQUIRED
[[ ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 && ${GITHUB_RUN_ATTEMPT:-} == 1 ]] || die STANDARD_LINUX_REQUIRED
[[ ${GITHUB_REF:-} == refs/heads/codex-ci-maintenance-reminders-browser-20261112 && ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die EXACT_EVENT_REQUIRED

ops="$1"; relay="$2"; frontend="$3"; backend="$4"; migration="$5"
[[ "$ops" == "$GITHUB_WORKSPACE/browser-ops" && "$relay" == "$GITHUB_WORKSPACE/frontend-relay" && "$frontend" == "$GITHUB_WORKSPACE/frontend-base" && "$backend" == "$GITHUB_WORKSPACE/browser-backend" && "$migration" == "$GITHUB_WORKSPACE/migration-relay" ]] || die CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$relay" "$frontend" "$backend" "$migration"; do
  checkout_status=$(git -C "$checkout" status --porcelain=v1 --untracked-files=all) || die CLEAN_CHECKOUT_REQUIRED
  [[ $(realpath "$checkout") == "$checkout" && -z "$checkout_status" ]] || die CLEAN_CHECKOUT_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" && $(git -C "$ops" rev-parse HEAD^) == 6165ca389c33ff795a53d185ab956c3ead20f0f0 ]] || die EXACT_OPERATIONS_PARENT_REQUIRED
[[ $(git -C "$relay" rev-parse HEAD) == 441efdc288c3a38b8607c816c684abec080d83ad ]] || die EXACT_FRONTEND_RELAY_REQUIRED
[[ $(git -C "$frontend" rev-parse HEAD) == 73d771940e73d42ed044cea77a5258a93a445b05 ]] || die EXACT_FRONTEND_BASE_REQUIRED
[[ $(git -C "$backend" rev-parse HEAD) == c12c7317553f7453eacd89697fc6982fe5c4171d && $(git -C "$backend" rev-parse 'HEAD^{tree}') == b34559d307141d117abd2a1cc8658b1db5f2fd5b ]] || die EXACT_API_SOURCE_REQUIRED
[[ $(git -C "$migration" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 ]] || die EXACT_ACCEPTED_PYTHON_BUILD_REQUIRED

tools="$ops/ci/browser"
frontend_tools="$relay/ci/frontend"
dockerfile="$migration/ci/migration/services163.Dockerfile"
for file in serve_service_visit_fixture.py serve_service_negotiation_fixture.py serve_service_review_fixture.py serve_maintenance_reminders_fixture.py serve_dashboard_fixture.mjs run-maintenance-reminders-browser.mjs reconstruct-service-visits-frontend.py reconstruct-service-conversation-frontend.py frontend-service-visits-delta.json frontend-service-conversation-delta.json reconstruct-service-negotiation-frontend.py frontend-service-negotiation-delta.json reconstruct-service-review-frontend.py frontend-service-review-delta.json reconstruct-service-rebooking-frontend.py frontend-service-rebooking-delta.json reconstruct-service-favorites-frontend.py frontend-service-favorites-delta.json reconstruct-maintenance-reminders-frontend.py frontend-maintenance-reminders-delta.json check-maintenance-reminders-browser-result.py; do
  [[ -f "$tools/$file" && ! -L "$tools/$file" ]] || die EXACT_FIXTURE_SOURCE_REQUIRED
done
printf '%s  %s\n' \
  741823a5585c1bd396d8a05b04a0cdb47c41c1fee680ee128fd9de535c1da8d1 "$frontend_tools/frontend_ci_host.py" \
  45d69ba856c72c4dd9b342e7084f0fb604957d617802b13d3d4613fc64c72891 "$frontend_tools/frontend-overlay.json" \
  34601a2c8eec8d56749b0664af0299835c755ae74a77c6a9b5d84e942c689db7 "$frontend_tools/run_frontend_ci.cjs" \
  e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9 "$dockerfile" \
  | sha256sum --check --status || die HISTORICAL_TOOL_HASH_MISMATCH

node_image='node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c'
python_digest='sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60'
python_image="docker.io/library/python:3.11-slim@$python_digest"
postgres_image='docker.io/library/postgres:17@sha256:3b0cda93be7c8dba2ed484d19393d93845dc3dafb88973e248ba8e6d99d784e1'
browser_image='mcr.microsoft.com/playwright@sha256:ee15712262f017b8157421b212b12099146645f44b1bc5c0514cbbe6a229b1d0'

umask 077
[[ -n ${RUNNER_TEMP:-} && -d "$RUNNER_TEMP" && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die HOST_SCRATCH_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-browser.XXXXXXXX")
nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
label='org.ilovedrrealty.browser.maintenance-reminders'
run_key="$GITHUB_RUN_ID-$nonce"
app_image="ildr-browser-api:$run_key"
uids="$(id -u):$(id -g)"

owned() {
  [[ "$1" =~ ^[a-f0-9]{64}$ && $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$1") == "$run_key" ]]
}

cleanup() {
  local code=$? clean=1 id owned_ids owned_images tagged_id
  trap - EXIT
  set +e
  owned_ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  for id in $owned_ids; do
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
  owned_images=$(bounded 15s docker image ls -q --no-trunc --filter "label=$label=$run_key" | sort -u) || clean=0
  if [[ -n "$owned_images" ]]; then
    tagged_id=$(bounded 15s docker image inspect --format '{{.Id}}' "$app_image") || clean=0
    if [[ "$owned_images" =~ ^sha256:[a-f0-9]{64}$ && "$owned_images" == "$tagged_id" ]]; then
      bounded 15s docker image rm "$app_image" >/dev/null 2>&1 || clean=0
    else
      clean=0
    fi
  fi
  owned_images=$(bounded 15s docker image ls -q --no-trunc --filter "label=$label=$run_key") || clean=0
  owned_ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  [[ -z "$owned_ids" && -z "$owned_images" ]] || clean=0
  if [[ "$state" == "$RUNNER_TEMP"/ildr-browser.* && -d "$state" && ! -L "$state" && $(realpath "$state") == "$state" ]]; then
    rm -rf -- "$state" || clean=0
  else
    clean=0
  fi
  if [[ "$clean" != 1 || -e "$state" ]]; then
    printf 'BROWSER_REHEARSAL_CLEANUP_UNVERIFIED\n' >&2
    exit 1
  fi
  printf 'BROWSER_REHEARSAL_OWNED_CLEANUP commit=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT

mkdir "$state/frontend-work" "$state/fixtures" "$state/browser-run" "$state/python-context"
cp "$backend/requirements.txt" "$state/python-context/requirements.txt"
cp "$tools/run-maintenance-reminders-browser.mjs" "$state/browser-run/"

# Public, pinned dependency preparation. No app or browser process is running yet.
for image in "$node_image" "$python_image" "$postgres_image" "$browser_image"; do
  bounded 180s docker pull --platform linux/amd64 "$image" >/dev/null || die PINNED_IMAGE_PULL_FAILED
  [[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image") == linux/amd64 ]] || die EXACT_IMAGE_PLATFORM_REQUIRED
done
bounded 600s docker build --platform linux/amd64 --pull=false --quiet --label "$label=$run_key" \
  --build-arg "PYTHON_DIGEST=$python_digest" --file "$dockerfile" --tag "$app_image" "$state/python-context" >"$state/python-build.log" 2>&1 || die API_IMAGE_BUILD_FAILED

python3 -I -B "$tools/reconstruct-service-visits-frontend.py" base "$tools" "$relay" "$frontend" "$state"

prepare=(--label "$label=$run_key" --user "$uids" --read-only --cap-drop ALL --security-opt no-new-privileges:true
         --memory 8g --memory-swap 8g --pids-limit 256 --cpus 2 --tmpfs /tmp:rw,exec,size=128m)
bounded 500s docker run "${prepare[@]}" --network bridge --workdir /work \
  --mount "type=bind,src=$state/frontend-work,dst=/work" \
  --mount "type=bind,src=$frontend,dst=/source,readonly" \
  --mount "type=bind,src=$frontend_tools,dst=/ci,readonly" \
  --env "FE_CI_COMMIT=$GITHUB_SHA" --env "FE_CI_RUN_ID=$GITHUB_RUN_ID" \
  "$node_image" node /ci/run_frontend_ci.cjs install >"$state/npm-install.log" 2>&1 || die LOCKED_FRONTEND_INSTALL_FAILED
python3 -I -B "$tools/reconstruct-service-visits-frontend.py" apply "$tools" "$relay" "$frontend" "$state"
python3 -I -B "$tools/reconstruct-service-conversation-frontend.py" apply "$tools" "$relay" "$frontend" "$state"
python3 -I -B "$tools/reconstruct-service-negotiation-frontend.py" apply "$tools" "$relay" "$frontend" "$state"
python3 -I -B "$tools/reconstruct-service-review-frontend.py" apply "$tools" "$relay" "$frontend" "$state"
python3 -I -B "$tools/reconstruct-service-rebooking-frontend.py" apply "$tools" "$relay" "$frontend" "$state"
python3 -I -B "$tools/reconstruct-service-favorites-frontend.py" apply "$tools" "$relay" "$frontend" "$state"
python3 -I -B "$tools/reconstruct-maintenance-reminders-frontend.py" apply "$tools" "$relay" "$frontend" "$state"
if ! bounded 360s docker run "${prepare[@]}" --network none --workdir /work \
  --mount "type=bind,src=$state/frontend-work,dst=/work" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1536 \
  npm_config_userconfig=/tmp/ildr-user.npmrc npm_config_globalconfig=/tmp/ildr-global.npmrc npm_config_cache=/tmp/npm \
  npm_config_update_notifier=false npm_config_audit=false npm_config_fund=false \
  VITE_API_URL=http://127.0.0.1:8000 VITE_LANDING_URL=http://127.0.0.1:8917 \
  VITE_GOOGLE_CLIENT_ID=synthetic-ci.apps.googleusercontent.com sh -c 'node --test --test-reporter=tap tests/service-conversation.test.cjs tests/service-review.test.cjs tests/service-rebook.test.cjs tests/service-favorites.test.cjs tests/maintenance-reminders.test.cjs && npm run build --workspace=dashboard' >"$state/dashboard-build.log" 2>&1; then
  python3 -I -B - "$state/dashboard-build.log" <<'PY'
import re
import sys
from pathlib import Path
with Path(sys.argv[1]).open('rb') as log:
    body = log.read(512 * 1024)
diagnostics = re.findall(rb'(?m)^(src/[a-zA-Z0-9_./-]+\.tsx?)\(([0-9]+),([0-9]+)\): error (TS[0-9]+):', body)
for source, line, column, code in diagnostics[:20]:
    print(f'BROWSER_BUILD_DIAGNOSTIC {source.decode()}:{line.decode()}:{column.decode()} {code.decode()}')
PY
  die DASHBOARD_BUILD_FAILED
fi

python3 -I -B - "$state/dashboard-build.log" <<'PY'
import sys
from pathlib import Path
lines = Path(sys.argv[1]).read_bytes().splitlines()
for row in (b'# tests 28', b'# pass 28', b'# fail 0', b'# skipped 0'):
    assert lines.count(row) == 1
print('MAINTENANCE_REMINDERS_FRONTEND_UNIT native=0 tests=28 pass=28')
PY
python3 -I -B "$tools/reconstruct-maintenance-reminders-frontend.py" verify "$tools" "$relay" "$frontend" "$state"

python3 -I -B - "$state/frontend-work" "$state/browser-run" <<'PY'
import base64, hashlib, io, json, tarfile, urllib.request
from pathlib import Path, PurePosixPath
import sys
workspace, browser = map(Path, sys.argv[1:])
dist = workspace/'apps/dashboard/dist'
assert (dist/'index.html').is_file() and not (dist/'index.html').is_symlink()
assets = [f.read_bytes() for f in (dist/'assets').glob('*.js') if f.is_file() and not f.is_symlink()]
assert any(b'http://127.0.0.1:8000' in body for body in assets)
assert any(b'http://127.0.0.1:8917' in body for body in assets)
url = 'https://registry.npmjs.org/playwright-core/-/playwright-core-1.64.1.tgz'
with urllib.request.urlopen(url, timeout=30) as response:
    assert response.geturl() == url
    raw = response.read(20 * 1024 * 1024 + 1)
assert 0 < len(raw) <= 20 * 1024 * 1024
assert base64.b64encode(hashlib.sha512(raw).digest()).decode() == 'BC4s8y6SEVA6Q1rSxZTto8hI1KUI3xXlHGZgvGqP4IqPdI/a6FX9H29IDNz7vKo9l21b6LfYosScPL6M3xxyDQ=='
target = browser/'node_modules/playwright-core'
target.mkdir(parents=True)
with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
    members = archive.getmembers()
    assert len(members) <= 6000 and sum(member.size for member in members) <= 90 * 1024 * 1024
    for member in members:
        parts = PurePosixPath(member.name).parts
        assert parts and parts[0] == 'package' and '..' not in parts and (member.isdir() or member.isfile())
        path = target.joinpath(*parts[1:])
        if member.isdir():
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as out:
                out.write(archive.extractfile(member).read())
assert json.loads((target/'package.json').read_bytes())['version'] == '1.64.1'
PY

# The database owns an isolated network namespace containing only loopback.
db="ildr_browser_ci_$nonce"
pg=$(bounded 25s docker create --label "$label=$run_key" --network none \
  --security-opt no-new-privileges:true --memory 768m --memory-swap 768m --pids-limit 96 --cpus 1 \
  --tmpfs /var/lib/postgresql/data:rw,size=384m \
  --env POSTGRES_HOST_AUTH_METHOD=trust --env "POSTGRES_DB=$db" \
  "$postgres_image" postgres -p 55449 -h 127.0.0.1) || die POSTGRES_CREATE_FAILED
owned "$pg" && [[ $(bounded 15s docker inspect --format '{{.HostConfig.NetworkMode}}' "$pg") == none ]] || die OWNED_ISOLATED_POSTGRES_REQUIRED
bounded 25s docker start "$pg" >/dev/null || die POSTGRES_START_FAILED
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  if bounded 3s docker exec "$pg" pg_isready -h 127.0.0.1 -p 55449 -U postgres -d "$db" >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 && $(bounded 5s docker exec "$pg" ls /sys/class/net) == lo ]] || die LOOPBACK_POSTGRES_REQUIRED
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d "$db" -v ON_ERROR_STOP=1 \
  -c "COMMENT ON DATABASE $db IS 'ildr-browser-ci:$nonce'" >/dev/null || die DATABASE_IDENTITY_MARKER_FAILED

runtime=(--label "$label=$run_key" --network "container:$pg" --user "$uids" --read-only --init
         --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 256 --cpus 2
         --env GITHUB_ACTIONS=true --env GITHUB_REPOSITORY=ilovedrrealty/realty-api)
api=$(bounded 25s docker create "${runtime[@]}" --memory 2g --memory-swap 2g \
  --tmpfs /tmp:rw,exec,size=128m --env "ILDR_BROWSER_CI_NONCE=$nonce" \
  --env "ILDR_MAINTENANCE_APP_COMMIT=$(git -C "$backend" rev-parse HEAD)" \
  --env "ILDR_MAINTENANCE_APP_TREE=$(git -C "$backend" rev-parse 'HEAD^{tree}')" \
  --env ILDR_MAINTENANCE_MIGRATION_HEAD=e2b8c4a6d903 \
  --mount "type=bind,src=$backend,dst=/application,readonly" \
  --mount "type=bind,src=$tools,dst=/tools,readonly" \
  --mount "type=bind,src=$state/fixtures,dst=/browser-fixtures" \
  "$app_image" /usr/local/bin/python -I -B /tools/serve_maintenance_reminders_fixture.py) || die API_CREATE_FAILED
owned "$api" && bounded 25s docker start "$api" >/dev/null || die API_START_FAILED
dashboard=$(bounded 25s docker create "${runtime[@]}" --memory 512m --memory-swap 512m \
  --mount "type=bind,src=$state/frontend-work/apps/dashboard/dist,dst=/dashboard-dist,readonly" \
  --mount "type=bind,src=$tools,dst=/tools,readonly" \
  "$node_image" node /tools/serve_dashboard_fixture.mjs) || die DASHBOARD_CREATE_FAILED
owned "$dashboard" && bounded 25s docker start "$dashboard" >/dev/null || die DASHBOARD_START_FAILED

ready=0
for ((attempt=0; attempt<90; attempt++)); do
  if [[ -s "$state/fixtures/fixtures.json" ]] && bounded 4s docker exec "$api" /usr/local/bin/python -I -B -c \
    'from urllib.request import urlopen; assert all(urlopen(url,timeout=1).status==200 for url in ("http://127.0.0.1:8000/health","http://127.0.0.1:4175/"))' >/dev/null 2>&1; then ready=1; break; fi
  if [[ $(bounded 5s docker inspect --format '{{.State.Running}}' "$api") != true ]]; then
    if bounded 5s docker logs --tail 120 "$api" >"$state/api-start.log" 2>&1; then
      python3 -I -B - "$state/api-start.log" <<'PY'
import json,re,sys
from pathlib import Path
log=Path(sys.argv[1]); prefix=b'BROWSER_API_FIXTURE_FAILURE '
assert log.stat().st_size <= 131072
reports=[line[len(prefix):] for line in log.read_bytes().splitlines() if line.startswith(prefix)]
assert len(reports)<=1
if reports:
    value=json.loads(reports[0]); assert set(value)=={'stage','error_type','fixture_line'}
    assert value['stage'] in {'checkout','claim_database','configure_application','load_application','bootstrap_database',
        'apply_alembic','seed_accounts','seed_service_award','seed_maintenance_sources','write_fixture','serve_application','verify_maintenance_reminders'}
    assert re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}',value['error_type'])
    assert value['fixture_line'] is None or type(value['fixture_line']) is int and 0<value['fixture_line']<=1000
    print(prefix.decode()+json.dumps(value,sort_keys=True,separators=(',',':')))
PY
    fi
    die API_FIXTURE_EXITED_DURING_START
  fi
  sleep 1
done
[[ "$ready" == 1 && $(stat -c %a "$state/fixtures/fixtures.json") == 600 ]] || die PRIVATE_SYNTHETIC_FIXTURE_NOT_READY

browser=$(bounded 25s docker create "${runtime[@]}" --memory 2g --memory-swap 2g --shm-size 384m \
  --tmpfs /tmp:rw,exec,size=192m --env HOME=/tmp \
  --env PLAYWRIGHT_BROWSERS_PATH=/ms-playwright --env ILDR_BROWSER_FIXTURES=/browser-fixtures/fixtures.json \
  --mount "type=bind,src=$state/browser-run,dst=/rehearsal,readonly" \
  --mount "type=bind,src=$state/fixtures,dst=/browser-fixtures,readonly" \
  "$browser_image" node /rehearsal/run-maintenance-reminders-browser.mjs) || die BROWSER_CREATE_FAILED
owned "$browser" || die OWNED_BROWSER_REQUIRED
client_exit=0
bounded 310s docker start --attach "$browser" >"$state/browser.log" 2>&1 || client_exit=$?
container_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$browser")
[[ "$container_state" =~ ^[0-9]+\ (true|false)\ (true|false)$ ]] || die BROWSER_NATIVE_STATE_REQUIRED
printf 'BROWSER_REHEARSAL_NATIVE client=%s state=%s\n' "$client_exit" "$container_state"

python3 -I -B "$tools/check-maintenance-reminders-browser-result.py" browser "$state/browser.log"
[[ "$client_exit" == 0 && "$container_state" == '0 false false' ]] || die NATIVE_BROWSER_FAILED

bounded 20s docker stop --time 10 "$api" >/dev/null || die API_STOP_FAILED
api_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$api") || die API_SHUTDOWN_STATE_REQUIRED
bounded 5s docker logs --tail 120 "$api" >"$state/api-stop.log" 2>&1 || die API_READBACK_LOG_REQUIRED
python3 -I -B "$tools/check-maintenance-reminders-browser-result.py" fixture "$state/browser.log" "$state/api-stop.log"
[[ "$api_state" == '0 false false' ]] || die API_GRACEFUL_SHUTDOWN_REQUIRED
[[ ! -e "$state/fixtures/fixtures.json" ]] || die SYNTHETIC_LOGIN_FILE_CLEANUP_REQUIRED
printf 'BROWSER_API_FIXTURE_SHUTDOWN state=%s login_file=absent\n' "$api_state"
backend_status=$(git -C "$backend" status --porcelain=v1 --untracked-files=all) || die API_SOURCE_CHANGED
[[ -z "$backend_status" ]] || die API_SOURCE_CHANGED
printf 'MAINTENANCE_REMINDERS_BROWSER_ACCEPTANCE commit=%s frontend=%s api=%s run=%s attempt=1\n' \
  "$GITHUB_SHA" 7d868765663f7205269aba64753431572d18060f c12c7317553f7453eacd89697fc6982fe5c4171d "$GITHUB_RUN_ID"

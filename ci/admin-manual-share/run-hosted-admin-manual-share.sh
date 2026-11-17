#!/usr/bin/env bash
# Hosted-only build and browser exercise. The application and browser share an
# owned network namespace with just loopback. The build is never published.
set -euo pipefail

die() { printf 'BROWSER_REHEARSAL_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ $# == 4 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api ]] || die CI_REQUIRED
[[ ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 && ${GITHUB_RUN_ATTEMPT:-} == 1 ]] || die STANDARD_LINUX_REQUIRED
[[ ${GITHUB_REF:-} == refs/heads/codex-purchase-cost-worksheet-browser-ci-20261117 && ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die EXACT_EVENT_REQUIRED

ops="$1"; frontend="$2"; backend="$3"; migration="$4"
[[ "$ops" == "$GITHUB_WORKSPACE/browser-ops" && "$frontend" == "$GITHUB_WORKSPACE/frontend-base" &&
   "$backend" == "$GITHUB_WORKSPACE/browser-backend" && "$migration" == "$GITHUB_WORKSPACE/migration-relay" ]] ||
  die CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$frontend" "$backend" "$migration"; do
  checkout_status=$(git -C "$checkout" status --porcelain=v1 --untracked-files=all) || die CLEAN_CHECKOUT_REQUIRED
  [[ $(realpath "$checkout") == "$checkout" && -z "$checkout_status" ]] || die CLEAN_CHECKOUT_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" && $(git -C "$ops" rev-parse HEAD^) == 704b56972597734903176c263f635ae2ec93ceeb ]] ||
  die EXACT_OPERATIONS_PARENT_REQUIRED
operations_delta=$(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) || die EXACT_OPERATIONS_REQUIRED
[[ "$operations_delta" == $'.github/workflows/admin-manual-share.yml\nci/admin-manual-share/run-hosted-admin-manual-share.sh\nci/admin-manual-share/run-purchase-costs.mjs\nci/admin-manual-share/serve_purchase_costs_fixture.py' ]] ||
  die EXACT_OPERATIONS_REQUIRED
frontend_commit=3726c2dc02c45dd87f719a3223a710320f187ecf
frontend_tree=272f60b774a652d5eacf0275bbc760db8712b760
backend_commit=d1a8e4f9ed5708e97bc3dcaa9f60f96543e60cbe
backend_tree=752efbde336a7f6fa8ec72b6108464e918caa22f
migration_head=e12ac7d49f03
[[ $(git -C "$frontend" rev-parse HEAD) == "$frontend_commit" &&
   $(git -C "$frontend" rev-parse 'HEAD^{tree}') == "$frontend_tree" ]] || die EXACT_FRONTEND_REQUIRED
[[ $(git -C "$backend" rev-parse HEAD) == "$backend_commit" &&
   $(git -C "$backend" rev-parse 'HEAD^{tree}') == "$backend_tree" ]] || die EXACT_BACKEND_REQUIRED
[[ $(git -C "$migration" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 ]] ||
  die EXACT_ACCEPTED_PYTHON_BUILD_REQUIRED

tools="$ops/ci/admin-manual-share"
dockerfile="$migration/ci/migration/services163.Dockerfile"
for file in serve_admin_share_fixture.py serve_dashboard_fixture.mjs serve_purchase_costs_fixture.py run-purchase-costs.mjs; do
  [[ -f "$tools/$file" && ! -L "$tools/$file" ]] || die EXACT_FIXTURE_SOURCE_REQUIRED
done
printf '%s  %s\n' e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9 "$dockerfile" \
  | sha256sum --check --status || die HISTORICAL_TOOL_HASH_MISMATCH

node_image='node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c'
python_digest='sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60'
python_image="docker.io/library/python:3.11-slim@$python_digest"
postgres_image='docker.io/library/postgres:17.6@sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409'
browser_image='mcr.microsoft.com/playwright@sha256:ee15712262f017b8157421b212b12099146645f44b1bc5c0514cbbe6a229b1d0'

umask 077
[[ -n ${RUNNER_TEMP:-} && -d "$RUNNER_TEMP" && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die HOST_SCRATCH_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-browser.XXXXXXXX")
nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
label='org.ilovedrrealty.browser.network-live-share'
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
cp "$tools/run-purchase-costs.mjs" "$state/browser-run/"

# Public, pinned dependency preparation. No app or browser process is running yet.
for image in "$node_image" "$python_image" "$postgres_image" "$browser_image"; do
  bounded 180s docker pull --platform linux/amd64 "$image" >/dev/null || die PINNED_IMAGE_PULL_FAILED
  [[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image") == linux/amd64 ]] || die EXACT_IMAGE_PLATFORM_REQUIRED
done
bounded 600s docker build --platform linux/amd64 --pull=false --quiet --label "$label=$run_key" \
  --build-arg "PYTHON_DIGEST=$python_digest" --file "$dockerfile" --tag "$app_image" "$state/python-context" >"$state/python-build.log" 2>&1 || die API_IMAGE_BUILD_FAILED

git -C "$frontend" archive --format=tar HEAD | tar -xf - -C "$state/frontend-work" || die FRONTEND_ARCHIVE_FAILED
verify_frontend_files() {
  python3 -I -B - "$frontend" "$state/frontend-work" <<'PY'
from hashlib import sha1
from pathlib import Path
import subprocess, sys
source, copy = map(Path, sys.argv[1:])
files = subprocess.check_output(['git', '-C', str(source), 'ls-tree', '-rz', 'HEAD']).split(b'\0')
assert len(files) == 553 and not files[-1]  # 552 tracked files and the delimiter
for entry in filter(None, files):
    metadata, name = entry.split(b'\t', 1)
    mode, kind, object_id = metadata.split()
    path = copy / name.decode('utf-8')
    assert kind == b'blob' and mode in (b'100644', b'100755') and path.is_file() and not path.is_symlink()
    payload = path.read_bytes()
    assert sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest().encode() == object_id
PY
}
verify_frontend_files || die EXACT_ARCHIVED_FRONTEND_REQUIRED
printf '%s  %s\n' \
  8df3c4c580c65694bc727488fc0ec02d88ab1577e5fa16c509f4897b58e60f33 "$state/frontend-work/package-lock.json" \
  1126aeb69e32b34394cca03ac440848f7b88b339042496cb6722a1781a204337 "$state/frontend-work/apps/dashboard/package.json" \
  45893e6dfebaad4d2e73f7e7d6b26f176883bc1ba8c8dc025f188cc1b4d12344 "$state/frontend-work/apps/landing/package.json" \
  390997a9b438a9e134d4c5c0a375a284bc7e8657a13b076167438dfb728e702e "$state/frontend-work/tests/purchase-cost-worksheet.test.cjs" \
  | sha256sum --check --status || die EXACT_PACKAGE_AND_TEST_PINS_REQUIRED

prepare=(--label "$label=$run_key" --user "$uids" --read-only --cap-drop ALL --security-opt no-new-privileges:true
         --memory 8g --memory-swap 8g --pids-limit 256 --cpus 2 --tmpfs /tmp:rw,exec,size=128m)

install_exit=0
bounded 500s docker run "${prepare[@]}" --network bridge --workdir /work \
  --mount "type=bind,src=$state/frontend-work,dst=/work" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1024 \
  npm_config_registry=https://registry.npmjs.org/ npm_config_userconfig=/tmp/ildr-user.npmrc \
  npm_config_globalconfig=/tmp/ildr-global.npmrc npm_config_cache=/tmp/npm \
  npm_config_update_notifier=false npm_config_audit=false npm_config_fund=false \
  npm ci --ignore-scripts --no-audit --no-fund --include=dev --include=optional \
  >"$state/npm-install.log" 2>&1 || install_exit=$?
if [[ "$install_exit" != 0 ]]; then
  python3 -I -B - "$state/npm-install.log" "$install_exit" <<'PY'
from pathlib import Path
import os, re, sys

limit = 131072
native = int(sys.argv[2])
allowed = set(b'''EAI_AGAIN ENOTFOUND ETIMEDOUT ESOCKETTIMEDOUT ERR_SOCKET_TIMEOUT ECONNREFUSED ECONNRESET
    ENETUNREACH EHOSTUNREACH E401 E403 E404 E429 E500 E502 E503 E504 EUSAGE ERESOLVE EINTEGRITY
    ETARGET ELOCKVERIFY EACCES EPERM ENOSPC ENOMEM EPIPE UNABLE_TO_VERIFY_LEAF_SIGNATURE
    SELF_SIGNED_CERT_IN_CHAIN CERT_HAS_EXPIRED'''.split())
try:
    with Path(sys.argv[1]).open('rb') as log:
        log.seek(max(0, os.fstat(log.fileno()).st_size - limit))
        tail = log.read(limit)
except OSError:
    tail = b''
matches = re.findall(rb'(?m)^npm (?:error|ERR!) code ([A-Z][A-Z0-9_]{1,45})[ \t\r]*$', tail)
codes = ','.join(item.decode('ascii') for item in sorted(set(matches) & allowed)) or 'none'
if native == 124:
    summary = 'bounded_timeout'
elif codes != 'none':
    summary = 'recognized_npm_code'
elif re.search(rb'(?m)^npm (?:error|ERR!) network(?:[ \t\r]|$)', tail):
    summary = 'npm_network_summary'
else:
    summary = 'unclassified_install_failure'
print(f'PURCHASE_COSTS_INSTALL native={native} summary={summary} codes={codes}')
PY
  die LOCKED_FRONTEND_INSTALL_FAILED
fi
printf 'PURCHASE_COSTS_INSTALL native=0 summary=success codes=none\n'
verify_frontend_files || die INSTALLED_FRONTEND_SOURCE_CHANGED
if ! bounded 360s docker run "${prepare[@]}" --network none --workdir /work \
  --mount "type=bind,src=$state/frontend-work,dst=/work" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1536 \
  npm_config_userconfig=/tmp/ildr-user.npmrc npm_config_globalconfig=/tmp/ildr-global.npmrc npm_config_cache=/tmp/npm \
  npm_config_update_notifier=false npm_config_audit=false npm_config_fund=false \
  VITE_API_URL=http://127.0.0.1:8000 VITE_LANDING_URL=http://127.0.0.1:8917 \
  VITE_GOOGLE_CLIENT_ID=synthetic-ci.apps.googleusercontent.com sh -c 'node --test --test-reporter=tap tests/purchase-cost-worksheet.test.cjs &&
  npm run build --workspace=dashboard' >"$state/dashboard-build.log" 2>&1; then
  python3 -I -B - "$state/dashboard-build.log" <<'PY'
import re
import sys
from pathlib import Path
with Path(sys.argv[1]).open('rb') as log:
    body = log.read(512 * 1024)
for test in re.findall(rb'(?m)^not ok [0-9]+ - ([A-Za-z0-9 _.,:/-]{1,120})$', body)[:20]:
    print('BROWSER_TEST_FAILURE ' + test.decode())
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
for row in (b'# tests 4', b'# pass 4', b'# fail 0', b'# skipped 0', b'# todo 0'):
    assert lines.count(row) == 1
print('PURCHASE_COSTS_FRONTEND_CONTRACTS native=0 tests=4 pass=4 typecheck=success build=success')
PY
verify_frontend_files || die FRONTEND_SOURCE_CHANGED

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
  -c 'CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN' \
  -c "COMMENT ON DATABASE $db IS 'ildr-admin-share-ci:$nonce'" >/dev/null || die DATABASE_IDENTITY_MARKER_FAILED

runtime=(--label "$label=$run_key" --network "container:$pg" --user "$uids" --read-only --init
         --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 256 --cpus 2
         --env GITHUB_ACTIONS=true --env GITHUB_REPOSITORY=ilovedrrealty/realty-api)

api=$(bounded 25s docker create "${runtime[@]}" --memory 2g --memory-swap 2g \
  --tmpfs /tmp:rw,exec,size=128m --env "ILDR_BROWSER_CI_NONCE=$nonce" \
  --env "ILDR_PURCHASE_COSTS_APP_COMMIT=$backend_commit" --env "ILDR_PURCHASE_COSTS_APP_TREE=$backend_tree" \
  --env "ILDR_PURCHASE_COSTS_MIGRATION=$migration_head" \
  --mount "type=bind,src=$backend,dst=/application,readonly" \
  --mount "type=bind,src=$tools,dst=/tools,readonly" \
  --mount "type=bind,src=$state/fixtures,dst=/browser-fixtures" \
  "$app_image" /usr/local/bin/python -I -B /tools/serve_purchase_costs_fixture.py) || die API_CREATE_FAILED
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
        'apply_alembic','seed_accounts','seed_lead','seed_purchase_costs','write_fixture','serve_application'}
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
  "$browser_image" node /rehearsal/run-purchase-costs.mjs) || die BROWSER_CREATE_FAILED
owned "$browser" || die OWNED_BROWSER_REQUIRED
client_exit=0
bounded 240s docker start --attach "$browser" >"$state/browser.log" 2>&1 || client_exit=$?
container_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$browser")
[[ "$container_state" =~ ^[0-9]+\ (true|false)\ (true|false)$ ]] || die BROWSER_NATIVE_STATE_REQUIRED
printf 'BROWSER_REHEARSAL_NATIVE client=%s state=%s\n' "$client_exit" "$container_state"

python3 -I -B - "$state/browser.log" <<'PY'
from pathlib import Path
import json,re,sys
path=Path(sys.argv[1]); assert path.stat().st_size <= 131072
prefix=b'PURCHASE_COSTS_BROWSER '
lines=path.read_bytes().splitlines()
failure=b'PURCHASE_COSTS_BROWSER_FAILURE '
stages=rb'(?:fixture|normal_navigation|open_legacy_review|create_two_currencies|print_summary|reload_spanish|private_scope|clear_and_reload)'
failures=[line for line in lines if re.fullmatch(failure+stages+rb' [A-Z_]{1,90}', line)]
assert len(failures) <= 1
if failures:
    print(failures[0].decode('ascii'))
    sys.exit(1)
matches=[line[len(prefix):] for line in lines if line.startswith(prefix)]
assert len(matches)==1 and len(matches[0])<=8192
def safe(value):
    if isinstance(value,dict): return len(value)<=30 and all(re.fullmatch(r'[a-z_]+',key) and safe(item) for key,item in value.items())
    if isinstance(value,list): return len(value)<=100 and all(safe(item) for item in value)
    if isinstance(value,str): return re.fullmatch(r'[a-zA-Z0-9_ -]{1,120}',value) is not None
    return value is None or type(value) in (bool,int)
data=json.loads(matches[0]); assert safe(data)
print(prefix.decode()+json.dumps(data,sort_keys=True,separators=(',',':')))
assert data['status']=='PASS'
assert set(data)=={'status','checks'}
assert data['checks']==['legacy_private_review_opens_with_empty_costs',
                        'mobile_writes_usd_dop_and_explicit_zero_as_exact_cents',
                        'print_contains_rows_and_currency_totals_without_editor',
                        'fresh_spanish_mobile_session_restores_exact_saved_work',
                        'another_admin_and_anonymous_cannot_read_private_review',
                        'explicit_clear_saves_and_reloads_without_touching_legacy_review']
PY
[[ "$client_exit" == 0 && "$container_state" == '0 false false' ]] || die NATIVE_BROWSER_FAILED

bounded 20s docker stop --time 10 "$api" >/dev/null || die API_STOP_FAILED
api_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$api") || die API_SHUTDOWN_STATE_REQUIRED
[[ "$api_state" == '0 false false' ]] || die API_GRACEFUL_SHUTDOWN_REQUIRED
[[ ! -e "$state/fixtures/fixtures.json" ]] || die SYNTHETIC_LOGIN_FILE_CLEANUP_REQUIRED
printf 'BROWSER_API_FIXTURE_SHUTDOWN state=%s login_file=absent\n' "$api_state"
backend_status=$(git -C "$backend" status --porcelain=v1 --untracked-files=all) || die API_SOURCE_CHANGED
[[ -z "$backend_status" ]] || die API_SOURCE_CHANGED
printf 'PURCHASE_COSTS_ACCEPTANCE operations=%s frontend=%s frontend_tree=%s backend=%s backend_tree=%s migration=%s postgres_version_num=170006 run=%s attempt=1\n' \
  "$GITHUB_SHA" "$frontend_commit" "$frontend_tree" "$backend_commit" "$backend_tree" "$migration_head" "$GITHUB_RUN_ID"

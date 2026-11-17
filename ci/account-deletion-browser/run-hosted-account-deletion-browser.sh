#!/usr/bin/env bash
# Real Dashboard and API in a nonce-owned hosted PostgreSQL loopback namespace.
set -euo pipefail

die() { printf 'F09_BROWSER_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ $# == 5 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 &&
   ${GITHUB_RUN_ATTEMPT:-} == 1 && ${GITHUB_REF:-} == refs/heads/codex-ci-f09-deletion-browser-20261117 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die EXACT_HOSTED_EVENT_REQUIRED
ops="$1"; frontend="$2"; live="$3"; backend="$4"; migration="$5"
[[ "$ops" == "$GITHUB_WORKSPACE/browser-ops" && "$frontend" == "$GITHUB_WORKSPACE/frontend-base" &&
   "$live" == "$GITHUB_WORKSPACE/frontend-live" && "$backend" == "$GITHUB_WORKSPACE/browser-backend" &&
   "$migration" == "$GITHUB_WORKSPACE/migration-relay" ]] || die EXACT_CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$frontend" "$live" "$backend" "$migration"; do
  checkout_status=$(git -C "$checkout" status --porcelain=v1 --untracked-files=all) || die CLEAN_CHECKOUT_REQUIRED
  [[ $(realpath "$checkout") == "$checkout" && -z "$checkout_status" &&
     ! -e "$checkout/.env" && ! -L "$checkout/.env" ]] || die CLEAN_CHECKOUT_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" rev-parse HEAD^) == c57b2836e331dc02a346a85786f3ab674a60a9ac ]] || die EXACT_OPERATIONS_PARENT_REQUIRED
operations_delta=$(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) || die EXACT_OPERATIONS_DELTA_REQUIRED
[[ "$operations_delta" == $'.github/workflows/account-deletion-browser.yml\nci/account-deletion-browser/check-browser-result.py\nci/account-deletion-browser/reconstruct_frontend.py\nci/account-deletion-browser/run-account-deletion-browser.mjs\nci/account-deletion-browser/run-hosted-account-deletion-browser.sh\nci/account-deletion-browser/serve_account_deletion_fixture.py\nci/account-deletion-browser/serve_admin_share_fixture.py\nci/account-deletion-browser/serve_dashboard_fixture.mjs' ]] || die EXACT_OPERATIONS_DELTA_REQUIRED
frontend_commit=69d64e6bb7ba4d748267d22b7e215bd53089f325
frontend_tree=2570e8ccc497bd03fbee402897d67cbd9ad9a294
frontend_paths=575
backend_commit=979b5faf37a819161a0501962b2a807836da5b30
backend_tree=d286ca58414fa971f967c0657cba142ca41a5694
migration_head=c3a914f0b628
[[ $(git -C "$frontend" rev-parse HEAD) == 99bc8d4c0ee4f3f0b72cf174530971f6a35f0ab3 &&
   $(git -C "$live" rev-parse HEAD) == b5df6a9321365e827c01df556c09b8808151e59a &&
   $(git -C "$backend" rev-parse HEAD) == "$backend_commit" &&
   $(git -C "$backend" rev-parse 'HEAD^{tree}') == "$backend_tree" &&
   $(git -C "$migration" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 ]] || die EXACT_REVIEWED_SOURCES_REQUIRED
tools="$ops/ci/account-deletion-browser"
dockerfile="$migration/ci/migration/services163.Dockerfile"
printf '%s  %s\n' \
  e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9 "$dockerfile" \
  7b69adcde368665ac3eaf0b3dae12dfa867aaf197efd63e82c4cc7fddfb8f86d "$tools/serve_admin_share_fixture.py" \
  29e0041be48ea60430b9166c65f00190cf726452a0e62979a6075156f9b81ff3 "$tools/serve_dashboard_fixture.mjs" \
  | sha256sum --check --status || die EXACT_ACCEPTED_INFRASTRUCTURE_REQUIRED

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
label='org.ilovedrrealty.browser.account-deletion'
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
    printf 'F09_BROWSER_CLEANUP_UNVERIFIED\n' >&2
    exit 1
  fi
  printf 'F09_BROWSER_OWNED_CLEANUP commit=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT

mkdir "$state/frontend-work" "$state/fixtures" "$state/browser-run" "$state/python-context" "$state/npm-cache"
cp "$backend/requirements.txt" "$state/python-context/requirements.txt"
cp "$tools/run-account-deletion-browser.mjs" "$state/browser-run/"
for image in "$node_image" "$python_image" "$postgres_image" "$browser_image"; do
  bounded 180s docker pull --platform linux/amd64 "$image" >/dev/null || die PINNED_IMAGE_PULL_FAILED
  [[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image") == linux/amd64 ]] || die EXACT_IMAGE_PLATFORM_REQUIRED
done
bounded 600s docker build --platform linux/amd64 --pull=false --quiet --label "$label=$run_key" \
  --build-arg "PYTHON_DIGEST=$python_digest" --file "$dockerfile" --tag "$app_image" "$state/python-context" \
  >"$state/python-build.log" 2>&1 || die API_IMAGE_BUILD_FAILED

source_repo="$state/frontend-objects"
python3 -I -B "$tools/reconstruct_frontend.py" "$ops" "$frontend" "$live" "$source_repo" "$state/frontend-work" || die EXACT_FRONTEND_RECONSTRUCTION_REQUIRED
verify_frontend_files() {
  python3 -I -B - "$source_repo" "$state/frontend-work" "$frontend_commit" "$frontend_paths" <<'PY'
from hashlib import sha1
from pathlib import Path
import subprocess, sys
source, copy = map(Path, sys.argv[1:3])
entries = subprocess.check_output(["git", "-C", str(source), "ls-tree", "-rz", sys.argv[3]]).split(b"\0")
assert entries[-1] == b"" and len(entries) - 1 == int(sys.argv[4])
for entry in entries[:-1]:
    metadata, name = entry.split(b"\t", 1)
    mode, kind, object_id = metadata.split()
    path = copy / name.decode("utf-8")
    assert kind == b"blob" and mode in (b"100644", b"100755") and path.is_file() and not path.is_symlink()
    payload = path.read_bytes()
    assert sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest().encode() == object_id
PY
}
verify_frontend_files || die EXACT_FRONTEND_ARCHIVE_REQUIRED
printf '%s  %s\n' \
  8df3c4c580c65694bc727488fc0ec02d88ab1577e5fa16c509f4897b58e60f33 "$state/frontend-work/package-lock.json" \
  1126aeb69e32b34394cca03ac440848f7b88b339042496cb6722a1781a204337 "$state/frontend-work/apps/dashboard/package.json" \
  | sha256sum --check --status || die EXACT_PACKAGE_LOCK_REQUIRED
prepare=(--label "$label=$run_key" --user "$uids" --read-only --cap-drop ALL --security-opt no-new-privileges:true
         --memory 8g --memory-swap 8g --pids-limit 256 --cpus 2 --tmpfs /tmp:rw,exec,size=128m)
install_exit=0
bounded 500s docker run "${prepare[@]}" --network bridge --workdir /work \
  --mount "type=bind,src=$state/frontend-work,dst=/work" --mount "type=bind,src=$state/npm-cache,dst=/npm-cache" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true npm_config_registry=https://registry.npmjs.org/ \
  npm_config_userconfig=/tmp/ildr-user.npmrc npm_config_globalconfig=/tmp/ildr-global.npmrc npm_config_cache=/npm-cache \
  npm_config_update_notifier=false npm_config_audit=false npm_config_fund=false npm_config_ignore_scripts=true \
  node /usr/local/lib/node_modules/npm/bin/npm-cli.js ci --ignore-scripts --no-audit --no-fund --include=dev --include=optional \
  >"$state/npm-install.log" 2>&1 || install_exit=$?
python3 -I -B - "$state/npm-install.log" "$install_exit" <<'PY' || die LOCKED_DEPENDENCY_INSTALL_REQUIRED
from pathlib import Path
import re,sys
log=Path(sys.argv[1]); assert log.stat().st_size <= 1048576
codes=sorted(set(re.findall(rb"(?m)^npm (?:error|ERR!) code ([A-Z][A-Z0-9_]{1,45})[ \t\r]*$",log.read_bytes())))
print("F09_BROWSER_INSTALL native="+sys.argv[2]+" codes="+(",".join(code.decode() for code in codes[:6]) or "none"))
assert sys.argv[2]=="0"
PY
verify_frontend_files || die INSTALL_ALTERED_SOURCE
native=0
bounded 360s docker run "${prepare[@]}" --network none --workdir /work \
  --mount "type=bind,src=$state/frontend-work,dst=/work" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1536 \
  npm_config_userconfig=/tmp/ildr-user.npmrc npm_config_globalconfig=/tmp/ildr-global.npmrc npm_config_cache=/tmp/npm \
  npm_config_update_notifier=false npm_config_audit=false npm_config_fund=false \
  VITE_API_URL=http://127.0.0.1:8918 VITE_LANDING_URL=http://127.0.0.1:8917 \
  VITE_GOOGLE_CLIENT_ID=synthetic-ci.apps.googleusercontent.com sh -c \
  'node --test --test-concurrency=1 --test-reporter=tap tests/account-deletion.test.cjs && npm run build --workspace=dashboard' \
  >"$state/dashboard-build.log" 2>&1 || native=$?
python3 -I -B - "$state/dashboard-build.log" "$native" <<'PY' || die STRICT_DASHBOARD_BUILD_REQUIRED
from pathlib import Path
import re,sys
log=Path(sys.argv[1]); assert log.stat().st_size <= 1048576
raw=log.read_bytes()
for source,line,column,code in re.findall(rb"(?m)^(src/[A-Za-z0-9_./-]+\.tsx?)\(([0-9]+),([0-9]+)\): error (TS[0-9]+):",raw)[:12]:
    print(f"F09_BROWSER_BUILD_DIAGNOSTIC {source.decode()}:{line.decode()}:{column.decode()} {code.decode()}")
assert sys.argv[2]=="0"
rows={name:int(value) for name,value in re.findall(rb"(?m)^# (tests|pass|fail|skipped) ([0-9]+)$",raw)}
assert rows=={b"tests":15,b"pass":15,b"fail":0,b"skipped":0}
assert b"tsc --noEmit" in raw and b"vite build" in raw
print("F09_BROWSER_DASHBOARD native=0 tests=15 pass=15 typecheck=success build=success")
PY
verify_frontend_files || die DASHBOARD_BUILD_ALTERED_SOURCE
python3 -I -B - "$state/frontend-work" "$state/browser-run" <<'PY' || die EXACT_BROWSER_DEPENDENCIES_REQUIRED
import base64,hashlib,io,json,sys,tarfile,urllib.request
from pathlib import Path,PurePosixPath
workspace,browser=map(Path,sys.argv[1:])
dist=workspace/"apps/dashboard/dist"; assert (dist/"index.html").is_file()
assets=[path.read_bytes() for path in (dist/"assets").glob("*.js") if path.is_file() and not path.is_symlink()]
assert any(b"http://127.0.0.1:8918" in payload for payload in assets)
for language in ("en","es"):
    payload=(workspace/f"apps/dashboard/src/locale/{language}/account_deletion.json").read_bytes()
    (browser/f"{language}.json").write_bytes(payload)
url="https://registry.npmjs.org/playwright-core/-/playwright-core-1.64.1.tgz"
with urllib.request.urlopen(url,timeout=30) as response:
    assert response.geturl()==url
    payload=response.read(20*1024*1024+1)
assert 0<len(payload)<=20*1024*1024
assert base64.b64encode(hashlib.sha512(payload).digest()).decode()=="BC4s8y6SEVA6Q1rSxZTto8hI1KUI3xXlHGZgvGqP4IqPdI/a6FX9H29IDNz7vKo9l21b6LfYosScPL6M3xxyDQ=="
target=browser/"node_modules/playwright-core";target.mkdir(parents=True)
with tarfile.open(fileobj=io.BytesIO(payload),mode="r:gz") as archive:
    members=archive.getmembers();assert len(members)<=6000 and sum(item.size for item in members)<=90*1024*1024
    for item in members:
        parts=PurePosixPath(item.name).parts
        assert parts and parts[0]=="package" and ".." not in parts and (item.isdir() or item.isfile())
        path=target.joinpath(*parts[1:])
        if item.isdir(): path.mkdir(parents=True,exist_ok=True)
        else:
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open("xb") as output: output.write(archive.extractfile(item).read())
assert json.loads((target/"package.json").read_bytes())["version"]=="1.64.1"
PY

db="ildr_browser_ci_$nonce"
pg=$(bounded 25s docker create --label "$label=$run_key" --network none \
  --security-opt no-new-privileges:true --memory 768m --memory-swap 768m --pids-limit 96 --cpus 1 \
  --tmpfs /var/lib/postgresql/data:rw,size=384m --env POSTGRES_HOST_AUTH_METHOD=trust --env "POSTGRES_DB=$db" \
  "$postgres_image" postgres -p 55449 -h 127.0.0.1) || die POSTGRES_CREATE_FAILED
owned "$pg" && [[ $(bounded 15s docker inspect --format '{{.HostConfig.NetworkMode}}' "$pg") == none ]] || die OWNED_POSTGRES_REQUIRED
bounded 25s docker start "$pg" >/dev/null || die POSTGRES_START_FAILED
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  if bounded 3s docker exec "$pg" pg_isready -h 127.0.0.1 -p 55449 -U postgres -d "$db" >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 && $(bounded 5s docker exec "$pg" ls /sys/class/net) == lo ]] || die LOOPBACK_POSTGRES_REQUIRED
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d "$db" -v ON_ERROR_STOP=1 \
  -c 'CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN' \
  -c "COMMENT ON DATABASE $db IS 'ildr-admin-share-ci:$nonce'" >/dev/null || die DATABASE_MARKER_REQUIRED
postgres_version_num=$(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d "$db" -v ON_ERROR_STOP=1 -c 'SHOW server_version_num')
[[ "$postgres_version_num" == 170006 ]] || die EXACT_POSTGRES_VERSION_REQUIRED
runtime=(--label "$label=$run_key" --network "container:$pg" --user "$uids" --read-only --init
         --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 256 --cpus 2
         --env GITHUB_ACTIONS=true --env GITHUB_REPOSITORY=ilovedrrealty/realty-api)
api=$(bounded 25s docker create "${runtime[@]}" --memory 2g --memory-swap 2g --tmpfs /tmp:rw,exec,size=128m \
  --env "ILDR_BROWSER_CI_NONCE=$nonce" --mount "type=bind,src=$backend,dst=/application,readonly" \
  --mount "type=bind,src=$tools,dst=/tools,readonly" --mount "type=bind,src=$state/fixtures,dst=/browser-fixtures" \
  "$app_image" /usr/local/bin/python -I -B /tools/serve_account_deletion_fixture.py) || die API_CREATE_FAILED
owned "$api" && bounded 25s docker start "$api" >/dev/null || die API_START_FAILED
dashboard=$(bounded 25s docker create "${runtime[@]}" --memory 512m --memory-swap 512m \
  --mount "type=bind,src=$state/frontend-work/apps/dashboard/dist,dst=/dashboard-dist,readonly" \
  --mount "type=bind,src=$tools,dst=/tools,readonly" "$node_image" node /tools/serve_dashboard_fixture.mjs) || die DASHBOARD_CREATE_FAILED
owned "$dashboard" && bounded 25s docker start "$dashboard" >/dev/null || die DASHBOARD_START_FAILED
ready=0
for ((attempt=0; attempt<90; attempt++)); do
  if [[ -s "$state/fixtures/fixtures.json" ]] && bounded 4s docker exec "$api" /usr/local/bin/python -I -B -c \
    'from urllib.request import urlopen; assert all(urlopen(url,timeout=1).status==200 for url in ("http://127.0.0.1:8918/health","http://127.0.0.1:4175/"))' >/dev/null 2>&1; then ready=1; break; fi
  if [[ $(bounded 5s docker inspect --format '{{.State.Running}}' "$api") != true ]]; then
    bounded 5s docker logs --tail 120 "$api" >"$state/api-start.log" 2>&1 || true
    python3 -I -B - "$state/api-start.log" <<'PY'
from pathlib import Path
import json,re,sys
source=Path(sys.argv[1]);assert source.stat().st_size<=131072
for line in source.read_text().splitlines():
    if line.startswith("F09_BROWSER_API_FAILURE "):
        value=json.loads(line.removeprefix("F09_BROWSER_API_FAILURE "))
        assert set(value)=={"stage","error_type","fixture_line"}
        assert value["stage"] in {"checkout","claim_database","configure_application","load_application","bootstrap_database","apply_alembic","seed_accounts","write_fixture","serve_application"}
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}",value["error_type"])
        assert value["fixture_line"] is None or type(value["fixture_line"]) is int and 0<value["fixture_line"]<1000
        print("F09_BROWSER_API_FAILURE "+json.dumps(value,separators=(",",":")))
PY
    die API_FIXTURE_EXITED
  fi
  sleep 1
done
[[ "$ready" == 1 && $(stat -c %a "$state/fixtures/fixtures.json") == 600 ]] || die PRIVATE_FIXTURE_NOT_READY
browser=$(bounded 25s docker create "${runtime[@]}" --memory 2g --memory-swap 2g --shm-size 384m \
  --tmpfs /tmp:rw,exec,size=192m --env HOME=/tmp --env PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
  --env ILDR_BROWSER_FIXTURES=/browser-fixtures/fixtures.json \
  --mount "type=bind,src=$state/browser-run,dst=/rehearsal,readonly" \
  --mount "type=bind,src=$state/fixtures,dst=/browser-fixtures,readonly" \
  "$browser_image" node /rehearsal/run-account-deletion-browser.mjs) || die BROWSER_CREATE_FAILED
owned "$browser" || die OWNED_BROWSER_REQUIRED
client_exit=0
bounded 320s docker start --attach "$browser" >"$state/browser.log" 2>&1 || client_exit=$?
container_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$browser")
[[ "$container_state" =~ ^[0-9]+\ (true|false)\ (true|false)$ ]] || die BROWSER_NATIVE_STATE_REQUIRED
printf 'F09_BROWSER_NATIVE client=%s state=%s\n' "$client_exit" "$container_state"
verified=0
python3 -I -B "$tools/check-browser-result.py" "$state/browser.log" || verified=$?
bounded 20s docker stop --time 10 "$api" >/dev/null || die API_STOP_FAILED
api_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$api")
[[ "$api_state" == '0 false false' && ! -e "$state/fixtures/fixtures.json" ]] || die PRIVATE_FIXTURE_CLEANUP_REQUIRED
printf 'F09_BROWSER_API_SHUTDOWN state=%s login_file=absent\n' "$api_state"
bounded 10s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d "$db" -v ON_ERROR_STOP=1 -c "
SELECT json_build_object(
 'users',(SELECT count(*) FROM users),
 'active_users',(SELECT count(*) FROM users WHERE status='active'),
 'cleared_flags',(SELECT count(*) FROM users WHERE deletion_requested_at IS NULL),
 'requests',(SELECT count(*) FROM account_deletion_requests),
 'active_requests',(SELECT count(*) FROM account_deletion_requests WHERE status<>'cancelled'),
 'events',(SELECT count(*) FROM account_deletion_events),
 'requested',(SELECT count(*) FROM account_deletion_events WHERE action='requested'),
 'reviewed',(SELECT count(*) FROM account_deletion_events WHERE action='reviewed'),
 'holds',(SELECT count(*) FROM account_deletion_events WHERE action='retention_hold'),
 'cancelled',(SELECT count(*) FROM account_deletion_events WHERE action='cancelled'),
 'newsletter',(SELECT count(*) FROM newsletter_subscriptions),
 'delivery_jobs',(SELECT count(*) FROM lead_delivery_jobs),
 'leads',(SELECT count(*) FROM leads),
 'listings',(SELECT count(*) FROM listings));" >"$state/db-audit.json" || die OWNED_DATABASE_AUDIT_REQUIRED
python3 -I -B - "$state/db-audit.json" "$verified" <<'PY' || die REVIEW_HISTORY_PARITY_REQUIRED
from pathlib import Path
import json,sys
path=Path(sys.argv[1]);assert path.stat().st_size<=4096
value=json.loads(path.read_bytes())
assert all(type(item) is int and 0<=item<=100 for item in value.values())
print("F09_BROWSER_DATABASE "+json.dumps(value,sort_keys=True,separators=(",",":")))
if sys.argv[2]=="0":
    assert value=={"users":4,"active_users":4,"cleared_flags":4,"requests":4,"active_requests":0,"events":12,"requested":4,"reviewed":2,"holds":2,"cancelled":4,"newsletter":0,"delivery_jobs":0,"leads":0,"listings":0}
PY
[[ "$verified" == 0 && "$client_exit" == 0 && "$container_state" == '0 false false' ]] || die ACTUAL_BROWSER_JOURNEY_NOT_ACCEPTED
verify_frontend_files || die BROWSER_ALTERED_FRONTEND_SOURCE
[[ -z $(git -C "$backend" status --porcelain=v1 --untracked-files=all) ]] || die BROWSER_ALTERED_BACKEND_SOURCE
printf 'F09_BROWSER_ACCEPTANCE operations=%s frontend=%s frontend_tree=%s backend=%s backend_tree=%s migration=%s postgres_version_num=%s run=%s attempt=1 scope=request-review-cancel\n' \
  "$GITHUB_SHA" "$frontend_commit" "$frontend_tree" "$backend_commit" "$backend_tree" "$migration_head" "$postgres_version_num" "$GITHUB_RUN_ID"

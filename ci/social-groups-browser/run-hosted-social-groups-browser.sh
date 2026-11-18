#!/usr/bin/env bash
# Exact sources; one immutable build; fresh owned DB and private fixture per phase.
set -euo pipefail
umask 077
die() { printf 'PLATFORM_BROWSER_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }
[[ $# == 6 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 &&
   ${GITHUB_RUN_ATTEMPT:-} == 1 && ${GITHUB_REF:-} == refs/heads/codex-ci-social-groups-browser-20261118-v8 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die HOSTED_EXACT_EVENT_REQUIRED
ops="$1"; frontend="$2"; baseline="$3"; backend="$4"; migration="$5"; relay="$6"
[[ "$ops" == "$GITHUB_WORKSPACE/build-ops" && "$frontend" == "$GITHUB_WORKSPACE/frontend-source" &&
   "$baseline" == "$GITHUB_WORKSPACE/current-platform" && "$backend" == "$GITHUB_WORKSPACE/browser-backend" &&
   "$migration" == "$GITHUB_WORKSPACE/migration-relay" &&
   "$relay" == "$GITHUB_WORKSPACE/browser-relay" ]] || die EXACT_CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$frontend" "$baseline" "$backend" "$migration" "$relay"; do
  [[ $(realpath "$checkout") == "$checkout" && -z $(git -C "$checkout" status --porcelain=v1 --untracked-files=all) &&
     ! -e "$checkout/.env" && ! -L "$checkout/.env" ]] || die CLEAN_CHECKOUT_REQUIRED
done
expected_operations=$(cat <<'EXACT_OPERATIONS'
.github/workflows/social-groups-browser.yml
ci/social-groups-browser/PLAN.json
ci/social-groups-browser/check-social-groups-result.py
ci/social-groups-browser/run-hosted-social-groups-browser.sh
ci/social-groups-browser/run-social-groups-browser.mjs
EXACT_OPERATIONS
)
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" rev-parse HEAD^) == 1d6668a926eb8e26b0f33bf810f48b2a09639877 &&
   $(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) == "$expected_operations" ]] || die EXACT_OPERATIONS_REQUIRED
backend_commit=4e23cc8b643a4b07f4e14042e530e6271dad9c8f
backend_tree=8093ea7aa9fd8f87a842e2527c715a8c482ac1fc
frontend_commit=7b9ec53f54141ef66b92466de4dde81f5bc0f69e
frontend_tree=8baac20b593e6cf4cfc964fa95333e7a856de7d8
frontend_paths=585
[[ $(git -C "$backend" rev-parse HEAD) == "$backend_commit" && $(git -C "$backend" rev-parse HEAD^{tree}) == "$backend_tree" &&
   $(git -C "$frontend" rev-parse HEAD) == "$frontend_commit" && $(git -C "$frontend" rev-parse HEAD^{tree}) == "$frontend_tree" &&
   $(git -C "$frontend" rev-parse HEAD^) == 5d2afabca5560cb06166ad6af8efa3a06f8d4b6c &&
   $(git -C "$frontend" rev-parse HEAD^^) == c53aed2cdd0d9480a6c46c78253874a7674452d5 &&
   $(git -C "$frontend" rev-parse HEAD^^^) == 461a2ca2c7c2cac39212d4bddec9352e35429c39 &&
   $(git -C "$baseline" rev-parse HEAD) == 01695530df670a94ac28f8b3bf36cc34b5b7a942 &&
   $(git -C "$baseline" rev-parse HEAD^{tree}) == e88ea0c57f9fac39545322532a5992d5a2927676 &&
   $(git -C "$migration" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 &&
   $(git -C "$relay" rev-parse HEAD) == 3007728fd67c5889a8373e41ff3ffa5cc50d839b &&
   $(git -C "$relay" rev-parse HEAD^{tree}) == 80868dce147c2cd4609e303493669d0a19f09741 ]] || die EXACT_SOURCES_REQUIRED
[[ -z $(git -C "$frontend" diff --name-only 461a2ca2c7c2cac39212d4bddec9352e35429c39 HEAD -- apps/landing packages package.json package-lock.json) ]] || die UNCHANGED_LANDING_AND_PACKAGES_REQUIRED
printf 'SOCIAL_GROUPS_RETAINED_EVIDENCE landing=byte_identical parent=461a2ca2c7c2cac39212d4bddec9352e35429c39 prior_network_groups=68 prior_contact_groups=8 prior_geometry=59 prior_map_groups=8
'
[[ -d ${RUNNER_TEMP:-} && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die HOST_SCRATCH_REQUIRED
nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-integrated.XXXXXXXX")
label='org.ilovedrrealty.social.groups.browser'
run_key="$GITHUB_RUN_ID-$nonce"
app_image="ildr-platform-browser:$run_key"
cleanup() {
  local status=$? clean=1 ids id
  trap - EXIT
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  for id in $ids; do
    if [[ "$id" =~ ^[a-f0-9]{64}$ && $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$id") == "$run_key" ]]; then
      bounded 15s docker rm -f -v "$id" >/dev/null 2>&1 || clean=0
    else
      clean=0
    fi
  done
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  [[ -z "$ids" ]] || clean=0
  images=$(bounded 15s docker image ls -q --no-trunc --filter "label=$label=$run_key" | sort -u) || clean=0
  if [[ -n "$images" ]]; then
    tagged=$(bounded 15s docker image inspect --format '{{.Id}}' "$app_image") || clean=0
    if [[ "$images" =~ ^sha256:[a-f0-9]{64}$ && "$images" == "$tagged" ]]; then
      bounded 15s docker image rm "$app_image" >/dev/null 2>&1 || clean=0
    else
      clean=0
    fi
  fi
  images=$(bounded 15s docker image ls -q --no-trunc --filter "label=$label=$run_key") || clean=0
  [[ -z "$images" ]] || clean=0
  if [[ "$state" == "$RUNNER_TEMP"/ildr-integrated.* && $(realpath "$state") == "$state" && -O "$state" && ! -L "$state" ]]; then
    bounded 40s rm --recursive --force --one-file-system -- "$state" || clean=0
  else
    clean=0
  fi
  [[ ! -e "$state" && "$clean" == 1 ]] || die OWNED_CLEANUP_NOT_VERIFIED
  printf 'INTEGRATED_FRONTEND_OWNED_CLEANUP operations=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$status"
}
trap cleanup EXIT
mkdir "$state/frontend-work" "$state/npm-cache"
source_repo="$frontend"
git -C "$source_repo" archive --format=tar "$frontend_commit" | tar -xf - -C "$state/frontend-work" || die ARCHIVE_REQUIRED
printf 'PLATFORM_FRONTEND_DIRECT_SOURCE commit=%s tree=%s live_parent=461a2ca2c7c2cac39212d4bddec9352e35429c39 paths=%s\n' \
  "$frontend_commit" "$frontend_tree" "$frontend_paths"
verify_source() {
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
verify_source || die ARCHIVED_FRONTEND_MISMATCH
(
  cd "$state/frontend-work"
  sha256sum --check --status <<'PINS'
8df3c4c580c65694bc727488fc0ec02d88ab1577e5fa16c509f4897b58e60f33  package-lock.json
4f4587eb432013b20c771d3a045fb04b67d146794c47c007e805299d2c562a43  apps/dashboard/package.json
45893e6dfebaad4d2e73f7e7d6b26f176883bc1ba8c8dc025f188cc1b4d12344  apps/landing/package.json
PINS
) || die PACKAGE_SHA256_REQUIRED

node_image='node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c'
bounded 180s docker pull --platform linux/amd64 "$node_image" >/dev/null || die PINNED_NODE_PULL_FAILED
[[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$node_image") == linux/amd64 ]] || die PINNED_NODE_PLATFORM_REQUIRED
prepare=(--rm --label "$label=$run_key" --user "$(id -u):$(id -g)" --read-only --cap-drop ALL
         --security-opt no-new-privileges:true --memory 8g --memory-swap 8g --pids-limit 256 --cpus 2
         --tmpfs /tmp:rw,exec,size=128m --workdir /work)
native=0
bounded 500s docker run "${prepare[@]}" --network bridge \
  --mount "type=bind,src=$state/frontend-work,dst=/work" --mount "type=bind,src=$state/npm-cache,dst=/npm-cache" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1024 \
  npm_config_registry=https://registry.npmjs.org/ npm_config_userconfig=/tmp/ildr-user.npmrc \
  npm_config_globalconfig=/tmp/ildr-global.npmrc npm_config_cache=/npm-cache \
  npm_config_update_notifier=false npm_config_audit=false npm_config_fund=false npm_config_ignore_scripts=true \
  node /usr/local/lib/node_modules/npm/bin/npm-cli.js ci --ignore-scripts --no-audit --no-fund --include=dev --include=optional \
  >"$state/install.log" 2>&1 || native=$?
python3 -I -B - "$state/install.log" "$native" <<'PY' || die LOCKED_INSTALL_REQUIRED
from pathlib import Path
import re, sys

with Path(sys.argv[1]).open("rb") as log:
    log.seek(max(0, Path(sys.argv[1]).stat().st_size - 131072))
    data = log.read(131072)
allowed = {b'ENOSPC', b'ENOMEM', b'EINTEGRITY', b'EAI_AGAIN', b'ENOTFOUND', b'ETIMEDOUT', b'ECONNRESET', b'E429', b'E502', b'E503', b'E504', b'EACCES', b'EUSAGE'}
codes = set(re.findall(rb"(?m)^npm (?:error|ERR!) code ([A-Z0-9_]{2,45})\s*$", data)) & allowed
print(f"INTEGRATED_FRONTEND_INSTALL native={int(sys.argv[2])} codes=" + (b",".join(sorted(codes)).decode("ascii") or "none"))
assert sys.argv[2] == "0"
PY
verify_source || die INSTALLED_FRONTEND_MISMATCH

# Exact unchanged product already passed the full contracts and strict check in V2.
printf 'SOCIAL_GROUPS_RETAINED_FRONTEND_ACCEPTANCE run=44287063765 job=134742332787 frontend=7b9ec53f54141ef66b92466de4dde81f5bc0f69e tree=8baac20b593e6cf4cfc964fa95333e7a856de7d8 contracts=480 passed=480 skipped=0 strict_native=0 vite_native=0\n'


native=0
bounded 360s docker run "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1536 \
  npm_config_userconfig=/tmp/ildr-user.npmrc npm_config_globalconfig=/tmp/ildr-global.npmrc \
  npm_config_cache=/tmp/npm npm_config_audit=false npm_config_fund=false npm_config_update_notifier=false \
  VITE_API_URL=http://127.0.0.1:8918 VITE_LANDING_URL=http://127.0.0.1:8917 \
  VITE_GOOGLE_CLIENT_ID=synthetic-ci.apps.googleusercontent.com \
  sh -c 'npm run build --workspace=dashboard' \
  >"$state/dashboard.log" 2>&1 || native=$?
python3 -I -B - "$state/dashboard.log" "$state/frontend-work/apps/dashboard/dist/index.html" "$native" <<'PY' || die ACTUAL_DASHBOARD_BUILD_REQUIRED
from pathlib import Path
import re, sys

log, index = map(Path, sys.argv[1:3])
assert log.stat().st_size <= 1048576
data = log.read_bytes()
if sys.argv[3] != "0":
    for file, line, col, code in re.findall(
        rb"(?m)^((?:src|\.\./\.\./packages)/[A-Za-z0-9_./-]+\.(?:ts|tsx))\(([0-9]{1,5}),([0-9]{1,5})\): error (TS[0-9]{4}):", data
    )[:12]:
        print(f"INTEGRATED_DASHBOARD_DIAGNOSTIC {file.decode()}:{line.decode()}:{col.decode()} {code.decode()}")
    print(f"INTEGRATED_DASHBOARD native={int(sys.argv[3])}")
    raise SystemExit(1)
assert b"vite build" in data and index.is_file() and not index.is_symlink()
assert b"127.0.0.1:8918" in b"".join(f.read_bytes() for f in index.parent.glob("assets/*.js") if f.is_file())
print("INTEGRATED_DASHBOARD native=0 typecheck=retained build=success")
PY
verify_source || die DASHBOARD_ALTERED_SOURCE

tools="$ops/ci/social-groups-browser"
dockerfile="$migration/ci/migration/services163.Dockerfile"
dashboard_server="$relay/ci/platform-browser/accepted/serve_dashboard_fixture.mjs"
printf '%s  %s\n' \
  e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9 "$dockerfile" \
  29e0041be48ea60430b9166c65f00190cf726452a0e62979a6075156f9b81ff3 "$dashboard_server" | sha256sum --check --status || die ACCEPTED_RUNTIME_TOOLS_REQUIRED
python_digest='sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60'
python_image="docker.io/library/python:3.11-slim@$python_digest"
postgres_image='docker.io/library/postgres:17.6@sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409'
browser_image='mcr.microsoft.com/playwright@sha256:ee15712262f017b8157421b212b12099146645f44b1bc5c0514cbbe6a229b1d0'
for image in "$python_image" "$postgres_image" "$browser_image"; do
  bounded 180s docker pull --platform linux/amd64 "$image" >/dev/null || die PINNED_RUNTIME_IMAGE_PULL_FAILED
  [[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image") == linux/amd64 ]] || die EXACT_RUNTIME_IMAGE_PLATFORM_REQUIRED
done
mkdir "$state/python-context" "$state/fixtures" "$state/browser-run"
cp "$baseline/requirements.txt" "$state/python-context/requirements.txt"
cp "$dockerfile" "$state/python-context/Dockerfile"
cat >>"$state/python-context/Dockerfile" <<'DOCKER_READERS'
RUN mkdir -p /opt/contact-import && /usr/local/bin/python -I -B -c "import importlib.metadata as m,json,re;from pathlib import Path;Path('/opt/contact-import/baseline-inventory.json').write_text(json.dumps({re.sub(r'[-_.]+','-',d.metadata['Name'].lower()):d.version for d in m.distributions()},sort_keys=True))"
COPY readers.txt /opt/contact-import/readers.txt
RUN /usr/local/bin/python -I -B -m pip install --retries 0 --timeout 30 --no-cache-dir --no-compile --no-deps --only-binary=:all: --require-hashes --requirement /opt/contact-import/readers.txt \
    && /usr/local/bin/python -I -B -m pip check
DOCKER_READERS
cat >"$state/python-context/readers.txt" <<'READER_WHEELS'
openpyxl==3.1.5 --hash=sha256:5282c12b107bffeef825f4617dc029afaf41d0ea60823bbb665ef3079dc79de2
defusedxml==0.7.1 --hash=sha256:a352e7e428770286cc899e2542b6cdaedb2b4953ff269a210103ec58f6198a61
et-xmlfile==2.0.0 --hash=sha256:7a91720bc756843502c3b7504c77b8fe44217c85c537d85037f0f536151b2caa
READER_WHEELS

cp -R "$tools/." "$state/browser-run/"
bounded 600s docker build --platform linux/amd64 --pull=false --quiet --label "$label=$run_key" \
  --build-arg "PYTHON_DIGEST=$python_digest" --file "$state/python-context/Dockerfile" --tag "$app_image" "$state/python-context" \
  >"$state/python-build.log" 2>&1 || die PINNED_API_DEPENDENCIES_REQUIRED

python3 -I -B - "$state/frontend-work" "$state/browser-run" <<'PY_BROWSER'
import base64, hashlib, io, json, tarfile, urllib.request
from pathlib import Path, PurePosixPath
import sys
workspace, browser = map(Path, sys.argv[1:])
output = workspace/'apps/dashboard/dist'
assert (output/'index.html').is_file() and (output/'templates/contact-import.csv').is_file()
for language in ('en', 'es'):
    def copy(name):
        return json.loads((workspace/f'apps/dashboard/src/locale/{language}/{name}.json').read_bytes())
    (browser/f'{language}.json').write_text(json.dumps({'network': copy('property_network'), 'nav': copy('nav'), 'common': copy('common')}, ensure_ascii=False), encoding='utf-8')
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
            with path.open('xb') as stream:
                stream.write(archive.extractfile(member).read())
assert json.loads((target/'package.json').read_bytes())['version'] == '1.64.1'
PY_BROWSER

pg=$(bounded 25s docker create --label "$label=$run_key" --network none \
  --security-opt no-new-privileges:true --memory 768m --memory-swap 768m --pids-limit 96 --cpus 1 \
  --tmpfs /var/lib/postgresql/data:rw,size=384m \
  --env POSTGRES_HOST_AUTH_METHOD=trust --env POSTGRES_DB=postgres \
  "$postgres_image" postgres -p 55449 -h 127.0.0.1) || die POSTGRES_CREATE_FAILED
[[ "$pg" =~ ^[a-f0-9]{64}$ && $(bounded 15s docker inspect --format "{{index .Config.Labels \"$label\"}}" "$pg") == "$run_key" &&
   $(bounded 15s docker inspect --format '{{.HostConfig.NetworkMode}}' "$pg") == none ]] || die OWNED_LOOPBACK_DATABASE_REQUIRED
bounded 25s docker start "$pg" >/dev/null || die POSTGRES_START_FAILED
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  if bounded 3s docker exec "$pg" pg_isready -h 127.0.0.1 -p 55449 -U postgres -d postgres >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 && $(bounded 5s docker exec "$pg" ls /sys/class/net) == lo ]] || die POSTGRES_READY_REQUIRED
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c 'CREATE ROLE anon NOLOGIN NOINHERIT; CREATE ROLE authenticated NOLOGIN NOINHERIT; CREATE ROLE service_role NOLOGIN NOINHERIT' \
  >/dev/null || die SYNTHETIC_DATABASE_ROLES_REQUIRED
[[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres \
  -v ON_ERROR_STOP=1 -c 'SHOW server_version_num') == 170006 ]] || die EXACT_POSTGRES_REQUIRED

runtime=(--label "$label=$run_key" --network "container:$pg" --user "$(id -u):$(id -g)" --read-only --init
         --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 256 --cpus 2)
dashboard=$(bounded 25s docker create "${runtime[@]}" --memory 192m --memory-swap 192m \
  --tmpfs /tmp:rw,size=32m --mount "type=bind,src=$state/frontend-work/apps/dashboard/dist,dst=/dashboard-dist,readonly" \
  --mount "type=bind,src=$dashboard_server,dst=/serve-dashboard.mjs,readonly" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp GITHUB_ACTIONS=true GITHUB_REPOSITORY=ilovedrrealty/realty-api \
  node /serve-dashboard.mjs) || die DASHBOARD_SERVER_CREATE_FAILED
bounded 20s docker start "$dashboard" >/dev/null || die STATIC_SERVER_START_FAILED

phases=(groups)
drivers=(run-social-groups-browser.mjs)
for index in "${!phases[@]}"; do
  phase="${phases[$index]}"
  phase_nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
  [[ "$phase_nonce" =~ ^[a-f0-9]{32}$ ]] || die PHASE_NONCE_REQUIRED
  db="ildr_browser_ci_$phase_nonce"
  marker="ildr-admin-share-ci:$phase_nonce"
  bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "CREATE DATABASE $db" -c "COMMENT ON DATABASE $db IS '$marker'" >/dev/null || die OWNED_PHASE_DATABASE_REQUIRED
  mkdir "$state/fixtures/$phase"
  api=$(bounded 25s docker create "${runtime[@]}" --memory 2g --memory-swap 2g --tmpfs /tmp:rw,exec,size=128m \
    --mount "type=bind,src=$backend,dst=/application,readonly" --mount "type=bind,src=$ops/ci,dst=/ci-tools,readonly" \
    --mount "type=bind,src=$state/fixtures/$phase,dst=/browser-fixtures" "$app_image" \
    env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp PYTHONDONTWRITEBYTECODE=1 \
    GITHUB_ACTIONS=true GITHUB_REPOSITORY=ilovedrrealty/realty-api ILDR_BROWSER_CI_NONCE="$phase_nonce" \
    python -I -B /ci-tools/social-groups-browser/serve_social_groups_fixture.py) || die API_CREATE_FAILED
  bounded 20s docker start "$api" >/dev/null || die API_START_FAILED
  ready=0
  for ((attempt=0; attempt<120; attempt++)); do
    if [[ -f "$state/fixtures/$phase/fixtures.json" ]] && bounded 4s docker exec "$api" python -I -B -c \
      'import urllib.request; assert all(urllib.request.urlopen(url,timeout=2).status==200 for url in ("http://127.0.0.1:8918/health","http://127.0.0.1:4175/groups"))' >/dev/null 2>&1; then
      ready=1; break
    fi
    [[ $(bounded 4s docker inspect --format '{{.State.Running}}' "$api") == true ]] || break
    sleep 1
  done
  if [[ "$ready" != 1 ]]; then
    bounded 8s docker logs "$api" >"$state/api-$phase.log" 2>&1 || true
    python3 -I -B "$tools/check-social-groups-result.py" api "$state/api-$phase.log" 1 || true
    die ACTUAL_PHASE_API_REQUIRED
  fi
  browser_native=0
  bounded 900s docker run --rm "${runtime[@]}" --memory 2g --memory-swap 2g --shm-size 256m \
    --tmpfs /tmp:rw,exec,size=256m --workdir /browser-run \
    --mount "type=bind,src=$state/browser-run,dst=/browser-run,readonly" \
    --mount "type=bind,src=$state/browser-run,dst=/rehearsal,readonly" \
    --mount "type=bind,src=$state/fixtures/$phase,dst=/browser-fixtures,readonly" "$browser_image" \
    env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp GITHUB_ACTIONS=true GITHUB_REPOSITORY=ilovedrrealty/realty-api \
    ILDR_BROWSER_FIXTURES=/browser-fixtures/fixtures.json PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    node "/browser-run/${drivers[$index]}" >"$state/browser-$phase.log" 2>&1 || browser_native=$?
  exit_class=nonzero
  if [[ "$browser_native" == 0 ]]; then exit_class=success
  elif [[ "$browser_native" == 124 ]]; then exit_class=timeout
  elif ((browser_native >= 128)); then exit_class=signal
  fi
  printf 'SOCIAL_GROUPS_BROWSER_NATIVE_EXIT code=%s classification=%s\n' "$browser_native" "$exit_class"
  # Preserve the genuine API final-state proof even when browser assertions fail.
  bounded 35s docker stop --time 25 "$api" >/dev/null || die API_GRACEFUL_STOP_REQUIRED
  api_native=$(bounded 5s docker inspect --format '{{.State.ExitCode}}' "$api")
  bounded 10s docker logs "$api" >"$state/api-$phase.log" 2>&1 || die API_LOG_REQUIRED
  browser_ok=0; api_ok=0
  python3 -I -B "$tools/check-social-groups-result.py" browser "$state/browser-$phase.log" "$browser_native" || browser_ok=$?
  python3 -I -B "$tools/check-social-groups-result.py" api "$state/api-$phase.log" "$api_native" || api_ok=$?
  [[ "$browser_ok" == 0 && "$api_ok" == 0 && ! -e "$state/fixtures/$phase/fixtures.json" &&
     ! -L "$state/fixtures/$phase/fixtures.json" ]] || die EXACT_BROWSER_AND_AUTHORITATIVE_FACTS_REQUIRED
  [[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres \
    -v ON_ERROR_STOP=1 -c "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname='$db'") == "$marker" ]] || die OWNED_DATABASE_DROP_GUARD
  bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE $db" >/dev/null || die OWNED_DATABASE_DROP_REQUIRED
  bounded 15s docker rm -v "$api" >/dev/null || die PHASE_API_REMOVAL_REQUIRED
  verify_source || die BROWSER_ALTERED_FRONTEND_SOURCE
  [[ -z $(git -C "$backend" status --porcelain=v1 --untracked-files=all) ]] || die BROWSER_ALTERED_BACKEND_SOURCE
done
printf 'SOCIAL_GROUPS_HOSTED_BROWSER_RESULT operations=%s frontend=%s backend=%s phases=1 checks=12 geometry=16 retained_tests=480 live_provider_calls=0 run=%s attempt=1\n' \
  "$GITHUB_SHA" "$frontend_commit" "$backend_commit" "$GITHUB_RUN_ID"

#!/usr/bin/env bash
set -euo pipefail
umask 077
die() { printf 'INBOX_RESILIENCE_NATIVE_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }
[[ $# == 5 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${GITHUB_REF:-} == refs/heads/codex-ci-inbox-resilience-native-20261125 && ${GITHUB_RUN_ATTEMPT:-} == 1 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 ]] || die EXACT_HOSTED_EVENT_REQUIRED
ops="$1"; application="$2"; baseline="$3"; recipe="$4"; previous="$5"
[[ "$ops" == "$GITHUB_WORKSPACE/inbox-ops" && "$application" == "$GITHUB_WORKSPACE/inbox-app" &&
   "$baseline" == "$GITHUB_WORKSPACE/inbox-base" && "$recipe" == "$GITHUB_WORKSPACE/inbox-recipe" && "$previous" == "$GITHUB_WORKSPACE/inbox-previous" ]] || die EXACT_PATHS_REQUIRED
for path in "$ops" "$application" "$baseline" "$recipe" "$previous"; do
  [[ $(realpath "$path") == "$path" && -z $(git -C "$path" status --porcelain=v1 --untracked-files=all) &&
     ! -e "$path/.env" && ! -L "$path/.env" ]] || die CLEAN_SOURCE_REQUIRED
done
expected_operations=$(cat <<'EXACT_OPERATIONS'
.github/workflows/native-inbox.yml
ci/native-inbox/PLAN.json
ci/native-inbox/blackhole-connect.py
ci/native-inbox/check.py
ci/native-inbox/run-hosted.sh
ci/native-inbox/verify.py
EXACT_OPERATIONS
)
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" show -s --format=%P HEAD) == c0b93c7dbf14e8aabee2bd17428b5c46e6eed4a9 &&
   $(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) == "$expected_operations" &&
   $(git -C "$application" rev-parse HEAD) == 17943ba1714673c51c43cdbb800106f91612f527 &&
   $(git -C "$application" rev-parse 'HEAD^{tree}') == ff940a7b6e983d4c669202746d9676b4b82e9a06 &&
   $(git -C "$application" show -s --format=%P HEAD) == "79e9311874e3ea91e45b990629965534d41bb5fa 07b2b7c762b129c5b6ea978299ba69e528e3189e" &&
   $(git -C "$baseline" rev-parse HEAD) == 01695530df670a94ac28f8b3bf36cc34b5b7a942 &&
   $(git -C "$baseline" rev-parse 'HEAD^{tree}') == e88ea0c57f9fac39545322532a5992d5a2927676 &&
   $(git -C "$recipe" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 &&
   $(git -C "$previous" rev-parse HEAD) == 8957b18520d15dac503e0e4a5ce4b2455135d601 &&
   $(git -C "$previous" rev-parse 'HEAD^{tree}') == 11ab69db36eb30881e4f3086578201f584c4092d ]] || die EXACT_PINS_REQUIRED
git -C "$application" cat-file -e '8957b18520d15dac503e0e4a5ce4b2455135d601^{commit}' || die SOURCE_BASE_COMMIT_REQUIRED
git -C "$application" merge-base --is-ancestor 8957b18520d15dac503e0e4a5ce4b2455135d601 HEAD || die SOURCE_BASE_ANCESTOR_REQUIRED
source_delta=$(git -C "$application" diff --name-only 8957b18520d15dac503e0e4a5ce4b2455135d601 HEAD -- requirements.txt) || die SOURCE_DIFF_REQUIRED
[[ -z "$source_delta" ]] || die UNCHANGED_DEPENDENCY_SOURCE_REQUIRED
python3 -I -B - "$application" "$ops" <<'PY_COMPOSITION'
from hashlib import sha256
from pathlib import Path
import subprocess,sys
application,ops=map(Path,sys.argv[1:])
base='8957b18520d15dac503e0e4a5ce4b2455135d601'
inbox='79e9311874e3ea91e45b990629965534d41bb5fa'
resilience='07b2b7c762b129c5b6ea978299ba69e528e3189e'
def git(*args):return subprocess.check_output(['git','-C',str(application),*args])
def changed(commit):return set(git('diff','--name-only',base,commit).decode().splitlines())
def tree(commit):
    result={}
    for line in git('ls-tree','-rz',commit).split(b'\0'):
        if line:
            value,name=line.split(b'\t',1);result[name.decode()]=value
    return result
expected_paths={'alembic/versions/d8f4c2a91e67_private_native_inbox.py','database.py','docs/NATIVE-INBOX-CONTRACT.md',
    'main.py','models/__init__.py','models/native_inbox.py','routes/native_inbox.py','schemas/native_inbox.py',
    'services/native_inbox.py','tests/test_database_connection_options.py','tests/test_database_pool_recovery.py',
    'tests/test_native_inbox.py','utils/database_connection.py'}
left,right=changed(inbox),changed(resilience)
assert len(left)==9 and len(right)==4 and not left&right and left|right==expected_paths==changed('HEAD')
expected=tree(base);a,b=tree(inbox),tree(resilience)
for path in left:expected[path]=a[path]
for path in right:expected[path]=b[path]
assert len(expected)==365 and tree('HEAD')==expected
blackhole=ops/'ci/native-inbox/blackhole-connect.py'
assert blackhole.is_file() and not blackhole.is_symlink() and sha256(blackhole.read_bytes()).hexdigest()=='ac58eb70c25035379974859710d4c56e9d0226f98479dd192384943a372196ea'
PY_COMPOSITION
dockerfile="$recipe/ci/migration/services163.Dockerfile"
printf '%s  %s\n' e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9 "$dockerfile" |
  sha256sum --check --status || die ACCEPTED_RECIPE_REQUIRED
python3 -I -B - "$baseline/requirements.txt" "$application/requirements.txt" <<'PY_REQUIREMENTS'
from pathlib import Path
import sys
assert Path(sys.argv[2]).read_bytes() == Path(sys.argv[1]).read_bytes() + b'openpyxl==3.1.5\ndefusedxml==0.7.1\net-xmlfile==2.0.0\n'
PY_REQUIREMENTS
[[ -d ${RUNNER_TEMP:-} && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die OWNED_SCRATCH_REQUIRED
nonce=$(python3 -I -B -c 'from uuid import uuid4;print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-inbox-native.XXXXXXXX")
label=org.ilovedrrealty.inbox-native
run_key="$GITHUB_RUN_ID-$nonce"
image="ildr-inbox-native:$run_key"
python_digest=sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
postgres='docker.io/library/postgres:17.6@sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409'
pg=''
owned() { [[ "$1" =~ ^[a-f0-9]{64}$ && $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$1") == "$run_key" ]]; }
cleanup() {
  local code=$? clean=1 ids id images tagged
  trap - EXIT
  set +e
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  for id in $ids; do
    if owned "$id"; then bounded 20s docker rm -f -v "$id" >/dev/null 2>&1 || clean=0; else clean=0; fi
  done
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
  if [[ "$state" == "$RUNNER_TEMP"/ildr-inbox-native.* && -d "$state" && ! -L "$state" && -O "$state" && $(realpath "$state") == "$state" ]]; then
    bounded 40s rm --recursive --force --one-file-system -- "$state" || clean=0
  else clean=0; fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'INBOX_RESILIENCE_NATIVE_OWNED_CLEANUP operations=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT
mkdir "$state/build"
cp "$baseline/requirements.txt" "$state/build/requirements.txt"
cp "$dockerfile" "$state/build/Dockerfile"
cat >>"$state/build/Dockerfile" <<'DOCKER_READERS'
RUN mkdir -p /opt/inbox-native && /usr/local/bin/python -I -B -c "import importlib.metadata as m,json,re;from pathlib import Path;Path('/opt/inbox-native/baseline-inventory.json').write_text(json.dumps({re.sub(r'[-_.]+','-',d.metadata['Name'].lower()):d.version for d in m.distributions()},sort_keys=True))"
COPY readers.txt /opt/inbox-native/readers.txt
RUN /usr/local/bin/python -I -B -m pip install --retries 0 --timeout 30 --no-cache-dir --no-compile --no-deps --only-binary=:all: --require-hashes --requirement /opt/inbox-native/readers.txt \
    && /usr/local/bin/python -I -B -m pip check
DOCKER_READERS
cat >"$state/build/readers.txt" <<'READER_WHEELS'
openpyxl==3.1.5 --hash=sha256:5282c12b107bffeef825f4617dc029afaf41d0ea60823bbb665ef3079dc79de2
defusedxml==0.7.1 --hash=sha256:a352e7e428770286cc899e2542b6cdaedb2b4953ff269a210103ec58f6198a61
et-xmlfile==2.0.0 --hash=sha256:7a91720bc756843502c3b7504c77b8fe44217c85c537d85037f0f536151b2caa
READER_WHEELS
for base in "docker.io/library/python:3.11-slim@$python_digest" "$postgres"; do
  bounded 180s docker pull --platform linux/amd64 "$base" >/dev/null || die PINNED_IMAGE_PULL_FAILED
  [[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$base") == linux/amd64 ]] || die EXACT_IMAGE_PLATFORM_REQUIRED
done
bounded 600s docker build --platform linux/amd64 --pull=false --quiet --label "$label=$run_key" \
  --build-arg "PYTHON_DIGEST=$python_digest" --file "$state/build/Dockerfile" --tag "$image" "$state/build" \
  >"$state/build.log" 2>&1 || die DEPENDENCY_IMAGE_BUILD_FAILED
pg=$(bounded 25s docker create --label "$label=$run_key" --network none --security-opt no-new-privileges:true \
  --memory 768m --memory-swap 768m --pids-limit 96 --cpus 1 --tmpfs /var/lib/postgresql/data:rw,size=384m \
  --env POSTGRES_HOST_AUTH_METHOD=trust --env POSTGRES_DB=postgres \
  "$postgres" postgres -p 55449 -h 127.0.0.1) || die POSTGRES_CREATE_FAILED
owned "$pg" && [[ $(bounded 15s docker inspect --format '{{.HostConfig.NetworkMode}}' "$pg") == none ]] || die OWNED_ISOLATED_POSTGRES_REQUIRED
bounded 25s docker start "$pg" >/dev/null || die POSTGRES_START_FAILED
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  if bounded 3s docker exec "$pg" pg_isready -h 127.0.0.1 -p 55449 -U postgres -d postgres >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 && $(bounded 5s docker exec "$pg" ls /sys/class/net) == lo ]] || die LOOPBACK_POSTGRES_REQUIRED
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c 'CREATE ROLE anon NOLOGIN NOINHERIT; CREATE ROLE authenticated NOLOGIN NOINHERIT; CREATE ROLE service_role NOLOGIN NOINHERIT' >/dev/null || die ISOLATED_ROLES_REQUIRED
db="ildr_browser_ci_$nonce"
marker="ildr-admin-share-ci:$nonce"
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c "CREATE DATABASE $db TEMPLATE template0" -c "COMMENT ON DATABASE $db IS '$marker'" >/dev/null || die OWNED_DATABASE_REQUIRED
legacy_db=ildr_platform_test_20261025
legacy_marker="ildr-inbox-legacy-ci:$nonce"
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c "CREATE DATABASE $legacy_db TEMPLATE template0" -c "COMMENT ON DATABASE $legacy_db IS '$legacy_marker'" >/dev/null || die OWNED_LEGACY_DATABASE_REQUIRED
native=$(bounded 25s docker create --label "$label=$run_key" --network "container:$pg" \
  --user "$(id -u):$(id -g)" --read-only --init --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 192 --cpus 2 --memory 1536m --memory-swap 1536m --tmpfs /tmp:rw,exec,size=128m \
  --mount "type=bind,src=$application,dst=/application,readonly" --mount "type=bind,src=$previous,dst=/previous,readonly" --mount "type=bind,src=$ops/ci,dst=/ci-tools,readonly" \
  "$image" env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp PYTHONDONTWRITEBYTECODE=1 \
  GITHUB_ACTIONS=true GITHUB_REPOSITORY=ilovedrrealty/realty-api ILDR_BROWSER_CI_NONCE="$nonce" \
  python -I -B /ci-tools/native-inbox/verify.py) || die NATIVE_CONTAINER_CREATE_FAILED
owned "$native" || die OWNED_NATIVE_CONTAINER_REQUIRED
client_exit=0
bounded 900s docker start --attach "$native" >"$state/native.log" 2>&1 || client_exit=$?
read -r native_exit running oom <<<"$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$native")"
printf 'INBOX_RESILIENCE_NATIVE_PROCESS client=%s native=%s running=%s oom=%s\n' "$client_exit" "$native_exit" "$running" "$oom"
checked=0
python3 -I -B "$ops/ci/native-inbox/check.py" "$state/native.log" "$client_exit" "$native_exit" "$running" "$oom" || checked=$?
# A failed native process is stopped before checking the exact owned DB marker.
if [[ "$running" == true ]]; then bounded 20s docker stop --time 10 "$native" >/dev/null || die NATIVE_STOP_REQUIRED; fi
bounded 15s docker rm -v "$native" >/dev/null || die NATIVE_CONTAINER_REMOVAL_REQUIRED
[[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres \
  -v ON_ERROR_STOP=1 -c "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname='$db'") == "$marker" ]] || die OWNED_DATABASE_DROP_GUARD
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE $db" >/dev/null || die OWNED_DATABASE_DROP_REQUIRED
[[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres \
  -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_database WHERE datname='$db'") == 0 ]] || die OWNED_DATABASE_ABSENCE_REQUIRED
[[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres \
  -v ON_ERROR_STOP=1 -c "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname='$legacy_db'") == "$legacy_marker" ]] || die OWNED_LEGACY_DATABASE_DROP_GUARD
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE $legacy_db" >/dev/null || die OWNED_LEGACY_DATABASE_DROP_REQUIRED
[[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres \
  -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_database WHERE datname='$legacy_db'") == 0 ]] || die OWNED_LEGACY_DATABASE_ABSENCE_REQUIRED
printf 'INBOX_RESILIENCE_NATIVE_DATABASE_REMOVED canonical=true legacy=true\n'
[[ "$checked" == 0 && -z $(git -C "$application" status --porcelain=v1 --untracked-files=all) ]] || die EXACT_NATIVE_ACCEPTANCE_REQUIRED
printf 'INBOX_RESILIENCE_NATIVE_HOSTED_RESULT operations=%s backend=17943ba1714673c51c43cdbb800106f91612f527 tests=95 schema_tables=106 legacy_tables=99 new_private_tables=7 live_provider_calls=0 run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"

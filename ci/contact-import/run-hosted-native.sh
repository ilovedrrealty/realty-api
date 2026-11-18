#!/usr/bin/env bash
set -euo pipefail
die() { printf 'CONTACT_IMPORT_NATIVE_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }
[[ $# == 4 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${GITHUB_REF:-} == refs/heads/codex-ci-contact-import-native-20261118 && ${GITHUB_RUN_ATTEMPT:-} == 1 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 ]] || die EXACT_HOSTED_EVENT_REQUIRED
ops="$1"; application="$2"; baseline="$3"; recipe="$4"
[[ "$ops" == "$GITHUB_WORKSPACE/contact-ops" && "$application" == "$GITHUB_WORKSPACE/contact-app" &&
   "$baseline" == "$GITHUB_WORKSPACE/contact-base" && "$recipe" == "$GITHUB_WORKSPACE/contact-recipe" ]] || die EXACT_PATHS_REQUIRED
for path in "$ops" "$application" "$baseline" "$recipe"; do
  [[ $(realpath "$path") == "$path" && -z $(git -C "$path" status --porcelain=v1 --untracked-files=all) &&
     ! -e "$path/.env" && ! -L "$path/.env" ]] || die CLEAN_SOURCE_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" show -s --format=%P HEAD) == 01695530df670a94ac28f8b3bf36cc34b5b7a942 &&
   $(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) == $'.github/workflows/contact-import-native.yml\nci/contact-import/run-hosted-native.sh\nci/contact-import/verify-contact-import-native.py' &&
   $(git -C "$application" rev-parse HEAD) == 161be4464b1f134e53e9c89b3b93222ea7617bc9 &&
   $(git -C "$application" rev-parse 'HEAD^{tree}') == 6227e5a3cae0d4835c31bd2400ec19a04c2262c0 &&
   $(git -C "$baseline" rev-parse HEAD) == 01695530df670a94ac28f8b3bf36cc34b5b7a942 &&
   $(git -C "$baseline" rev-parse 'HEAD^{tree}') == e88ea0c57f9fac39545322532a5992d5a2927676 &&
   $(git -C "$recipe" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 ]] || die EXACT_PINS_REQUIRED
[[ -z $(git -C "$application" diff --name-only 01695530df670a94ac28f8b3bf36cc34b5b7a942 HEAD -- alembic models) ]] || die EXISTING_SCHEMA_BYTES_REQUIRED
git -C "$application" merge-base --is-ancestor 01695530df670a94ac28f8b3bf36cc34b5b7a942 HEAD || die EXACT_LIVE_ANCESTOR_REQUIRED
[[ $(git -C "$application" diff --name-only 01695530df670a94ac28f8b3bf36cc34b5b7a942 HEAD) == $'requirements.txt\nroutes/trade_contacts.py\nschemas/trade_contact_files.py\nschemas/trade_contacts.py\nservices/trade_contact_files.py\nservices/trade_contacts.py\ntests/test_admin_trade_contacts.py\ntests/test_trade_contact_files.py' ]] || die EXACT_CONTACT_SLICE_REQUIRED
dockerfile="$recipe/ci/migration/services163.Dockerfile"
printf '%s  %s\n' e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9 "$dockerfile" |
  sha256sum --check --status || die ACCEPTED_RECIPE_REQUIRED
python3 -I -B - "$baseline/requirements.txt" "$application/requirements.txt" <<'PY_REQUIREMENTS'
from pathlib import Path
import sys
assert Path(sys.argv[2]).read_bytes() == Path(sys.argv[1]).read_bytes() + b'openpyxl==3.1.5\ndefusedxml==0.7.1\net-xmlfile==2.0.0\n'
PY_REQUIREMENTS
umask 077
[[ -d ${RUNNER_TEMP:-} && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die OWNED_SCRATCH_REQUIRED
nonce=$(python3 -I -B -c 'from uuid import uuid4;print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-contact-native.XXXXXXXX")
label=org.ilovedrrealty.contact-import-native
run_key="$GITHUB_RUN_ID-$nonce"
image="ildr-contact-native:$run_key"
python_digest=sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
postgres='docker.io/library/postgres:17.6@sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409'
database="ildr_contacts_$nonce"
pg=''
owned() { [[ "$1" =~ ^[a-f0-9]{64}$ && $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$1") == "$run_key" ]]; }
cleanup() {
  local code=$? clean=1 ids id images tagged
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
  if [[ "$state" == "$RUNNER_TEMP"/ildr-contact-native.* && -d "$state" && ! -L "$state" && $(realpath "$state") == "$state" ]]; then
    rm -rf -- "$state" || clean=0
  else clean=0; fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'CONTACT_IMPORT_NATIVE_OWNED_CLEANUP commit=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT
mkdir "$state/build"
cp "$baseline/requirements.txt" "$state/build/requirements.txt"
cp "$dockerfile" "$state/build/Dockerfile"
cat >>"$state/build/Dockerfile" <<'DOCKER_READERS'
RUN mkdir -p /opt/contact-import && /usr/local/bin/python -I -B -c "import importlib.metadata as m,json,re;from pathlib import Path;Path('/opt/contact-import/baseline-inventory.json').write_text(json.dumps({re.sub(r'[-_.]+','-',d.metadata['Name'].lower()):d.version for d in m.distributions()},sort_keys=True))"
COPY readers.txt /opt/contact-import/readers.txt
RUN /usr/local/bin/python -I -B -m pip install --retries 0 --timeout 30 --no-cache-dir --no-compile --no-deps --only-binary=:all: --require-hashes --requirement /opt/contact-import/readers.txt \
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
  --env POSTGRES_HOST_AUTH_METHOD=trust --env "POSTGRES_DB=$database" \
  "$postgres" postgres -p 55449 -h 127.0.0.1) || die POSTGRES_CREATE_FAILED
owned "$pg" && [[ $(bounded 15s docker inspect --format '{{.HostConfig.NetworkMode}}' "$pg") == none ]] || die OWNED_ISOLATED_POSTGRES_REQUIRED
bounded 25s docker start "$pg" >/dev/null || die POSTGRES_START_FAILED
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  if bounded 3s docker exec "$pg" pg_isready -h 127.0.0.1 -p 55449 -U postgres -d "$database" >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 && $(bounded 5s docker exec "$pg" ls /sys/class/net) == lo ]] || die LOOPBACK_POSTGRES_REQUIRED
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d "$database" -v ON_ERROR_STOP=1 \
  -c 'CREATE ROLE anon NOLOGIN NOINHERIT; CREATE ROLE authenticated NOLOGIN NOINHERIT; CREATE ROLE service_role NOLOGIN NOINHERIT' \
  -c 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role' \
  -c "COMMENT ON DATABASE $database IS 'ildr-contact-import-ci:$nonce'" >/dev/null || die DATABASE_MARKER_REQUIRED
container=$(bounded 25s docker create --label "$label=$run_key" --network "container:$pg" \
  --user "$(id -u):$(id -g)" --read-only --init --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 192 --cpus 2 --memory 1536m --memory-swap 1536m --tmpfs /tmp:rw,exec,size=128m \
  --env GITHUB_ACTIONS=true --env GITHUB_REPOSITORY=ilovedrrealty/realty-api --env "ILDR_CONTACT_CI_NONCE=$nonce" \
  --env "DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:55449/$database" \
  --mount "type=bind,src=$application,dst=/application,readonly" --mount "type=bind,src=$ops/ci/contact-import,dst=/tools,readonly" \
  "$image" /usr/local/bin/python -I -B /tools/verify-contact-import-native.py) || die NATIVE_CONTAINER_CREATE_FAILED
owned "$container" || die OWNED_NATIVE_CONTAINER_REQUIRED
native_exit=0
bounded 300s docker start --attach "$container" >"$state/native.log" 2>&1 || native_exit=$?
native_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$container")
printf 'CONTACT_IMPORT_NATIVE_PROCESS client=%s state=%s\n' "$native_exit" "$native_state"
python3 -I -B - "$state/native.log" <<'PY_REPORT'
from pathlib import Path
import json,re,sys
path=Path(sys.argv[1]);assert path.stat().st_size<=131072
prefix=b'CONTACT_IMPORT_NATIVE_RESULT '
rows=[json.loads(line[len(prefix):]) for line in path.read_bytes().splitlines() if line.startswith(prefix)]
assert len(rows)==1
report=rows[0]
assert report.get('status') in {'PASS','FAIL'}
for suite in report.get('suites',[]):
    assert (suite['module'],suite['class'],suite['expected']) in {
        ('test_trade_contact_files','ContactFileReaderTest',11),('test_admin_trade_contacts','TradeContactsTest',6)}
    for diagnostic in suite['diagnostics']:
        assert re.fullmatch(r'test_[a-z_]+|setup_or_cleanup',diagnostic['test'])
        assert re.fullmatch(r'[A-Za-z_]{1,80}',diagnostic['error_type'])
print('CONTACT_IMPORT_NATIVE_RESULT '+json.dumps(report,sort_keys=True,separators=(',',':')))
assert report.get('unexpected_transport_attempts')==0
assert report['status']=='PASS' and report['tests']==17 and report['schema_tables']==92
PY_REPORT
[[ "$native_exit" == 0 && "$native_state" == '0 false false' ]] || die NATIVE_RUNTIME_FAILED
for path in "$application" "$baseline" "$recipe"; do
  [[ -z $(git -C "$path" status --porcelain=v1 --untracked-files=all) ]] || die MOUNTED_SOURCE_CHANGED
done
printf 'CONTACT_IMPORT_NATIVE_ACCEPTANCE commit=%s application=%s tests=17 run=%s attempt=1\n' \
  "$GITHUB_SHA" 161be4464b1f134e53e9c89b3b93222ea7617bc9 "$GITHUB_RUN_ID"

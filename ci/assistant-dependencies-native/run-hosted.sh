#!/usr/bin/env bash
set -euo pipefail
umask 077
die() { printf 'ASSISTANT_DEPENDENCIES_NATIVE_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }
[[ $# == 4 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${GITHUB_REF:-} == refs/heads/codex-ci-assistant-dependencies-native-20261127 && ${GITHUB_RUN_ATTEMPT:-} == 1 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 ]] || die EXACT_HOSTED_EVENT_REQUIRED
ops="$1"; application="$2"; dependencies="$3"; recipe="$4"
[[ "$ops" == "$GITHUB_WORKSPACE/assistant-ops" && "$application" == "$GITHUB_WORKSPACE/assistant-app" &&
   "$dependencies" == "$GITHUB_WORKSPACE/assistant-dependencies" && "$recipe" == "$GITHUB_WORKSPACE/assistant-recipe" ]] || die EXACT_PATHS_REQUIRED
for path in "$ops" "$application" "$dependencies" "$recipe"; do
  [[ $(realpath "$path") == "$path" && ! -e "$path/.env" && ! -L "$path/.env" ]] || die EXACT_CLEAN_PATH_REQUIRED
done
python3 -I -S -B - "$ops" "$application" "$dependencies" "$recipe" <<'PY_SOURCE'
import ast,hashlib,json,os,re,subprocess,sys
from pathlib import Path
ops,application,dependencies,recipe=map(Path,sys.argv[1:])
plan=json.loads((ops/'ci/assistant-dependencies-native/PLAN.json').read_bytes())
def git(root,*args):return subprocess.check_output(['git','-C',str(root),*args]).decode().strip()
def tree(root,ref):return {p.decode():m.decode() for x in subprocess.check_output(['git','-C',str(root),'ls-tree','-rz',ref]).split(b'\0') if x for m,p in [x.split(b'\t',1)]}
for root in (ops,application,dependencies,recipe):assert not git(root,'status','--porcelain=v1','--untracked-files=all')
paths={'.github/workflows/assistant-dependencies-native.yml',*[f'ci/assistant-dependencies-native/{p}' for p in ('PLAN.json','run-hosted.sh','verify.py','check-result.py')]}
assert git(ops,'rev-parse','HEAD')==os.environ['GITHUB_SHA']
assert git(ops,'show','-s','--format=%P','HEAD')==plan['operations_parent']=='51f6d1d7c16b2f68a826e3bcd49d64363f8345e1'
assert git(ops,'rev-parse','HEAD^^{tree}')==plan['operations_parent_tree']=='ca23cd51871376817842d671e976403d7809154f'
old,current=tree(ops,'HEAD^'),tree(ops,'HEAD')
assert len(old)==446 and len(current)==451 and current.keys()-old.keys()==paths and all(current[p]==v for p,v in old.items())
assert set(git(ops,'diff','--name-only','HEAD^','HEAD').splitlines())==paths
assert set(git(ops,'diff','--diff-filter=A','--name-only','HEAD^','HEAD').splitlines())==paths
pin=plan['application']
assert git(application,'rev-parse','HEAD')==pin['commit']=='9a422618139ad3a01bc8c0b3fd38ae5dd7f42f41'
assert git(application,'rev-parse','HEAD^{tree}')==pin['tree']=='b09f1c01af1260225778f915dc3cd79b10807525'
parents=['53d730a1676abde47eae5609925e74ffad7ede21','8f57a60cca3cf9e8568eb52e64e5f6d50c0db9b4']
assert git(application,'show','-s','--format=%P','HEAD').split()==pin['parents']==parents
assert all(git(application,'show','-s','--format=%P',p)==pin['facts_parent']=='625cc7a372faf1edaf668d80ab635f78937263b9' for p in parents)
assert git(application,'rev-parse',pin['facts_parent']+'^{tree}')==pin['facts_tree']=='dee4f72557415f39f1abef6146ffb6389c9c10b7'
assert git(application,'show','-s','--format=%P',pin['facts_parent'])==pin['listing_source']=='875ade946f633ce71bec23cd0c2f6bf79b7dc2ed'
assert git(application,'rev-parse',pin['listing_source']+'^{tree}')==pin['listing_tree']=='9ccf81ad0b67fa2e0b678974479458697def8f51'
listing,facts,reads,help_,current=[tree(application,p) for p in [pin['listing_source'],pin['facts_parent'],*parents,'HEAD']]
fact_paths={'schemas/public_assistant_listing.py','services/public_assistant_listing.py','tests/test_public_assistant_listing.py'}
read_paths={'services/public_assistant_listing_reads.py','tests/test_public_assistant_listing_reads.py'}
help_paths={'services/assistant_feature_help.py','services/assistant_feature_help.json','tests/test_assistant_feature_help.py'}
assert [len(t) for t in [listing,facts,reads,help_,current]]==[375,378,380,381,383]
for older,newer,added in [(listing,facts,fact_paths),(facts,reads,read_paths),(facts,help_,help_paths),(reads,current,help_paths)]:
 assert newer.keys()-older.keys()==added and all(newer[p]==v for p,v in older.items())
assert current=={**facts,**{p:reads[p] for p in read_paths},**{p:help_[p] for p in help_paths}}
assert set(pin['files'])==fact_paths|read_paths|help_paths and pin['expected_models']==107
for path,value in pin['files'].items():
 raw=(application/path).read_bytes();assert current[path]==value['tuple'] and len(raw)==value['bytes'] and hashlib.sha256(raw).hexdigest()==value['sha256']
assert {mode:s['count'] for mode,s in plan['suites'].items()}=={'facts':9,'wiring':3,'reads':6,'help':7}
assert sum(s['count'] for s in plan['suites'].values())==25
for mode,spec in plan['suites'].items():
 raw=(application/spec['file']).read_bytes();assert hashlib.sha256(raw).hexdigest()==spec['sha256']
 classes=[n for n in ast.parse(raw).body if isinstance(n,ast.ClassDef) and n.name==spec['class']];assert len(classes)==1
 names=[n.name for n in classes[0].body if isinstance(n,ast.FunctionDef) and n.name.startswith('test_')]
 assert names==spec['all_source_methods']==spec['methods'] and len(names)==len(set(names))==spec['count']
for path,digest in plan['helper_pins'].items():assert hashlib.sha256((ops/path).read_bytes()).hexdigest()==digest
runtime=plan['runtime']
for directory,key in ((dependencies,'dependency'),(recipe,'recipe')):
 assert git(directory,'rev-parse','HEAD')==runtime[key+'_commit'] and git(directory,'rev-parse','HEAD^{tree}')==runtime[key+'_tree']
assert (application/'requirements.txt').read_bytes()==(dependencies/'requirements.txt').read_bytes()+b'openpyxl==3.1.5\ndefusedxml==0.7.1\net-xmlfile==2.0.0\n'
assert hashlib.sha256((recipe/'ci/migration/services163.Dockerfile').read_bytes()).hexdigest()==runtime['dockerfile_sha256']
print('ASSISTANT_DEPENDENCIES_NATIVE_SOURCE exact=true inherited_paths=446 source_paths=383 cases=25 facts=9 wiring=3 pg_reads=6 help=7 frontend_build=false genuine_migration=false')
PY_SOURCE
[[ -d ${RUNNER_TEMP:-} && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die OWNED_SCRATCH_REQUIRED
nonce=$(python3 -I -S -B -c 'from uuid import uuid4;print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-assistant-native.XXXXXXXX")
label=org.ilovedrrealty.assistant-dependencies-native
run_key="$GITHUB_RUN_ID-$nonce"
image="ildr-assistant-native:$run_key"
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
  if [[ "$state" == "$RUNNER_TEMP"/ildr-assistant-native.* && -d "$state" && ! -L "$state" && -O "$state" && $(realpath "$state") == "$state" ]]; then
    bounded 40s rm --recursive --force --one-file-system -- "$state" || clean=0
  else clean=0; fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'ASSISTANT_DEPENDENCIES_NATIVE_OWNED_CLEANUP operations=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT
mkdir "$state/build"
cp "$dependencies/requirements.txt" "$state/build/requirements.txt"
cp "$recipe/ci/migration/services163.Dockerfile" "$state/build/Dockerfile"
cat >>"$state/build/Dockerfile" <<'DOCKER_READERS'
RUN mkdir -p /opt/assignment-native && /usr/local/bin/python -I -B -c "import importlib.metadata as m,json,re;from pathlib import Path;Path('/opt/assignment-native/baseline-inventory.json').write_text(json.dumps({re.sub(r'[-_.]+','-',d.metadata['Name'].lower()):d.version for d in m.distributions()},sort_keys=True))"
COPY readers.txt /opt/assignment-native/readers.txt
RUN /usr/local/bin/python -I -B -m pip install --retries 0 --timeout 30 --no-cache-dir --no-compile --no-deps --only-binary=:all: --require-hashes --requirement /opt/assignment-native/readers.txt \
    && /usr/local/bin/python -I -B -m pip check
DOCKER_READERS
cat >"$state/build/readers.txt" <<'READER_WHEELS'
openpyxl==3.1.5 --hash=sha256:5282c12b107bffeef825f4617dc029afaf41d0ea60823bbb665ef3079dc79de2
defusedxml==0.7.1 --hash=sha256:a352e7e428770286cc899e2542b6cdaedb2b4953ff269a210103ec58f6198a61
et-xmlfile==2.0.0 --hash=sha256:7a91720bc756843502c3b7504c77b8fe44217c85c537d85037f0f536151b2caa
READER_WHEELS
python_digest=sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
postgres='docker.io/library/postgres:17.6@sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409'
for base in "docker.io/library/python:3.11-slim@$python_digest" "$postgres"; do
  bounded 180s docker pull --platform linux/amd64 "$base" >/dev/null || die PINNED_IMAGE_PULL_FAILED
  [[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$base") == linux/amd64 ]] || die EXACT_IMAGE_PLATFORM_REQUIRED
done
bounded 600s docker build --platform linux/amd64 --pull=false --quiet --label "$label=$run_key" \
  --build-arg "PYTHON_DIGEST=$python_digest" --file "$state/build/Dockerfile" --tag "$image" "$state/build" \
  >"$state/build.log" 2>&1 || die DEPENDENCY_IMAGE_BUILD_FAILED
pg=$(bounded 25s docker create --label "$label=$run_key" --network none --security-opt no-new-privileges:true \
  --memory 768m --memory-swap 768m --pids-limit 96 --cpus 1 --tmpfs /var/lib/postgresql/data:rw,size=384m \
  --env POSTGRES_HOST_AUTH_METHOD=trust --env POSTGRES_DB=postgres "$postgres" postgres -p 55449 -h 127.0.0.1) || die POSTGRES_CREATE_FAILED
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
server_start=$(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 -c 'SELECT pg_postmaster_start_time()::text')
[[ -n "$server_start" ]] || die POSTMASTER_START_REQUIRED
overall=0
unit_db=ildr_platform_test_20261025
for mode in facts wiring reads help; do
  marker="ildr-assistant-dependencies-$mode-ci:$nonce"
  [[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_database WHERE datname='$unit_db'") == 0 ]] || die FRESH_MODE_DATABASE_ABSENCE_REQUIRED
  bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "CREATE DATABASE $unit_db TEMPLATE template0" -c "COMMENT ON DATABASE $unit_db IS '$marker'" >/dev/null || die OWNED_MODE_DATABASE_CREATE_REQUIRED
  native=$(bounded 25s docker create --label "$label=$run_key" --network "container:$pg" \
    --user "$(id -u):$(id -g)" --read-only --init --cap-drop ALL --security-opt no-new-privileges:true \
    --pids-limit 192 --cpus 2 --memory 1536m --memory-swap 1536m --tmpfs /tmp:rw,exec,size=128m \
    --mount "type=bind,src=$application,dst=/application,readonly" --mount "type=bind,src=$ops/ci,dst=/ci-tools,readonly" \
    "$image" env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp PYTHONDONTWRITEBYTECODE=1 \
    GITHUB_ACTIONS=true GITHUB_REPOSITORY=ilovedrrealty/realty-api ILDR_BROWSER_CI_NONCE="$nonce" \
    python -I -B /ci-tools/assistant-dependencies-native/verify.py "$mode") || die NATIVE_CONTAINER_CREATE_FAILED
  owned "$native" || die OWNED_NATIVE_CONTAINER_REQUIRED
  client_exit=0
  bounded 180s docker start --attach "$native" >"$state/$mode.log" 2>&1 || client_exit=$?
  read -r native_exit running oom <<<"$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$native")"
  [[ "$client_exit" =~ ^[0-9]{1,3}$ && "$native_exit" =~ ^[0-9]{1,3}$ && "$running" =~ ^(true|false)$ && "$oom" =~ ^(true|false)$ ]] || die TYPED_PROCESS_FACTS_REQUIRED
  printf 'ASSISTANT_DEPENDENCIES_NATIVE_PROCESS mode=%s client=%s native=%s running=%s oom=%s\n' "$mode" "$client_exit" "$native_exit" "$running" "$oom"
  python3 -I -S -B "$ops/ci/assistant-dependencies-native/check-result.py" "$mode" "$state/$mode.log" \
    "$client_exit" "$native_exit" "$running" "$oom" || overall=1
  if [[ "$running" == true ]]; then bounded 20s docker stop --time 10 "$native" >/dev/null || die NATIVE_STOP_REQUIRED; fi
  [[ $(bounded 10s docker inspect --format '{{.State.Running}}' "$native") == false ]] || die NATIVE_STOPPED_REQUIRED
  [[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname='$unit_db'") == "$marker" ]] || die OWNED_MODE_DROP_GUARD_REQUIRED
  bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE $unit_db" >/dev/null || die OWNED_MODE_DATABASE_DROP_REQUIRED
  [[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "SELECT count(*) FROM pg_database WHERE datname='$unit_db'") == 0 ]] || die OWNED_MODE_DATABASE_ABSENCE_REQUIRED
  bounded 15s docker rm -v "$native" >/dev/null || die NATIVE_CONTAINER_REMOVAL_REQUIRED
  printf 'ASSISTANT_DEPENDENCIES_NATIVE_DATABASE_REMOVED mode=%s absent=true stopped_client=true\n' "$mode"
done
read -r pg_running pg_oom <<<"$(bounded 15s docker inspect --format '{{.State.Running}} {{.State.OOMKilled}}' "$pg")"
[[ "$pg_running" =~ ^(true|false)$ && "$pg_oom" =~ ^(true|false)$ ]] || die TYPED_POSTGRES_FACTS_REQUIRED
same_start=false
[[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 -c 'SELECT pg_postmaster_start_time()::text') != "$server_start" ]] || same_start=true
printf 'ASSISTANT_DEPENDENCIES_NATIVE_POSTGRES running=%s oom=%s same_start=%s\n' "$pg_running" "$pg_oom" "$same_start"
[[ "$overall" == 0 && "$pg_running" == true && "$pg_oom" == false && "$same_start" == true ]] || die EXACT_FOCUSED_ACCEPTANCE_REQUIRED
[[ -z $(git -C "$application" status --porcelain=v1 --untracked-files=all) && -z $(git -C "$ops" status --porcelain=v1 --untracked-files=all) ]] || die SOURCE_ALTERED_BY_NATIVE
printf 'ASSISTANT_DEPENDENCIES_NATIVE_HOSTED_RESULT operations=%s source=9a422618139ad3a01bc8c0b3fd38ae5dd7f42f41 cases=25 facts=9 wiring=3 pg_reads=6 help=7 modes=4 providers=0 genuine_migration=false http_wire=false model_or_auth_adapter=false frontend_build=false run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"

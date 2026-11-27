#!/usr/bin/env bash
set -euo pipefail
umask 077
die() { printf 'ASSIGNMENT_NATIVE_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }
[[ $# == 6 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${GITHUB_REF:-} == refs/heads/codex-ci-assignment-notifications-native-v2-20261127 && ${GITHUB_RUN_ATTEMPT:-} == 1 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 ]] || die EXACT_HOSTED_EVENT_REQUIRED
ops="$1"; application="$2"; reference="$3"; previous="$4"; dependencies="$5"; recipe="$6"
[[ "$ops" == "$GITHUB_WORKSPACE/assignment-ops" && "$application" == "$GITHUB_WORKSPACE/assignment-app" &&
   "$reference" == "$GITHUB_WORKSPACE/assignment-baseline106" && "$previous" == "$GITHUB_WORKSPACE/assignment-previous99" &&
   "$dependencies" == "$GITHUB_WORKSPACE/assignment-dependencies" && "$recipe" == "$GITHUB_WORKSPACE/assignment-recipe" ]] || die EXACT_PATHS_REQUIRED
for path in "$ops" "$application" "$reference" "$previous" "$dependencies" "$recipe"; do
  [[ $(realpath "$path") == "$path" && ! -e "$path/.env" && ! -L "$path/.env" ]] || die EXACT_CLEAN_PATH_REQUIRED
done
python3 -I -S -B - "$ops" "$application" "$reference" "$previous" "$dependencies" "$recipe" <<'PY_SOURCE'
import hashlib,json,re,subprocess,sys
from pathlib import Path
ops,application,reference,previous,dependencies,recipe=map(Path,sys.argv[1:])
plan=json.loads((ops/'ci/assignment-notifications-native/PLAN.json').read_bytes())
def git(root,*args):
    return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL).decode().strip()
def tree(root,rev):
    raw=subprocess.check_output(['git','-C',str(root),'ls-tree','-rz',rev])
    return {p.decode():meta.decode() for item in raw.split(b'\0') if item for meta,p in [item.split(b'\t',1)]}
for root in (ops,application,reference,previous,dependencies,recipe):
    assert not git(root,'status','--porcelain=v1','--untracked-files=all')
expected=['.github/workflows/assignment-notifications-native.yml','ci/assignment-notifications-native/PLAN.json',
          'ci/assignment-notifications-native/check-result.py','ci/assignment-notifications-native/run-hosted.sh',
          'ci/assignment-notifications-native/verify.py']
assert git(ops,'rev-parse','HEAD')==__import__('os').environ['GITHUB_SHA']
assert git(ops,'show','-s','--format=%P','HEAD')==plan['operations_parent']=='2cfb0169f9fc904f374c209d6b8c0a21e80583b1'
old,current=tree(ops,'HEAD^'),tree(ops,'HEAD')
assert len(old)==431 and len(current)==436 and set(current)-set(old)==set(expected)
assert all(current[p]==v for p,v in old.items())
assert git(ops,'diff-tree','--no-commit-id','--name-only','-r','HEAD^','HEAD').splitlines()==expected
for root,pin in ((application,plan['application']),(reference,plan['baseline106']),(previous,plan['previous99'])):
    assert all(type(pin[k]) is str and re.fullmatch('[a-f0-9]{40}',pin[k]) for k in ('commit','tree'))
    assert git(root,'rev-parse','HEAD')==pin['commit'] and git(root,'rev-parse','HEAD^{tree}')==pin['tree']
assert git(application,'show','-s','--format=%P','HEAD').split()==plan['application']['parents']
assert git(dependencies,'rev-parse','HEAD')=='01695530df670a94ac28f8b3bf36cc34b5b7a942'
assert git(dependencies,'rev-parse','HEAD^{tree}')=='e88ea0c57f9fac39545322532a5992d5a2927676'
assert git(recipe,'rev-parse','HEAD')=='c193a925ba57ea2d67ea48c44e5262817885c8a2'
assert git(recipe,'rev-parse','HEAD^{tree}')=='10edb138fba1d201e66042d25ba022313b300cc2'
base=plan['baseline106']['commit']
subprocess.run(['git','-C',str(application),'cat-file','-e',base+'^{commit}'],check=True,stdout=subprocess.DEVNULL)
subprocess.run(['git','-C',str(application),'merge-base','--is-ancestor',base,'HEAD'],check=True)
before,after=tree(application,base),tree(application,'HEAD')
changed={p for p in set(before)|set(after) if before.get(p)!=after.get(p)}
assert len(before)==367 and changed==set(plan['application']['changed_paths_from_baseline'])
assert all(after[p]==v for p,v in before.items() if p not in changed)
assert set(plan['application']['file_hashes'])==changed
for name,digest in plan['application']['file_hashes'].items():
    assert hashlib.sha256((application/name).read_bytes()).hexdigest()==digest
for name in ('requirements.txt','database.py','bootstrap_db.py','alembic/env.py'):
    assert before[name]==after[name] and (application/name).read_bytes()==(reference/name).read_bytes()
assert (application/'requirements.txt').read_bytes()==(dependencies/'requirements.txt').read_bytes()+b'openpyxl==3.1.5\ndefusedxml==0.7.1\net-xmlfile==2.0.0\n'
assert set(plan['source_suites'])=={'assignment','admin_assignment','lead_tracking'}
total=0
for suite in plan['source_suites'].values():
    assert type(suite['count']) is int and 1<=suite['count']<=200 and type(suite['methods']) is list
    assert len(suite['methods'])==len(set(suite['methods']))==suite['count']
    assert all(type(m) is str and re.fullmatch('test_[a-z0-9_]{1,180}',m) for m in suite['methods'])
    assert hashlib.sha256((application/suite['file']).read_bytes()).hexdigest()==suite['test_file_sha256']
    total+=suite['count']
for relative,digest in plan['helper_pins'].items():
    assert hashlib.sha256((ops/relative).read_bytes()).hexdigest()==digest
assert hashlib.sha256((recipe/'ci/migration/services163.Dockerfile').read_bytes()).hexdigest()==plan['runtime']['dockerfile_sha256']
print('ASSIGNMENT_NATIVE_SOURCE exact=true inherited_paths=431 existing_schema=106 future_schema=108 suites=3 current_source_inventory='+str(total))
PY_SOURCE
[[ -d ${RUNNER_TEMP:-} && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die OWNED_SCRATCH_REQUIRED
nonce=$(python3 -I -S -B -c 'from uuid import uuid4;print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-assignment-native.XXXXXXXX")
label=org.ilovedrrealty.assignment-native
run_key="$GITHUB_RUN_ID-$nonce"
image="ildr-assignment-native:$run_key"
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
  if [[ "$state" == "$RUNNER_TEMP"/ildr-assignment-native.* && -d "$state" && ! -L "$state" && -O "$state" && $(realpath "$state") == "$state" ]]; then
    bounded 40s rm --recursive --force --one-file-system -- "$state" || clean=0
  else clean=0; fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'ASSIGNMENT_NATIVE_OWNED_CLEANUP operations=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
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
canonical_db="ildr_assignment_ci_$nonce"
unit_db=ildr_platform_test_20261025
for mode in canonical unit; do
  db="$canonical_db"; [[ "$mode" == canonical ]] || db="$unit_db"
  marker="ildr-assignment-$mode-ci:$nonce"
  bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "CREATE DATABASE $db TEMPLATE template0" -c "COMMENT ON DATABASE $db IS '$marker'" >/dev/null || die OWNED_DATABASE_CREATE_REQUIRED
done
overall=0
for mode in canonical assignment admin_assignment lead_tracking; do
  native=$(bounded 25s docker create --label "$label=$run_key" --network "container:$pg" \
    --user "$(id -u):$(id -g)" --read-only --init --cap-drop ALL --security-opt no-new-privileges:true \
    --pids-limit 192 --cpus 2 --memory 1536m --memory-swap 1536m --tmpfs /tmp:rw,exec,size=128m \
    --mount "type=bind,src=$application,dst=/application,readonly" --mount "type=bind,src=$previous,dst=/previous,readonly" \
    --mount "type=bind,src=$reference,dst=/baseline106,readonly" --mount "type=bind,src=$ops/ci,dst=/ci-tools,readonly" \
    "$image" env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp PYTHONDONTWRITEBYTECODE=1 \
    GITHUB_ACTIONS=true GITHUB_REPOSITORY=ilovedrrealty/realty-api ILDR_BROWSER_CI_NONCE="$nonce" \
    python -I -B /ci-tools/assignment-notifications-native/verify.py "$mode") || die NATIVE_CONTAINER_CREATE_FAILED
  owned "$native" || die OWNED_NATIVE_CONTAINER_REQUIRED
  client_exit=0
  phase_bound=900s
  [[ "$mode" != admin_assignment && "$mode" != lead_tracking ]] || phase_bound=300s
  bounded "$phase_bound" docker start --attach "$native" >"$state/$mode.log" 2>&1 || client_exit=$?
  read -r native_exit running oom <<<"$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$native")"
  [[ "$client_exit" =~ ^[0-9]{1,3}$ && "$native_exit" =~ ^[0-9]{1,3}$ && "$running" =~ ^(true|false)$ && "$oom" =~ ^(true|false)$ ]] || die TYPED_PROCESS_FACTS_REQUIRED
  printf 'ASSIGNMENT_NATIVE_PROCESS mode=%s client=%s native=%s running=%s oom=%s\n' "$mode" "$client_exit" "$native_exit" "$running" "$oom"
  python3 -I -S -B "$ops/ci/assignment-notifications-native/check-result.py" "$mode" "$state/$mode.log" \
    "$client_exit" "$native_exit" "$running" "$oom" || overall=1
  if [[ "$running" == true ]]; then bounded 20s docker stop --time 10 "$native" >/dev/null || die NATIVE_STOP_REQUIRED; fi
  [[ $(bounded 10s docker inspect --format '{{.State.Running}}' "$native") == false ]] || die NATIVE_STOPPED_REQUIRED
  bounded 15s docker rm -v "$native" >/dev/null || die NATIVE_CONTAINER_REMOVAL_REQUIRED
done
read -r pg_running pg_oom <<<"$(bounded 15s docker inspect --format '{{.State.Running}} {{.State.OOMKilled}}' "$pg")"
[[ "$pg_running" =~ ^(true|false)$ && "$pg_oom" =~ ^(true|false)$ ]] || die TYPED_POSTGRES_FACTS_REQUIRED
printf 'ASSIGNMENT_NATIVE_POSTGRES running=%s oom=%s\n' "$pg_running" "$pg_oom"
for mode in canonical unit; do
  db="$canonical_db"; [[ "$mode" == canonical ]] || db="$unit_db"
  marker="ildr-assignment-$mode-ci:$nonce"
  [[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname='$db'") == "$marker" ]] || die OWNED_DATABASE_DROP_GUARD
  bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE $db" >/dev/null || die OWNED_DATABASE_DROP_REQUIRED
  [[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "SELECT count(*) FROM pg_database WHERE datname='$db'") == 0 ]] || die OWNED_DATABASE_ABSENCE_REQUIRED
done
printf 'ASSIGNMENT_NATIVE_DATABASE_REMOVED canonical=true unit=true\n'
[[ "$overall" == 0 && "$pg_running" == true && "$pg_oom" == false ]] || die EXACT_NATIVE_ACCEPTANCE_REQUIRED
[[ -z $(git -C "$application" status --porcelain=v1 --untracked-files=all) && -z $(git -C "$reference" status --porcelain=v1 --untracked-files=all) ]] || die SOURCE_ALTERED_BY_NATIVE
printf 'ASSIGNMENT_NATIVE_HOSTED_RESULT operations=%s source=current native_suite=exact_inventory genuine_schema=108 model_tables=107 old_tables=106 external_provider_calls=0 run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"

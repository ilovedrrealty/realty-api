#!/usr/bin/env bash
set -euo pipefail
umask 077
die() { printf 'GATEWAY_UNIT_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }
[[ $# == 4 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${GITHUB_REF:-} == refs/heads/codex-ci-assistant-gateway-unit-20261127 && ${GITHUB_RUN_ATTEMPT:-} == 1 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 ]] || die EXACT_HOSTED_EVENT_REQUIRED
ops="$1"; application="$2"; dependencies="$3"; recipe="$4"
[[ "$ops" == "$GITHUB_WORKSPACE/gateway-ops" && "$application" == "$GITHUB_WORKSPACE/gateway-app" &&
   "$dependencies" == "$GITHUB_WORKSPACE/gateway-dependencies" && "$recipe" == "$GITHUB_WORKSPACE/gateway-recipe" ]] || die EXACT_PATHS_REQUIRED
for path in "$ops" "$application" "$dependencies" "$recipe"; do
  [[ $(realpath "$path") == "$path" && ! -e "$path/.env" && ! -L "$path/.env" ]] || die EXACT_CLEAN_PATH_REQUIRED
done
python3 -I -S -B - "$ops" "$application" "$dependencies" "$recipe" <<'PY_SOURCE'
import ast,hashlib,json,os,subprocess,sys
from pathlib import Path
ops,app,deps,recipe=map(Path,sys.argv[1:])
plan=json.loads((ops/'ci/assistant-gateway-unit/PLAN.json').read_bytes())
def git(root,*args):return subprocess.check_output(['git','-C',str(root),*args]).decode().strip()
def tree(root,c):return {p.decode():m.decode() for x in subprocess.check_output(['git','-C',str(root),'ls-tree','-rz',c]).split(b'\0') if x for m,p in [x.split(b'\t',1)]}
for root in (ops,app,deps,recipe):assert not git(root,'status','--porcelain=v1','--untracked-files=all')
assert git(ops,'rev-parse','HEAD')==os.environ['GITHUB_SHA']
assert git(ops,'show','-s','--format=%P','HEAD')==plan['operations_parent']=='2a474ccc8b6b5188a79a460fa059cf4080fdefe7'
assert git(ops,'rev-parse','HEAD^^{tree}')==plan['operations_parent_tree']=='dab774530f4c3b962482924e7a52ef7ab94cca33'
old,current=tree(ops,'HEAD^'),tree(ops,'HEAD')
paths={'.github/workflows/assistant-gateway-unit.yml',*[f'ci/assistant-gateway-unit/{p}' for p in ('PLAN.json','verify.py','check-result.py','run-hosted.sh')]}
assert len(old)==451 and len(current)==456 and current.keys()-old.keys()==paths and all(current[p]==v for p,v in old.items())
assert set(git(ops,'diff','--name-only','HEAD^','HEAD').splitlines())==paths
assert set(git(ops,'diff','--diff-filter=A','--name-only','HEAD^','HEAD').splitlines())==paths
assert git(app,'rev-parse','HEAD')==plan['application']['commit']=='43f3b2d760b52c8ab449fcd5e6cd22f3118e6173'
assert git(app,'rev-parse','HEAD^{tree}')==plan['application']['tree']=='0e3a1add5efb5e027f9498788e4101e59da5b15f'
assert git(app,'show','-s','--format=%P','HEAD')==plan['application']['parent']=='9a422618139ad3a01bc8c0b3fd38ae5dd7f42f41'
assert git(app,'rev-parse','HEAD^^{tree}')=='b09f1c01af1260225778f915dc3cd79b10807525'
old,current=tree(app,'HEAD^'),tree(app,'HEAD')
assert len(old)==383 and len(current)==385 and current.keys()-old.keys()=={'services/assistant_gateway_transport.py','tests/test_assistant_gateway_transport.py'} and all(current[p]==v for p,v in old.items())
for p,pin in plan['source_files'].items():
 raw=(app/p).read_bytes();assert current[p]==pin['tuple'] and len(raw)==pin['bytes'] and hashlib.sha256(raw).hexdigest()==pin['sha256']
suite=plan['suite'];raw=(app/suite['file']).read_bytes()
classes=[n for n in ast.parse(raw).body if isinstance(n,ast.ClassDef) and n.name==suite['class']];assert len(classes)==1
names=[n.name for n in classes[0].body if isinstance(n,ast.AsyncFunctionDef) and n.name.startswith('test_')]
assert names==suite['methods'] and len(names)==len(set(names))==12
for root,key in ((deps,'dependencies'),(recipe,'recipe')):
 assert git(root,'rev-parse','HEAD')==plan[key]['commit'] and git(root,'rev-parse','HEAD^{tree}')==plan[key]['tree']
assert hashlib.sha256((deps/'requirements.txt').read_bytes()).hexdigest()==plan['dependencies']['requirements_sha256']
assert hashlib.sha256((recipe/'ci/migration/services163.Dockerfile').read_bytes()).hexdigest()==plan['recipe']['dockerfile_sha256']
print('GATEWAY_UNIT_SOURCE exact=true inherited=451 source_paths=385 cases=12 database=false actual_model=false')
PY_SOURCE
[[ -d ${RUNNER_TEMP:-} && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die OWNED_SCRATCH_REQUIRED
nonce=$(python3 -I -S -B -c 'from uuid import uuid4;print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-gateway-unit.XXXXXXXX")
label=org.ilovedrrealty.gateway-unit
run_key="$GITHUB_RUN_ID-$nonce"
image="ildr-gateway-unit:$run_key"
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
  if [[ "$state" == "$RUNNER_TEMP"/ildr-gateway-unit.* && -d "$state" && ! -L "$state" && -O "$state" && $(realpath "$state") == "$state" ]]; then
    bounded 40s rm --recursive --force --one-file-system -- "$state" || clean=0
  else clean=0; fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'GATEWAY_UNIT_OWNED_CLEANUP operations=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT
mkdir "$state/build"
cp "$dependencies/requirements.txt" "$state/build/requirements.txt"
cp "$recipe/ci/migration/services163.Dockerfile" "$state/build/Dockerfile"
python_digest=sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
base="docker.io/library/python:3.11-slim@$python_digest"
bounded 180s docker pull --platform linux/amd64 "$base" >/dev/null || die PINNED_IMAGE_PULL_FAILED
[[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$base") == linux/amd64 ]] || die EXACT_IMAGE_PLATFORM_REQUIRED
bounded 600s docker build --platform linux/amd64 --pull=false --quiet --label "$label=$run_key" \
  --build-arg "PYTHON_DIGEST=$python_digest" --file "$state/build/Dockerfile" --tag "$image" "$state/build" \
  >"$state/build.log" 2>&1 || die DEPENDENCY_IMAGE_BUILD_FAILED
native=$(bounded 25s docker create --label "$label=$run_key" --network none \
  --user "$(id -u):$(id -g)" --read-only --init --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 96 --cpus 1 --memory 384m --memory-swap 384m --tmpfs /tmp:rw,exec,size=64m \
  --mount "type=bind,src=$application,dst=/application,readonly" --mount "type=bind,src=$ops/ci/assistant-gateway-unit,dst=/ci-tools,readonly" \
  "$image" env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp PYTHONDONTWRITEBYTECODE=1 \
  python -I -B /ci-tools/verify.py) || die UNIT_CONTAINER_CREATE_FAILED
owned "$native" && [[ $(bounded 15s docker inspect --format '{{.HostConfig.NetworkMode}}' "$native") == none ]] || die OWNED_ISOLATED_UNIT_REQUIRED
client_exit=0
bounded 90s docker start --attach "$native" >"$state/unit.log" 2>&1 || client_exit=$?
read -r native_exit running oom <<<"$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$native")"
[[ "$client_exit" =~ ^[0-9]{1,3}$ && "$native_exit" =~ ^[0-9]{1,3}$ && "$running" =~ ^(true|false)$ && "$oom" =~ ^(true|false)$ ]] || die TYPED_PROCESS_FACTS_REQUIRED
printf 'GATEWAY_UNIT_PROCESS client=%s native=%s running=%s oom=%s\n' "$client_exit" "$native_exit" "$running" "$oom"
overall=0
python3 -I -S -B "$ops/ci/assistant-gateway-unit/check-result.py" "$state/unit.log" "$client_exit" "$native_exit" "$running" "$oom" || overall=1
if [[ "$running" == true ]]; then bounded 20s docker stop --time 10 "$native" >/dev/null || die UNIT_STOP_REQUIRED; fi
[[ $(bounded 10s docker inspect --format '{{.State.Running}}' "$native") == false ]] || die UNIT_STOPPED_REQUIRED
bounded 15s docker rm -v "$native" >/dev/null || die UNIT_CONTAINER_REMOVAL_REQUIRED
printf 'GATEWAY_UNIT_CLIENT_REMOVED stopped=true\n'
[[ "$overall" == 0 ]] || die EXACT_UNIT_ACCEPTANCE_REQUIRED
[[ -z $(git -C "$application" status --porcelain=v1 --untracked-files=all) && -z $(git -C "$ops" status --porcelain=v1 --untracked-files=all) ]] || die SOURCE_ALTERED_BY_UNIT
printf 'GATEWAY_UNIT_HOSTED_RESULT operations=%s source=43f3b2d760b52c8ab449fcd5e6cd22f3118e6173 cases=12 database=false actual_model_calls=0 entitlement=false run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"

#!/usr/bin/env bash
set -euo pipefail
die() { printf 'BACKUP_QUERY_COMPAT_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }
[[ $# == 2 && "$GITHUB_ACTIONS" == true && "$GITHUB_REPOSITORY" == ilovedrrealty/realty-api &&
   "$GITHUB_REF" == refs/heads/codex-ci-backup-query-compatibility-20261118 && "$GITHUB_RUN_ATTEMPT" == 1 &&
   "$GITHUB_SHA" =~ ^[a-f0-9]{40}$ && "$GITHUB_RUN_ID" =~ ^[1-9][0-9]{0,19}$ &&
   "$RUNNER_ENVIRONMENT" == github-hosted && "$RUNNER_OS" == Linux && "$RUNNER_ARCH" == X64 ]] || die EXACT_HOSTED_EVENT_REQUIRED
ops="$1"; recipe="$2"
[[ "$ops" == "$GITHUB_WORKSPACE/backup-ops" && "$recipe" == "$GITHUB_WORKSPACE/migration-recipe" ]] || die EXACT_PATHS_REQUIRED
for checkout in "$ops" "$recipe"; do
  [[ $(realpath "$checkout") == "$checkout" && -z $(git -C "$checkout" status --porcelain=v1 --untracked-files=all) &&
     ! -e "$checkout/.env" && ! -L "$checkout/.env" ]] || die CLEAN_SOURCE_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" show -s --format=%P HEAD) == 6b049ea0f90e3db186583add2af4d67959405701 &&
   $(git -C "$recipe" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 ]] || die EXACT_PINS_REQUIRED
python3 -I -B - "$ops" <<'PY_SCOPE'
from pathlib import Path
import subprocess,sys
root=Path(sys.argv[1]);base='ci/backup-query-compatibility/'
expected={'.github/workflows/backup-query-compatibility.yml',base+'run-hosted.sh',base+'verify-synthetic.py',base+'requirements.txt',base+'source-manifest.json'}
for name in ('daily-schema-catalog-read-only.sql','make-daily-full-public-query.py','make-daily-compressed-query.py','receive-daily-compressed-snapshot.py','finalize-daily-full-public-backup.py'):
 expected.add(base+'workspace/outputs/website-growth-20261022/backups/'+name)
expected.add(base+'workspace/outputs/property-network-20261101/backup-query-compatibility-20261118/make-daily-full-public-query-v2.py')
changed=subprocess.check_output(['git','-C',str(root),'diff-tree','--no-commit-id','--name-status','-r','HEAD^','HEAD'],text=True).splitlines()
assert set(changed)=={'A\t'+name for name in expected}
assert len(expected)==11
assert not any(p.is_symlink() for p in (root/base).rglob('*'))
PY_SCOPE
[[ $? == 0 ]] || die EXACT_ADDITIONS_REQUIRED
tools="$ops/ci/backup-query-compatibility"
dockerfile="$recipe/ci/migration/services163.Dockerfile"
printf '%s  %s\n' e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9 "$dockerfile" | sha256sum --check --status || die RECIPE_HASH_CHANGED
umask 077
[[ -d "$RUNNER_TEMP" && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die OWNED_SCRATCH_REQUIRED
nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-backup-compat.XXXXXXXX")
run_key="$GITHUB_RUN_ID-$nonce"
label=org.ilovedrrealty.backup-query-compat
image="ildr-backup-compat:$run_key"
python_digest=sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
postgres='docker.io/library/postgres:17.6@sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409'
pg=''
owned() { [[ "$1" =~ ^[a-f0-9]{64}$ && $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$1") == "$run_key" ]]; }
cleanup() {
  local code=$? clean=1 ids images id tagged
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
  if [[ "$state" == "$RUNNER_TEMP"/ildr-backup-compat.* && -d "$state" && ! -L "$state" && $(realpath "$state") == "$state" ]]; then
    rm -rf -- "$state" || clean=0
  else clean=0; fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'BACKUP_QUERY_COMPAT_OWNED_CLEANUP commit=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT
mkdir "$state/build" "$state/test"
cp "$tools/requirements.txt" "$state/build/requirements.txt"
for base in "docker.io/library/python:3.11-slim@$python_digest" "$postgres"; do
  bounded 180s docker pull --platform linux/amd64 "$base" >/dev/null || die PINNED_PULL_FAILED
  [[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$base") == linux/amd64 ]] || die IMAGE_PLATFORM_REQUIRED
done
bounded 600s docker build --platform linux/amd64 --pull=false --quiet --label "$label=$run_key" --build-arg "PYTHON_DIGEST=$python_digest" \
  --file "$dockerfile" --tag "$image" "$state/build" >"$state/build.log" 2>&1 || die DEPENDENCY_BUILD_FAILED
pg=$(bounded 25s docker create --label "$label=$run_key" --network none --security-opt no-new-privileges:true \
  --memory 768m --memory-swap 768m --pids-limit 96 --cpus 1 --tmpfs /var/lib/postgresql/data:rw,size=384m \
  --env POSTGRES_HOST_AUTH_METHOD=trust --env POSTGRES_DB=postgres "$postgres" postgres -p 55449 -h 127.0.0.1) || die POSTGRES_CREATE_FAILED
owned "$pg" && [[ $(bounded 15s docker inspect --format '{{.HostConfig.NetworkMode}}' "$pg") == none ]] || die ISOLATED_POSTGRES_REQUIRED
bounded 25s docker start "$pg" >/dev/null || die POSTGRES_START_FAILED
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  if bounded 3s docker exec "$pg" pg_isready -h 127.0.0.1 -p 55449 -U postgres -d postgres >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 && $(bounded 5s docker exec "$pg" ls /sys/class/net) == lo ]] || die LOOPBACK_REQUIRED
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c "COMMENT ON DATABASE postgres IS 'ildr-backup-compat:$nonce'" >/dev/null || die POSTGRES_MARKER_REQUIRED
test_container=$(bounded 25s docker create --label "$label=$run_key" --network "container:$pg" --user "$(id -u):$(id -g)" \
  --read-only --init --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 128 --cpus 2 --memory 1024m --memory-swap 1024m \
  --tmpfs /tmp:rw,exec,size=128m --env GITHUB_ACTIONS=true --env GITHUB_REPOSITORY=ilovedrrealty/realty-api \
  --env "ILDR_BACKUP_COMPAT_NONCE=$nonce" --mount "type=bind,src=$tools,dst=/tools,readonly" \
  --mount "type=bind,src=$state/test,dst=/state" "$image" /usr/local/bin/python -I -B /tools/verify-synthetic.py) || die TEST_CREATE_FAILED
owned "$test_container" || die OWNED_TEST_REQUIRED
test_exit=0
bounded 420s docker start --attach "$test_container" >"$state/test.log" 2>&1 || test_exit=$?
test_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$test_container")
[[ "$test_state" =~ ^[0-9]+\ (true|false)\ (true|false)$ ]] || die TEST_STATE_REQUIRED
printf 'BACKUP_QUERY_COMPAT_NATIVE client=%s state=%s\n' "$test_exit" "$test_state"
python3 -I -B - "$state/test.log" <<'PY_RESULT'
from pathlib import Path
import json,re,sys
path=Path(sys.argv[1]);assert path.stat().st_size<=131072
prefix=b'BACKUP_QUERY_COMPAT_RESULT '
reports=[json.loads(line[len(prefix):]) for line in path.read_bytes().splitlines() if line.startswith(prefix)]
assert len(reports)==1
report=reports[0]
if report.get('status')=='FAIL':
 assert set(report)=={'status','stage','error_type','reason'}
 assert all(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_]{1,90}',value) for value in report.values())
else:
 assert report=={'status':'PASS','postgres_version_num':170006,'v2_table_counts':[25,50,51,92],
  'old_semantics_equal_table_counts':[25,50],'old_51_exact_arity_error':True,'complete_count_hash_maps':True,
  'bytea_rows_typed_roundtrip':8,'schema_drift_fail_closed':True,'frozen_source_copies':5,
  'compressed_transport_verified':True,'frozen_receiver_verified':True,'frozen_finalizer_validation_cleanup_verified':True,
  'sealer_boundary_calls':1,'owner_encryption_or_cloud_executed':False,'production_data_accessed':False,'external_network_available':False}
print(prefix.decode()+json.dumps(report,sort_keys=True,separators=(',',':')))
assert report['status']=='PASS'
PY_RESULT
[[ "$test_exit" == 0 && "$test_state" == '0 false false' ]] || die SYNTHETIC_RUNTIME_FAILED
for checkout in "$ops" "$recipe"; do
  [[ -z $(git -C "$checkout" status --porcelain=v1 --untracked-files=all) ]] || die SOURCE_CHANGED
done
printf 'BACKUP_QUERY_COMPAT_ACCEPTANCE commit=%s run=%s attempt=1 tables=25,50,51,92 bytea=verified sealer=blocked\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"

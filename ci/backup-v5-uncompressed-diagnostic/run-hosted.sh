#!/usr/bin/env bash
set -euo pipefail
umask 077
die() { printf 'BACKUP_V5_UNCOMPRESSED_DIAGNOSTIC_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }
[[ $# == 2 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${GITHUB_REF:-} == refs/heads/codex-ci-backup-v5-uncompressed-20261125 && ${GITHUB_RUN_ATTEMPT:-} == 1 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 ]] || die EXACT_HOSTED_EVENT_REQUIRED
ops="$1"; recipe="$2"
[[ "$ops" == "$GITHUB_WORKSPACE/backup-ops" && "$recipe" == "$GITHUB_WORKSPACE/migration-recipe" ]] || die EXACT_PATHS_REQUIRED
for checkout in "$ops" "$recipe"; do
  [[ $(realpath "$checkout") == "$checkout" && -z $(git -C "$checkout" status --porcelain=v1 --untracked-files=all) &&
     ! -e "$checkout/.env" && ! -L "$checkout/.env" ]] || die CLEAN_SOURCE_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" show -s --format=%P HEAD) == 54fa2203c24ae675c8837b529c1b4a0bbe9bbb54 &&
   $(git -C "$recipe" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 ]] || die EXACT_PINS_REQUIRED
python3 -I -B - "$ops" <<'PY_SCOPE'
from pathlib import Path
from hashlib import sha256
import json,subprocess,sys
root=Path(sys.argv[1]);base='ci/backup-v5-uncompressed-diagnostic/'
expected={'.github/workflows/backup-v5-uncompressed-diagnostic.yml',*(base+name for name in (
    'PLAN.json','run-hosted.sh','check-result.py','verify-native.py'))}
changed=subprocess.check_output(['git','-C',str(root),'diff-tree','--no-commit-id','--name-status','-r','HEAD^','HEAD'],text=True).splitlines()
assert len(expected)==5 and set(changed)=={'A\t'+name for name in expected}
assert not any(path.is_symlink() for path in (root/base).rglob('*'))
candidate=root/'ci/backup-query-v4-native'
manifest=json.loads((candidate/'source-manifest.json').read_text())
assert len(manifest['inherited_files'])==11 and len(manifest['candidate_files'])==6
assert sha256((candidate/'verify-native.py').read_bytes()).hexdigest()=="b2565a1b76eec78df9252f3ab08d4b30bc2ee308c40982a29002459761b1d500"
for name,digest in manifest['inherited_files'].items():
    path=root/name
    assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root)
    assert sha256(path.read_bytes()).hexdigest()==digest
for name,digest in manifest['candidate_files'].items():
    path=candidate/name
    assert path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(candidate)
    assert sha256(path.read_bytes()).hexdigest()==digest
PY_SCOPE
tools="$ops/ci/backup-v5-uncompressed-diagnostic"
accepted="$ops/ci/backup-query-compatibility"
dockerfile="$recipe/ci/migration/services163.Dockerfile"
printf '%s  %s\n' e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9 "$dockerfile" | sha256sum --check --status || die RECIPE_HASH_CHANGED
[[ -d "$RUNNER_TEMP" && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die OWNED_SCRATCH_REQUIRED
nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-backup-v5-uncompressed.XXXXXXXX")
run_key="$GITHUB_RUN_ID-$nonce"
label=org.ilovedrrealty.backup-v5-uncompressed-diagnostic
image="ildr-backup-v5-uncompressed:$run_key"
python_digest=sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
postgres='docker.io/library/postgres:17.6@sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409'
owned_volumes=()
owned() { [[ "$1" =~ ^[a-f0-9]{64}$ && $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$1") == "$run_key" ]]; }
remember_volume() {
  owned "$1" || return 1
  local volume
  volume=$(bounded 15s docker inspect --format '{{json .Mounts}}' "$1" | python3 -I -B -c '
import json,re,sys
volumes=[v for v in json.load(sys.stdin) if v["Type"]=="volume"]
assert len(volumes)==1 and volumes[0]["Destination"]=="/var/lib/postgresql/data"
assert re.fullmatch("[a-f0-9]{64}",volumes[0]["Name"])
print(volumes[0]["Name"])') || return 1
  owned_volumes+=("$volume")
}
cleanup() {
  local code=$? clean=1 ids images id tagged volume listed kind
  trap - EXIT
  set +e
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  for id in $ids; do
    if owned "$id"; then
      kind=$(bounded 10s docker inspect --format '{{.Config.Image}}' "$id") || clean=0
      if [[ "$kind" == "$postgres" ]]; then remember_volume "$id" || clean=0; fi
      bounded 20s docker rm -f -v "$id" >/dev/null 2>&1 || clean=0
    else clean=0; fi
  done
  for volume in "${owned_volumes[@]}"; do
    listed=$(bounded 15s docker volume ls -q --filter "name=$volume") || clean=0
    [[ -z "$listed" ]] || clean=0
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
  if [[ "$state" == "$RUNNER_TEMP"/ildr-backup-v5-uncompressed.* && -d "$state" && ! -L "$state" &&
        $(realpath "$state") == "$state" && -O "$state" ]]; then
    bounded 40s rm --recursive --force --one-file-system -- "$state" || clean=0
  else clean=0; fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'BACKUP_V5_UNCOMPRESSED_DIAGNOSTIC_OWNED_CLEANUP commit=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT
mkdir "$state/build"
cp "$accepted/requirements.txt" "$state/build/requirements.txt"
for base in "docker.io/library/python:3.11-slim@$python_digest" "$postgres"; do
  bounded 180s docker pull --platform linux/amd64 "$base" >/dev/null || die PINNED_PULL_FAILED
  [[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$base") == linux/amd64 ]] || die IMAGE_PLATFORM_REQUIRED
done
bounded 600s docker build --platform linux/amd64 --pull=false --quiet --label "$label=$run_key" --build-arg "PYTHON_DIGEST=$python_digest" \
  --file "$dockerfile" --tag "$image" "$state/build" >"$state/build.log" 2>&1 || die ACCEPTED_DEPENDENCY_BUILD_FAILED
overall_failed=0
for phase in full_snapshot_uncompressed; do
  phase_nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
  [[ "$phase_nonce" =~ ^[a-f0-9]{32}$ ]] || die PHASE_NONCE_REQUIRED
  mkdir "$state/$phase"
  # Disk-backed anonymous data volume is owned by this exact container. Its
  # name/absence is verified; cgroup metrics include reclaimable file cache.
  pg=$(bounded 25s docker create --label "$label=$run_key" --network none --security-opt no-new-privileges:true \
    --memory 512m --memory-swap 512m --pids-limit 96 --cpus 1 \
    --env POSTGRES_HOST_AUTH_METHOD=trust --env POSTGRES_DB=postgres "$postgres" \
    postgres -p 55449 -h 127.0.0.1 -c max_parallel_workers_per_gather=0 -c jit=off) || die POSTGRES_CREATE_FAILED
  owned "$pg" && [[ $(bounded 15s docker inspect --format '{{.HostConfig.NetworkMode}}' "$pg") == none &&
    $(bounded 15s docker inspect --format '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' "$pg") == '536870912 536870912' ]] || die ISOLATED_BOUNDED_POSTGRES_REQUIRED
  remember_volume "$pg" || die OWNED_ANONYMOUS_VOLUME_REQUIRED
  bounded 25s docker start "$pg" >/dev/null || die POSTGRES_START_FAILED
  ready=0
  for ((attempt=0; attempt<60; attempt++)); do
    if bounded 3s docker exec "$pg" pg_isready -h 127.0.0.1 -p 55449 -U postgres -d postgres >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
  done
  [[ "$ready" == 1 && $(bounded 5s docker exec "$pg" ls /sys/class/net) == lo ]] || die LOOPBACK_REQUIRED
  bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "COMMENT ON DATABASE postgres IS 'ildr-backup-compat:$phase_nonce'" >/dev/null || die POSTGRES_MARKER_REQUIRED
  metrics() {
    owned "$pg" || return 1
    bounded 10s docker exec "$pg" sh -c 'cat /sys/fs/cgroup/memory.current; cat /sys/fs/cgroup/memory.peak; cat /sys/fs/cgroup/memory.events'
  }
  pg_before_start=$(bounded 8s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 -c 'SELECT pg_postmaster_start_time()::text') || die POSTMASTER_START_REQUIRED
  metrics >"$state/$phase-before-memory.txt" || die ACTUAL_CGROUP_V2_METRICS_REQUIRED
  python3 -I -B "$tools/check-result.py" --memory-preflight "$state/$phase-before-memory.txt" || die CGROUP_MEMORY_PREFLIGHT_REFUSED
  client=$(bounded 25s docker create --label "$label=$run_key" --network "container:$pg" --user "$(id -u):$(id -g)" \
    --read-only --init --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 128 --cpus 2 \
    --memory 1024m --memory-swap 1024m --tmpfs /tmp:rw,exec,size=128m \
    --env GITHUB_ACTIONS=true --env GITHUB_REPOSITORY=ilovedrrealty/realty-api \
    --env "ILDR_BACKUP_COMPAT_NONCE=$phase_nonce" --mount "type=bind,src=$accepted,dst=/tools,readonly" \
    --mount "type=bind,src=$tools,dst=/new-tools,readonly" --mount "type=bind,src=$ops/ci/backup-query-v4-native,dst=/candidate-tools,readonly" --mount "type=bind,src=$state/$phase,dst=/state" \
    "$image" /usr/local/bin/python -I -B /new-tools/verify-native.py "$phase") || die CLIENT_CREATE_FAILED
  owned "$client" || die OWNED_CLIENT_REQUIRED
  started_ms=$(python3 -I -B -c 'import time; print(time.monotonic_ns()//1000000)')
  native=0
  bounded 480s docker start --attach "$client" >"$state/$phase.log" 2>&1 || native=$?
  elapsed_ms=$(( $(python3 -I -B -c 'import time; print(time.monotonic_ns()//1000000)') - started_ms ))
  client_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$client") || die ACTUAL_CLIENT_STATE_REQUIRED
  pg_state=$(bounded 15s docker inspect --format '{{.State.Running}} {{.State.OOMKilled}} {{.RestartCount}}' "$pg") || die ACTUAL_POSTGRES_STATE_REQUIRED
  pg_diagnostic=$(bounded 15s docker inspect --format '{{.State.Status}} {{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}} {{.RestartCount}}' "$pg") || die ACTUAL_POSTGRES_DIAGNOSTIC_REQUIRED
  start_state=unavailable
  if pg_after_start=$(bounded 8s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 -c 'SELECT pg_postmaster_start_time()::text' 2>"$state/$phase-start-read.log"); then
    start_state=changed
    [[ "$pg_before_start" != "$pg_after_start" ]] || start_state=unchanged
  fi
  metrics >"$state/$phase-after-memory.txt" 2>"$state/$phase-memory-read.log" || :
  python3 -I -B "$tools/check-result.py" --observed-processes "$phase" "$native" "$client_state" "$pg_diagnostic" "$start_state" \
    "$state/$phase-before-memory.txt" "$state/$phase-after-memory.txt" "$elapsed_ms" || die SAFE_PHASE_DIAGNOSTIC_REQUIRED
  case_failed=0
  python3 -I -B "$tools/check-result.py" "$phase" "$state/$phase.log" "$native" "$client_state" "$pg_state" \
    "$state/$phase-before-memory.txt" "$state/$phase-after-memory.txt" "$elapsed_ms" || case_failed=1
  [[ "$start_state" == unchanged ]] || case_failed=1
  [[ "$case_failed" == 0 ]] || overall_failed=1
  read -r client_exit client_running client_oom <<<"$client_state"
  if [[ "$client_running" == true ]]; then bounded 20s docker stop --time 10 "$client" >/dev/null || die OWNED_CLIENT_STOP_REQUIRED; fi
  bounded 20s docker rm -v "$client" >/dev/null || die OWNED_CLIENT_REMOVAL_REQUIRED
  bounded 25s docker stop --time 15 "$pg" >/dev/null || die OWNED_POSTGRES_STOP_REQUIRED
  phase_volume="${owned_volumes[-1]}"
  bounded 20s docker rm -v "$pg" >/dev/null || die OWNED_POSTGRES_REMOVAL_REQUIRED
  for removed in "$client" "$pg"; do
    remaining_container=$(bounded 15s docker ps -aq --no-trunc --filter "id=$removed") || die PRIOR_PHASE_CONTAINER_ABSENCE_QUERY_REQUIRED
    [[ -z "$remaining_container" ]] || die PRIOR_PHASE_CONTAINER_CLEANUP_REQUIRED
  done
  remaining_volume=$(bounded 15s docker volume ls -q --filter "name=$phase_volume") || die OWNED_VOLUME_ABSENCE_QUERY_REQUIRED
  [[ -z "$remaining_volume" ]] || die PRIOR_PHASE_VOLUME_CLEANUP_REQUIRED
  printf 'BACKUP_V5_UNCOMPRESSED_DIAGNOSTIC_CASE_CLEANUP case=%s verified=true failed=%s\n' "$phase" "$case_failed"
done
for checkout in "$ops" "$recipe"; do
  [[ -z $(git -C "$checkout" status --porcelain=v1 --untracked-files=all) ]] || die SOURCE_CHANGED
done
printf 'BACKUP_V5_UNCOMPRESSED_DIAGNOSTIC_DONE commit=%s run=%s attempt=1 cases=1 any_failed=%s backup_accepted=false\n' "$GITHUB_SHA" "$GITHUB_RUN_ID" "$overall_failed"
[[ "$overall_failed" == 0 ]] || die ONE_OR_MORE_COMPONENT_DIAGNOSTICS_FAILED

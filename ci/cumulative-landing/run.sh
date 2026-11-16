#!/usr/bin/env bash
# Use the same reconstruction chain as accepted custody/browser run 43961552607.
set -euo pipefail
die() { printf 'CUMULATIVE_LANDING_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }
[[ $# == 3 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 && ${GITHUB_RUN_ATTEMPT:-} == 1 ]] || die HOST_REQUIRED
[[ ${GITHUB_REF:-} == refs/heads/codex-ci-cumulative-network-landing-20261116 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die EXACT_EVENT_REQUIRED
ops="$1"; relay="$2"; frontend="$3"
parent=8cf4c400be4a58c00afe58c40e095fc9d4000c4d
tree=a0429808349e5221b7b4103cb1268ff9c7f67250
[[ "$ops" == "$GITHUB_WORKSPACE/landing-ops" && "$relay" == "$GITHUB_WORKSPACE/frontend-relay" &&
   "$frontend" == "$GITHUB_WORKSPACE/frontend-base" ]] || die EXACT_CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$relay" "$frontend"; do
  status=$(git -C "$checkout" status --porcelain=v1 --untracked-files=all) || die STATUS_REFUSED
  [[ $(realpath "$checkout") == "$checkout" && -z "$status" ]] || die CLEAN_CHECKOUT_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" && $(git -C "$ops" rev-parse HEAD^) == "$parent" &&
   $(git -C "$relay" rev-parse HEAD) == 441efdc288c3a38b8607c816c684abec080d83ad &&
   $(git -C "$frontend" rev-parse HEAD) == 73d771940e73d42ed044cea77a5258a93a445b05 ]] || die EXACT_PARENTS_REQUIRED
expected=$'.github/workflows/cumulative-network-landing.yml\nci/cumulative-landing/landing.cjs\nci/cumulative-landing/run.sh'
[[ $(git -C "$ops" diff --name-only "$parent" "$GITHUB_SHA") == "$expected" ]] || die EXACT_OPERATIONS_DELTA_REQUIRED
git -C "$ops" diff --quiet "$parent" "$GITHUB_SHA" -- ci/browser || die ORIGINAL_RECONSTRUCTION_CHANGED
frontend_tools="$relay/ci/frontend"
tools="$ops/ci/browser"
printf '%s  %s\n' \
  741823a5585c1bd396d8a05b04a0cdb47c41c1fee680ee128fd9de535c1da8d1 "$frontend_tools/frontend_ci_host.py" \
  45d69ba856c72c4dd9b342e7084f0fb604957d617802b13d3d4613fc64c72891 "$frontend_tools/frontend-overlay.json" \
  34601a2c8eec8d56749b0664af0299835c755ae74a77c6a9b5d84e942c689db7 "$frontend_tools/run_frontend_ci.cjs" \
  a166dc60598e76b7fdcc3fe2aa5253bc56952b48fd99e4cab5eb4a24523a548e "$frontend_tools/landing_fixture.cjs" \
  0a1abbdbd65a82e67ded20a0603a21fc9321a1b1aa5fd83dbccf1c049ffc4110 "$frontend_tools/landing_fixture.test.cjs" \
  | sha256sum --check --status || die ACCEPTED_TOOL_HASH_MISMATCH

node_image='node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c'
umask 077
[[ -n ${RUNNER_TEMP:-} && -d "$RUNNER_TEMP" && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die HOST_SCRATCH_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-landing.XXXXXXXX")
nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
label='org.ilovedrrealty.rehearsal.cumulative-landing'
run_key="$GITHUB_RUN_ID-$nonce"
uids="$(id -u):$(id -g)"
cleanup() {
  local code=$? clean=1 ids id
  trap - EXIT
  set +e
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  for id in $ids; do
    if [[ "$id" =~ ^[a-f0-9]{64}$ && $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$id") == "$run_key" ]]; then
      bounded 20s docker rm -f -v "$id" >/dev/null || clean=0
    else clean=0; fi
  done
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  [[ -z "$ids" ]] || clean=0
  if [[ "$state" == "$RUNNER_TEMP"/ildr-landing.* && -d "$state" && ! -L "$state" && $(realpath "$state") == "$state" ]]; then
    rm -rf -- "$state" || clean=0
  else clean=0; fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'CUMULATIVE_LANDING_OWNED_CLEANUP commit=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT
mkdir "$state/frontend-work"
bounded 180s docker pull --platform linux/amd64 "$node_image" >/dev/null || die PINNED_IMAGE_PULL_FAILED
[[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$node_image") == linux/amd64 ]] || die EXACT_IMAGE_PLATFORM_REQUIRED

python3 -I -B "$tools/reconstruct-service-visits-frontend.py" base "$tools" "$relay" "$frontend" "$state"
prepare=(--label "$label=$run_key" --user "$uids" --read-only --cap-drop ALL --security-opt no-new-privileges:true
         --memory 8g --memory-swap 8g --pids-limit 256 --cpus 2 --tmpfs /tmp:rw,exec,size=128m)
bounded 500s docker run --rm "${prepare[@]}" --network bridge --workdir /work \
  --mount "type=bind,src=$state/frontend-work,dst=/work" \
  --mount "type=bind,src=$frontend,dst=/source,readonly" \
  --mount "type=bind,src=$frontend_tools,dst=/ci,readonly" \
  --env "FE_CI_COMMIT=$GITHUB_SHA" --env "FE_CI_RUN_ID=$GITHUB_RUN_ID" \
  "$node_image" node /ci/run_frontend_ci.cjs install >"$state/npm-install.log" 2>&1 || die LOCKED_FRONTEND_INSTALL_FAILED
printf 'CUMULATIVE_LANDING_LOCKED_INSTALL native=0\n'
cd "$state/frontend-work"
sha256sum package-lock.json package.json apps/landing/package.json apps/dashboard/package.json >"$state/installed-manifests.sha256"
cd "$ops"
for stage in service-visits service-conversation service-negotiation service-review service-rebooking service-favorites maintenance-reminders due-work-reminders listing-room-tour integrated history-share private-custody; do
  python3 -I -B "$tools/reconstruct-$stage-frontend.py" apply "$tools" "$relay" "$frontend" "$state"
done
(cd "$state/frontend-work" && sha256sum --check --status "$state/installed-manifests.sha256") || die LOCK_CHANGED_AFTER_INSTALL
verify() {
  local observed
  observed=$(python3 -I -B "$tools/reconstruct-private-custody-frontend.py" verify "$tools" "$relay" "$frontend" "$state") || die EXACT_SOURCE_VERIFY_FAILED
  [[ "$observed" == "PRIVATE_CUSTODY_FRONTEND_VERIFY commit=047d5fbf6f78993b0404992ef666cd419001c65d tree=$tree source_paths=473" ]] || die EXACT_COMBINED_TREE_REQUIRED
  printf '%s\n' "$observed"
}
verify
bounded 690s docker run --rm "${prepare[@]}" --network none --workdir /work \
  --mount "type=bind,src=$state/frontend-work,dst=/work" \
  --mount "type=bind,src=$frontend_tools,dst=/fixture,readonly" \
  --mount "type=bind,src=$ops/ci/cumulative-landing,dst=/rehearsal,readonly" \
  "$node_image" env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true node /rehearsal/landing.cjs
verify
printf 'CUMULATIVE_LANDING_ACCEPTANCE commit=%s run=%s attempt=1 source_tree=%s local_ancestry_commit=ff59f37cef4f1b6f4ee4b970bf7226f46b76e762\n' "$GITHUB_SHA" "$GITHUB_RUN_ID" "$tree"

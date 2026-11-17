#!/usr/bin/env bash
set -euo pipefail

die() { printf 'MEMBER_LANDING_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ $# == 3 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 &&
   ${GITHUB_RUN_ATTEMPT:-} == 1 ]] || die HOST_REQUIRED
[[ ${GITHUB_REF:-} == refs/heads/codex-ci-shared-shortlists-landing-20261117 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die EXACT_EVENT_REQUIRED

ops="$1"; fixture="$2"; frontend="$3"
parent=59ad9e2dce7714302adad0cdda197bf6de46f041
fixture_commit=441efdc288c3a38b8607c816c684abec080d83ad
fixture_tree=6c01c7577e343d5e2542d4f8a84cbc0f103e4b5d
frontend_commit=b7daf320ceb1e813d5a4b07238bbf102b13a29b5
frontend_tree=9c21d0c1c506c52530455076a11bce7ea7c2ce68
[[ "$ops" == "$GITHUB_WORKSPACE/member-landing-ops" && "$fixture" == "$GITHUB_WORKSPACE/member-landing-fixture" &&
   "$frontend" == "$GITHUB_WORKSPACE/member-landing-source" ]] || die EXACT_CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$fixture" "$frontend"; do
  status=$(git -C "$checkout" status --porcelain=v1 --untracked-files=all) || die STATUS_REFUSED
  [[ $(realpath "$checkout") == "$checkout" && -z "$status" ]] || die CLEAN_CHECKOUT_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" && $(git -C "$ops" rev-parse HEAD^) == "$parent" &&
   $(git -C "$fixture" rev-parse HEAD) == "$fixture_commit" && $(git -C "$fixture" rev-parse 'HEAD^{tree}') == "$fixture_tree" &&
   $(git -C "$frontend" rev-parse HEAD) == "$frontend_commit" &&
   $(git -C "$frontend" rev-parse 'HEAD^{tree}') == "$frontend_tree" ]] || die EXACT_CHECKOUTS_REQUIRED
expected=$'.github/workflows/network-member-entry-landing.yml\nci/cumulative-landing/run-member-entry.sh\nci/cumulative-landing/verify-member-entry-export.py'
[[ $(git -C "$ops" diff --name-only "$parent" "$GITHUB_SHA") == "$expected" ]] || die EXACT_OPERATIONS_DELTA_REQUIRED
tools="$ops/ci/cumulative-landing"
fixture_tools="$fixture/ci/frontend"
printf '%s  %s\n' \
  bea505badd091280e537a107016b67533d8eac3c4cd21fa10ed4c80bf21ae654 "$tools/landing.cjs" \
  a166dc60598e76b7fdcc3fe2aa5253bc56952b48fd99e4cab5eb4a24523a548e "$fixture_tools/landing_fixture.cjs" \
  0a1abbdbd65a82e67ded20a0603a21fc9321a1b1aa5fd83dbccf1c049ffc4110 "$fixture_tools/landing_fixture.test.cjs" \
  | sha256sum --check --status || die ACCEPTED_TOOL_HASH_MISMATCH

node_image='node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c'
umask 077
[[ -n ${RUNNER_TEMP:-} && -d "$RUNNER_TEMP" && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die HOST_SCRATCH_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-member-landing.XXXXXXXX")
label='org.ilovedrrealty.rehearsal.member-landing'
run_key="$GITHUB_RUN_ID-${state##*.}"
cleanup() {
  local code=$? clean=1 ids id
  trap - EXIT
  set +e
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  for id in $ids; do
    if [[ "$id" =~ ^[a-f0-9]{64}$ && $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$id") == "$run_key" ]]; then
      bounded 20s docker rm -f -v "$id" >/dev/null 2>&1 || clean=0
    else
      clean=0
    fi
  done
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  [[ -z "$ids" ]] || clean=0
  if [[ "$state" == "$RUNNER_TEMP"/ildr-member-landing.* && -d "$state" && ! -L "$state" && $(realpath "$state") == "$state" ]]; then
    rm -rf -- "$state" || clean=0
  else
    clean=0
  fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'MEMBER_LANDING_OWNED_CLEANUP commit=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT

mkdir "$state/frontend-work" "$state/npm-cache"
git -C "$frontend" archive --format=tar HEAD | tar -xf - -C "$state/frontend-work" || die FRONTEND_ARCHIVE_FAILED

verify_frontend() {
  python3 -I -B - "$frontend" "$state/frontend-work" <<'PY'
from hashlib import sha1
from pathlib import Path
import subprocess, sys
source, archived = map(Path, sys.argv[1:])
files = subprocess.check_output(['git', '-C', str(source), 'ls-tree', '-rz', 'HEAD']).split(b'\0')
files = [entry for entry in files if entry]
assert len(files) == 480
for entry in files:
    metadata, name = entry.split(b'\t', 1)
    mode, kind, object_id = metadata.split()
    path = archived / name.decode('utf-8')
    assert kind == b'blob' and mode in (b'100644', b'100755') and path.is_file() and not path.is_symlink()
    assert not path.name.startswith('.env') or path.name == '.env.example'
    assert path.name != '.npmrc'
    payload = path.read_bytes()
    assert sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest().encode() == object_id
PY
}
verify_frontend || die EXACT_ARCHIVED_FRONTEND_REQUIRED
printf '%s  %s\n' \
  8df3c4c580c65694bc727488fc0ec02d88ab1577e5fa16c509f4897b58e60f33 "$state/frontend-work/package-lock.json" \
  be3b1828f5b10cb69bcf24d289020360c6e36da68ac9b6064a70e9ade0ec32dd "$state/frontend-work/package.json" \
  45893e6dfebaad4d2e73f7e7d6b26f176883bc1ba8c8dc025f188cc1b4d12344 "$state/frontend-work/apps/landing/package.json" \
  1126aeb69e32b34394cca03ac440848f7b88b339042496cb6722a1781a204337 "$state/frontend-work/apps/dashboard/package.json" \
  | sha256sum --check --status || die EXACT_PACKAGE_PINS_REQUIRED

bounded 180s docker pull --platform linux/amd64 "$node_image" >/dev/null || die PINNED_NODE_PULL_FAILED
[[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$node_image") == linux/amd64 ]] ||
  die EXACT_NODE_PLATFORM_REQUIRED
prepare=(--label "$label=$run_key" --user "$(id -u):$(id -g)" --read-only --cap-drop ALL
         --security-opt no-new-privileges:true --memory 8g --memory-swap 8g --pids-limit 256 --cpus 2
         --tmpfs /tmp:rw,exec,size=128m --workdir /work)
install_exit=0
bounded 500s docker run --rm "${prepare[@]}" --network bridge \
  --mount "type=bind,src=$state/frontend-work,dst=/work" --mount "type=bind,src=$state/npm-cache,dst=/npm-cache" \
  "$node_image" env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1024 \
  npm_config_registry=https://registry.npmjs.org/ npm_config_cache=/npm-cache npm_config_update_notifier=false \
  npm_config_userconfig=/tmp/ildr-user.npmrc npm_config_globalconfig=/tmp/ildr-global.npmrc \
  npm_config_audit=false npm_config_fund=false npm_config_ignore_scripts=true \
  node /usr/local/lib/node_modules/npm/bin/npm-cli.js ci --ignore-scripts --no-audit --no-fund --include=dev --include=optional \
  >"$state/npm-install.log" 2>&1 || install_exit=$?
if [[ "$install_exit" != 0 ]]; then
  python3 -I -B - "$state/npm-install.log" "$install_exit" <<'PY'
from pathlib import Path
import os, re, sys
with Path(sys.argv[1]).open('rb') as log:
    log.seek(max(0, os.fstat(log.fileno()).st_size - 131072))
    tail = log.read(131072)
allowed = set(b'''ENOSPC ENOMEM EACCES EAI_AGAIN ENOTFOUND ETIMEDOUT ECONNRESET
                  E401 E403 E404 E429 E500 E502 E503 E504 EUSAGE ERESOLVE EINTEGRITY ELOCKVERIFY'''.split())
matches = re.findall(rb'(?m)^npm (?:error|ERR!) code ([A-Z][A-Z0-9_]{1,45})[ \t\r]*$', tail)
codes = ','.join(item.decode('ascii') for item in sorted(set(matches) & allowed)) or 'none'
print(f'MEMBER_LANDING_INSTALL native={int(sys.argv[2])} codes={codes}')
PY
  die LOCKED_FRONTEND_INSTALL_FAILED
fi
printf 'MEMBER_LANDING_INSTALL native=0 codes=none\n'
verify_frontend || die FRONTEND_CHANGED_DURING_INSTALL

swc_exit=0
bounded 30s docker run --rm "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work,readonly" \
  "$node_image" env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true node -e '
  const fs = require("node:fs");
  if (process.version !== "v24.22.1" || process.platform !== "linux" || process.arch !== "x64" ||
      fs.readdirSync("/sys/class/net").join(",") !== "lo") process.exit(2);
  const lock = require("/work/package-lock.json").packages;
  const names = ["next", "@next/swc-linux-x64-gnu"];
  for (const name of names) {
    const path = "/work/node_modules/" + name + "/package.json";
    if (!fs.existsSync(path)) process.exit(3);
    if (!lock["node_modules/" + name].integrity || lock["node_modules/" + name].version !== "15.5.18" ||
        require(path).version !== "15.5.18") process.exit(4);
  }
  try {
    if (typeof require("/work/node_modules/@next/swc-linux-x64-gnu").transformSync !== "function") process.exit(6);
  } catch (error) {
    process.exit(error.code === "ERR_DLOPEN_FAILED" ? 5 : 6);
  }
  console.log("MEMBER_LANDING_SWC native=0 locked_version=15.5.18 platform=linux-x64-gnu");
' >"$state/swc.log" 2>&1 || swc_exit=$?
if [[ "$swc_exit" != 0 ]]; then
  printf 'MEMBER_LANDING_SWC native=%s\n' "$swc_exit"
  die LOCKED_LINUX_SWC_UNAVAILABLE
fi
[[ $(<"$state/swc.log") == 'MEMBER_LANDING_SWC native=0 locked_version=15.5.18 platform=linux-x64-gnu' ]] || die SWC_RESULT_REQUIRED
printf 'MEMBER_LANDING_SWC native=0 locked_version=15.5.18 platform=linux-x64-gnu\n'

landing_exit=0
bounded 690s docker run --rm "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work" \
  --mount "type=bind,src=$fixture_tools,dst=/fixture,readonly" \
  --mount "type=bind,src=$tools,dst=/rehearsal,readonly" \
  "$node_image" env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true node /rehearsal/landing.cjs \
  >"$state/landing.log" 2>&1 || landing_exit=$?
python3 -I -B - "$state/landing.log" "$landing_exit" <<'PY' || die LANDING_RESULT_INVALID
import json, re, sys
from pathlib import Path
path = Path(sys.argv[1])
assert path.stat().st_size <= 16 * 1024 * 1024
body = path.read_bytes()
lines = body.splitlines()
phases = ('fixture_contract', 'landing_declared_full_build_synthetic_input', 'landing_full_strict_typecheck_with_generated_routes')
phase_prefix = b'CUMULATIVE_LANDING_PHASE '
reports = [json.loads(line[len(phase_prefix):]) for line in lines if line.startswith(phase_prefix)]
assert len(reports) <= 3
for report in reports:
    name = report['name']
    native = report['native_exit']
    signal = report['signal']
    stopped = report['stopped_reason']
    group_absent = report['child_group_absent']
    assert name in phases and (native is None or type(native) is int and 0 <= native <= 255)
    assert signal in (None, 'SIGHUP', 'SIGINT', 'SIGKILL', 'SIGABRT', 'SIGSEGV', 'SIGTERM')
    assert stopped in (None, 'CHILD_DEADLINE', 'CHILD_OUTPUT_BOUND') and type(group_absent) is bool
    print(f'MEMBER_LANDING_PHASE {name} native={native} signal={signal} stopped={stopped} group_absent={group_absent}')
native = int(sys.argv[2])
if native:
    for label, pattern in (('type_error', rb'Type error:|error TS[0-9]+'), ('missing_module', rb'Module not found:'),
                           ('static_input', rb'Listing static export stopped|Listing static export requires'),
                           ('export', rb'Export encountered'), ('swc', rb'Failed to load SWC|@next/swc')):
        if re.search(pattern, body):
            print(f'MEMBER_LANDING_DIAGNOSTIC {label}')
    print(f'MEMBER_LANDING_NATIVE native={native}')
    sys.exit(0)
assert tuple(report['name'] for report in reports) == phases
assert all(report['native_exit'] == 0 and report['signal'] is None and report['stopped_reason'] is None
           and report['child_group_absent'] is True for report in reports)
prefix = b'CUMULATIVE_LANDING_RESULT '
results = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
assert len(results) == 1
result = results[0]
assert result['status'] == 'PASS' and result['fixture'] == 'loopback-only'
assert all(result[key] is True for key in ('fixture_closed', 'synthetic_listing_export_and_structured_metadata',
                                         'output_must_not_be_deployed'))
counts = result['counts']
assert set(counts) == {'inventory', 'detail', 'refused', 'total'}
assert all(type(value) is int for value in counts.values())
assert counts['inventory'] >= 1 and counts['detail'] >= 1 and counts['refused'] == 0
assert counts['total'] == counts['inventory'] + counts['detail'] <= 128
print(f"MEMBER_LANDING_NATIVE native=0 inventory={counts['inventory']} detail={counts['detail']} refused=0 "
      "typecheck=pass static_export=pass fixture_closed=pass")
PY
[[ "$landing_exit" == 0 ]] || die ISOLATED_LANDING_BUILD_FAILED
verify_frontend || die FRONTEND_CHANGED_DURING_BUILD
python3 -I -B "$tools/verify-member-entry-export.py" "$state/frontend-work/apps/landing/out" ||
  die PUBLIC_ROUTE_METADATA_REQUIRED
printf 'MEMBER_LANDING_ACCEPTANCE operations=%s frontend=%s tree=%s fixture=%s run=%s attempt=1\n' \
  "$GITHUB_SHA" "$frontend_commit" "$frontend_tree" "$fixture_commit" "$GITHUB_RUN_ID"

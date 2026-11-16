#!/usr/bin/env bash
set -euo pipefail

die() { printf 'PROFESSIONALS_VALIDATION_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ $# == 2 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 ]] ||
  die GITHUB_HOSTED_REQUIRED
[[ ${GITHUB_REF:-} == refs/heads/codex-ci-professionals-member-entry-v3-20261116 && ${GITHUB_RUN_ATTEMPT:-} == 1 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die EXACT_EVENT_REQUIRED

ops="$1"; frontend="$2"
frontend_sha=fe68db99b0d6dfe0b5288bee96db42e71836f844
frontend_tree=443a0135244a8be3a83a7620543db0e48e77accd
[[ "$ops" == "$GITHUB_WORKSPACE/professionals-ops" && "$frontend" == "$GITHUB_WORKSPACE/professionals-frontend" ]] ||
  die CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$frontend"; do
  status=$(git -C "$checkout" status --porcelain=v1 --untracked-files=all) || die CLEAN_CHECKOUT_REQUIRED
  [[ $(realpath "$checkout") == "$checkout" && -z "$status" ]] || die CLEAN_CHECKOUT_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" rev-parse HEAD^) == a15940c940b01517e761934717b7dae56db2ecfa ]] || die EXACT_OPERATIONS_REQUIRED
[[ $(git -C "$frontend" rev-parse HEAD) == "$frontend_sha" &&
   $(git -C "$frontend" rev-parse 'HEAD^{tree}') == "$frontend_tree" ]] || die EXACT_FRONTEND_REQUIRED

node_image='node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c'
controls="$ops/ci/professionals-member-entry"
umask 077
[[ -n ${RUNNER_TEMP:-} && -d "$RUNNER_TEMP" && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die HOST_SCRATCH_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-professionals.XXXXXXXX")
label='org.ilovedrrealty.professionals.member-entry'
run_key="$GITHUB_RUN_ID-${state##*.}"

cleanup() {
  local code=$? clean=1 ids id
  trap - EXIT
  set +e
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  for id in $ids; do
    if [[ "$id" =~ ^[a-f0-9]{64}$ &&
          $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$id") == "$run_key" ]]; then
      bounded 20s docker rm -f -v "$id" >/dev/null 2>&1 || clean=0
    else
      clean=0
    fi
  done
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  [[ -z "$ids" ]] || clean=0
  if [[ "$state" == "$RUNNER_TEMP"/ildr-professionals.* && -d "$state" && ! -L "$state" && $(realpath "$state") == "$state" ]]; then
    rm -rf -- "$state" || clean=0
  else
    clean=0
  fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'PROFESSIONALS_OWNED_CLEANUP commit=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT

mkdir "$state/frontend-work"
git -C "$frontend" archive --format=tar HEAD | tar -xf - -C "$state/frontend-work" || die FRONTEND_ARCHIVE_FAILED
verify_frontend() {
  python3 -I -B - "$frontend" "$state/frontend-work" <<'PY'
from hashlib import sha1
from pathlib import Path
import subprocess, sys
source, archived = map(Path, sys.argv[1:])
files = subprocess.check_output(['git', '-C', str(source), 'ls-tree', '-rz', 'HEAD']).split(b'\0')
for entry in filter(None, files):
    metadata, name = entry.split(b'\t', 1)
    mode, kind, object_id = metadata.split()
    path = archived / name.decode('utf-8')
    assert kind == b'blob' and mode in (b'100644', b'100755') and path.is_file() and not path.is_symlink()
    payload = path.read_bytes()
    assert sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest().encode() == object_id
PY
}
verify_frontend || die EXACT_ARCHIVED_FRONTEND_REQUIRED
printf '%s  %s\n' \
  ddcbb0e45b77854a30f8f22a767be96e5555ca7800652d452fe35b25792281f3 "$state/frontend-work/package-lock.json" \
  be3b1828f5b10cb69bcf24d289020360c6e36da68ac9b6064a70e9ade0ec32dd "$state/frontend-work/package.json" \
  d32f9a37792098bcad7deae4501e9a407747b8d6dcd95c97302605b0f4404dcb "$state/frontend-work/apps/landing/package.json" \
  4f4587eb432013b20c771d3a045fb04b67d146794c47c007e805299d2c562a43 "$state/frontend-work/apps/dashboard/package.json" \
  dc3722b78e0cf4bd7c110430e2aade1e4509434615965ab695c17ce096b85d59 "$state/frontend-work/tests/professionals-contract.test.cjs" \
  | sha256sum --check --status || die EXACT_PACKAGE_AND_TEST_PINS_REQUIRED

bounded 180s docker pull --platform linux/amd64 "$node_image" >/dev/null || die PINNED_NODE_PULL_FAILED
[[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$node_image") == linux/amd64 ]] ||
  die EXACT_NODE_PLATFORM_REQUIRED
prepare=(--label "$label=$run_key" --user "$(id -u):$(id -g)" --read-only --init --cap-drop ALL
         --security-opt no-new-privileges:true --memory 8g --memory-swap 8g --pids-limit 256 --cpus 2
         --tmpfs /tmp:rw,exec,size=128m --workdir /work
         --mount "type=bind,src=$state/frontend-work,dst=/work")
runtime_env=(env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true
             npm_config_userconfig=/tmp/ildr-user.npmrc npm_config_globalconfig=/tmp/ildr-global.npmrc
             npm_config_cache=/tmp/npm npm_config_update_notifier=false npm_config_audit=false npm_config_fund=false)
install_exit=0
bounded 500s docker run "${prepare[@]}" --network bridge "$node_image" "${runtime_env[@]}" \
  NODE_OPTIONS=--max-old-space-size=1024 npm_config_registry=https://registry.npmjs.org/ \
  npm ci --ignore-scripts --no-audit --no-fund --include=dev --include=optional \
  >"$state/npm-install.log" 2>&1 || install_exit=$?
if [[ "$install_exit" != 0 ]]; then
  python3 -I -B - "$state/npm-install.log" "$install_exit" <<'PY'
from pathlib import Path
import os, re, sys
with Path(sys.argv[1]).open('rb') as log:
    log.seek(max(0, os.fstat(log.fileno()).st_size - 131072))
    tail = log.read(131072)
allowed = set(b'''EAI_AGAIN ENOTFOUND ETIMEDOUT ESOCKETTIMEDOUT ERR_SOCKET_TIMEOUT ECONNREFUSED ECONNRESET
    ENETUNREACH EHOSTUNREACH E401 E403 E404 E429 E500 E502 E503 E504 EUSAGE ERESOLVE EINTEGRITY
    ETARGET ELOCKVERIFY EACCES EPERM ENOSPC ENOMEM EPIPE UNABLE_TO_VERIFY_LEAF_SIGNATURE
    SELF_SIGNED_CERT_IN_CHAIN CERT_HAS_EXPIRED'''.split())
matches = re.findall(rb'(?m)^npm (?:error|ERR!) code ([A-Z][A-Z0-9_]{1,45})[ \t\r]*$', tail)
codes = ','.join(item.decode('ascii') for item in sorted(set(matches) & allowed)) or 'none'
print(f'PROFESSIONALS_INSTALL native={int(sys.argv[2])} codes={codes}')
PY
  die LOCKED_FRONTEND_INSTALL_FAILED
fi
printf 'PROFESSIONALS_INSTALL native=0 codes=none\n'
verify_frontend || die FRONTEND_CHANGED_DURING_INSTALL

contract_exit=0
bounded 120s docker run "${prepare[@]}" --network none "$node_image" "${runtime_env[@]}" \
  node --test --test-reporter=tap tests/professionals-contract.test.cjs \
  >"$state/contracts.log" 2>&1 || contract_exit=$?
python3 -I -B - "$state/contracts.log" "$contract_exit" <<'PY' || die PROFESSIONALS_CONTRACT_RESULT_INVALID
from pathlib import Path
import os, re, sys
native = int(sys.argv[2])
with Path(sys.argv[1]).open('rb') as log:
    log.seek(max(0, os.fstat(log.fileno()).st_size - 131072))
    body = log.read(131072)
if native:
    indices = sorted({int(index) for index in re.findall(rb'(?m)^not ok ([1-9]|1[0-6]) - ', body)})
    summary = {}
    for key in (b'tests', b'pass', b'fail', b'cancelled', b'skipped', b'todo'):
        matches = re.findall(rb'(?m)^# ' + key + rb' (0|[1-9]|1[0-6])\r?$', body)
        summary[key.decode()] = matches[0].decode() if len(matches) == 1 else 'unavailable'
    failed = ','.join(map(str, indices)) or 'none'
    totals = ' '.join(f'{key}={value}' for key, value in summary.items())
    print(f'PROFESSIONALS_CONTRACT_FAILURE native={native} failed_indices={failed} {totals}')
    sys.exit(0)
lines = body.splitlines()
for row in (b'# tests 16', b'# pass 16', b'# fail 0', b'# skipped 0', b'# todo 0'):
    assert lines.count(row) == 1
assert any(line.endswith(b'professional landing leads with member signup in both languages and keeps provider applications separate')
           and line.startswith(b'ok ') for line in lines)
print('PROFESSIONALS_CONTRACTS native=0 tests=16 pass=16 bilingual_hero=pass')
PY
[[ "$contract_exit" == 0 ]] || die PROFESSIONALS_CONTRACTS_FAILED
verify_frontend || die FRONTEND_CHANGED_DURING_CONTRACTS

landing_exit=0
bounded 620s docker run "${prepare[@]}" --network none \
  --mount "type=bind,src=$controls,dst=/controls,readonly" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 \
  node /controls/build-landing-fixture.mjs >"$state/landing-build.log" 2>&1 || landing_exit=$?
python3 -I -B - "$state/landing-build.log" "$landing_exit" <<'PY' || die LANDING_DIAGNOSTIC_INVALID
from pathlib import Path
import os, re, sys
with Path(sys.argv[1]).open('rb') as log:
    log.seek(max(0, os.fstat(log.fileno()).st_size - 262144))
    tail = log.read(262144)
phases = rb'(?:env|sys_net|fixture_bind|npm_build|typecheck|fixture_counts|fixture_close)'
signals = rb'(?:none|other|SIGHUP|SIGINT|SIGKILL|SIGABRT|SIGSEGV|SIGTERM)'
errors = rb'(?:GUARD_FAILED|EACCES|EADDRINUSE|EADDRNOTAVAIL|ENOENT|ENOTDIR|EEXIST|EPERM)'
patterns = (rb'PROFESSIONALS_LANDING_PHASE phase=' + phases + rb' status=(?:started|pass)',
            rb'PROFESSIONALS_LANDING_NATIVE phase=(?:npm_build|typecheck) exit=(?:[0-9]{1,3}|none) signal=' + signals,
            rb'PROFESSIONALS_LANDING_FAILURE phase=' + phases + rb' code=' + errors)
found = [line for line in tail.splitlines() if any(re.fullmatch(p, line) for p in patterns)]
assert len(found) <= 13
for line in found:
    print(line.decode('ascii'))
native = int(sys.argv[2])
print(f'PROFESSIONALS_LANDING_CONTAINER native={native}')
if native:
    for label, pattern in [('type_error', rb'Type error:|error TS[0-9]+'), ('missing_module', rb'Module not found:'),
                           ('static_params', rb'generateStaticParams'), ('export', rb'Export encountered'),
                           ('fetch', rb'fetch failed'), ('swc', rb'Failed to load SWC|@next/swc'),
                           ('npm_code', rb'(?m)^npm (?:error|ERR!) code (?:EUSAGE|ENOENT|EACCES|ENOTDIR|ENOSPC)\b'),
                           ('compiler_missing', rb'\bnext: (?:not found|Permission denied)\b')]:
        if re.search(pattern, tail):
            print(f'PROFESSIONALS_LANDING_DIAGNOSTIC {label}')
else:
    required = [b'PROFESSIONALS_LANDING_PHASE phase=' + phase + b' status=pass'
                for phase in (b'env', b'sys_net', b'fixture_bind', b'npm_build', b'typecheck', b'fixture_counts', b'fixture_close')]
    required += [b'PROFESSIONALS_LANDING_NATIVE phase=' + phase + b' exit=0 signal=none'
                 for phase in (b'npm_build', b'typecheck')]
    assert all(found.count(line) == 1 for line in required)
PY
[[ "$landing_exit" == 0 ]] || die LANDING_BUILD_OR_TYPECHECK_FAILED
python3 -I -B - "$state/landing-build.log" "$state/frontend-work/apps/landing/out" <<'PY'
from pathlib import Path
import re, sys
with Path(sys.argv[1]).open('rb') as log:
    body = log.read(512 * 1024)
assert len(re.findall(rb'(?m)^PROFESSIONALS_LANDING_FIXTURE list=[1-9][0-9]{0,2} detail=[1-9][0-9]{0,2} unexpected=0$', body)) == 1
out = Path(sys.argv[2])
listing_id = '10000000-0000-4000-8000-000000000001'
assert b'Property Services Directory' in (out/'professionals/index.html').read_bytes()
assert b'Synthetic fixture listing' in (out/f'listing/{listing_id}/index.html').read_bytes()
assert f'https://ilovedrrealty.com/listing/{listing_id}/'.encode() in (out/'sitemap.xml').read_bytes()
print('PROFESSIONALS_LANDING native=0 typecheck=success static_export=success synthetic_listing=pass')
PY
printf 'PROFESSIONALS_ACCEPTANCE operations=%s frontend=%s frontend_tree=%s run=%s attempt=1\n' \
  "$GITHUB_SHA" "$frontend_sha" "$frontend_tree" "$GITHUB_RUN_ID"

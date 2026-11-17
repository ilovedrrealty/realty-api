#!/usr/bin/env bash
set -euo pipefail

die() { printf 'MEMBER_LANDING_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ $# == 4 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 &&
   ${GITHUB_RUN_ATTEMPT:-} == 1 ]] || die HOST_REQUIRED
[[ ${GITHUB_REF:-} == refs/heads/codex-ci-seller-valuation-landing-20261117 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die EXACT_EVENT_REQUIRED

ops="$1"; fixture="$2"; frontend="$3"; backend="$4"
parent=cca546e94da5b36e164cadf9093374fd62c4960a
g06_regression_ops=36ea3f1a58905453030454077edb27bf8922937f
fixture_commit=441efdc288c3a38b8607c816c684abec080d83ad
fixture_tree=6c01c7577e343d5e2542d4f8a84cbc0f103e4b5d
frontend_commit=35f6380f8a3046cd1b3e8768f4207e8b2c31be3c
frontend_tree=c52b762985864e63931adc2b022fa14e3a427332
frontend_files=520
backend_commit=8936bec9f42f2affed00cea7cf952f49535898bb
backend_tree=ff89fa613e001bbc5564e8da9ea778ada69329c1
[[ "$frontend_commit" =~ ^[a-f0-9]{40}$ && "$frontend_tree" =~ ^[a-f0-9]{40}$ &&
   "$backend_commit" =~ ^[a-f0-9]{40}$ && "$backend_tree" =~ ^[a-f0-9]{40}$ &&
   "$frontend_files" =~ ^[1-9][0-9]{0,3}$ ]] || die S01_EXACT_PAIR_NOT_PINNED
[[ "$ops" == "$GITHUB_WORKSPACE/member-landing-ops" && "$fixture" == "$GITHUB_WORKSPACE/member-landing-fixture" &&
   "$frontend" == "$GITHUB_WORKSPACE/member-landing-source" &&
   "$backend" == "$GITHUB_WORKSPACE/member-landing-backend" ]] || die EXACT_CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$fixture" "$frontend" "$backend"; do
  status=$(git -C "$checkout" status --porcelain=v1 --untracked-files=all) || die STATUS_REFUSED
  [[ $(realpath "$checkout") == "$checkout" && -z "$status" ]] || die CLEAN_CHECKOUT_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" && $(git -C "$ops" rev-parse HEAD^) == "$parent" &&
   $(git -C "$fixture" rev-parse HEAD) == "$fixture_commit" && $(git -C "$fixture" rev-parse 'HEAD^{tree}') == "$fixture_tree" &&
   $(git -C "$frontend" rev-parse HEAD) == "$frontend_commit" &&
   $(git -C "$frontend" rev-parse 'HEAD^{tree}') == "$frontend_tree" &&
   $(git -C "$backend" rev-parse HEAD) == "$backend_commit" &&
   $(git -C "$backend" rev-parse 'HEAD^{tree}') == "$backend_tree" ]] || die EXACT_CHECKOUTS_REQUIRED
expected=$'.github/workflows/network-member-entry-landing.yml\nci/cumulative-landing/run-member-entry.sh\nci/cumulative-landing/run-shortlist-login.mjs\nci/cumulative-landing/seller-valuation-browser.mjs'
[[ $(git -C "$ops" diff --name-only "$parent" "$GITHUB_SHA") == "$expected" ]] || die EXACT_OPERATIONS_DELTA_REQUIRED
tools="$ops/ci/cumulative-landing"
fixture_tools="$fixture/ci/frontend"
printf '%s  %s\n' \
  c57087318e513ad8d2d38c0fc08fa5f081bc77932aa3a89e33734ca8f2f560c4 "$tools/landing.cjs" \
  6d701e7702200f8090c05d0a7327a5d595e802fa9e6de7cd7c75026e40bc3e9f "$tools/run-shortlist-login.mjs" \
  d52b7d1e6183b0936188bde98b53831ff856fdb61d819b9eb9587a8080fb328a "$tools/seller-valuation-browser.mjs" \
  ade09eb22229c52a6f4ea0feec0d2053966fa4cda134be50529d26f53ed05469 "$tools/development-browser.mjs" \
  af438d517fcd98883fbf8237cfc8e22d53b662209704c8a4ea5a03a75ae9a38c "$tools/spatial-browser.mjs" \
  f20dc2ee8d949f5e971a47bef6334d6d7cd1aa7a741a4a58d8005adb4e7ffa57 "$tools/editorial-browser.mjs" \
  a166dc60598e76b7fdcc3fe2aa5253bc56952b48fd99e4cab5eb4a24523a548e "$fixture_tools/landing_fixture.cjs" \
  0a1abbdbd65a82e67ded20a0603a21fc9321a1b1aa5fd83dbccf1c049ffc4110 "$fixture_tools/landing_fixture.test.cjs" \
  | sha256sum --check --status || die ACCEPTED_TOOL_HASH_MISMATCH

node_image='node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c'
browser_image='mcr.microsoft.com/playwright@sha256:ee15712262f017b8157421b212b12099146645f44b1bc5c0514cbbe6a229b1d0'
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
  python3 -I -B - "$frontend" "$state/frontend-work" "$frontend_files" <<'PY'
from hashlib import sha1
from pathlib import Path
import subprocess, sys
source, archived = map(Path, sys.argv[1:3])
files = subprocess.check_output(['git', '-C', str(source), 'ls-tree', '-rz', 'HEAD']).split(b'\0')
files = [entry for entry in files if entry]
assert len(files) == int(sys.argv[3])
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
  9b2fc413ff653011eed0e127ccea3db23d326a47a85040151c99da488b8b494d "$state/frontend-work/tests/search-map.test.cjs" \
  7d6e336151b00f9aed669b088f18a76ca9b9fd2e1c7e75dbcb44f9e4d22e3655 "$state/frontend-work/tests/spatial-search-geometry.test.cjs" \
  f2ccbdce0958f0fc1cf53e434eb25613ce3869e431142d02e662f0a8e6e42e07 "$state/frontend-work/tests/development-inventory-public.test.cjs" \
  abc08dfc11671b1615878dd2d6c39c234832e028484d9a648ad5dea547cb7f79 "$state/frontend-work/tests/seller-valuation-intake.test.cjs" \
  | sha256sum --check --status || die EXACT_LANDING_UNIT_SOURCES_REQUIRED
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

bounded 45s docker run --rm "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work,readonly" \
  "$node_image" env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true \
  node --test --test-concurrency=1 --test-reporter=tap tests/public-editorial.test.cjs \
  >"$state/editorial-unit.log" 2>&1 || die EDITORIAL_PUBLIC_UNIT_FAILED
python3 -I -B - "$state/editorial-unit.log" <<'PY' || die EXACT_EDITORIAL_PUBLIC_UNIT_RESULT_REQUIRED
import re, sys
from pathlib import Path
path = Path(sys.argv[1])
assert path.stat().st_size <= 131072
lines = path.read_bytes().splitlines()
summary = re.compile(rb'# (tests|pass|fail|skipped) (\d+)')
assert [(m[1], m[2]) for line in lines if (m := summary.fullmatch(line))] == [
    (b'tests', b'5'), (b'pass', b'5'), (b'fail', b'0'), (b'skipped', b'0')]
print('MEMBER_LANDING_EDITORIAL_UNIT tests=5 pass=5 fail=0 skipped=0')
PY
bounded 45s docker run --rm "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work,readonly" \
  "$node_image" env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true \
  node --test --test-concurrency=1 --test-reporter=tap tests/search-map.test.cjs tests/spatial-search-geometry.test.cjs \
  >"$state/spatial-unit.log" 2>&1 || die SPATIAL_UNIT_FAILED
grep -qx '# tests 19' "$state/spatial-unit.log" && grep -qx '# pass 19' "$state/spatial-unit.log" &&
  grep -qx '# fail 0' "$state/spatial-unit.log" && grep -qx '# skipped 0' "$state/spatial-unit.log" ||
  die EXACT_SPATIAL_UNIT_RESULT_REQUIRED
printf 'MEMBER_LANDING_SPATIAL_UNIT tests=19 pass=19 fail=0 skipped=0\n'
bounded 45s docker run --rm "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work,readonly" \
  "$node_image" env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true \
  node --test --test-concurrency=1 --test-reporter=tap tests/development-inventory-public.test.cjs \
  >"$state/development-unit.log" 2>&1 || die DEVELOPMENT_PUBLIC_UNIT_FAILED
grep -qx '# tests 5' "$state/development-unit.log" && grep -qx '# pass 5' "$state/development-unit.log" &&
  grep -qx '# fail 0' "$state/development-unit.log" && grep -qx '# skipped 0' "$state/development-unit.log" ||
  die EXACT_DEVELOPMENT_PUBLIC_UNIT_RESULT_REQUIRED
printf 'MEMBER_LANDING_DEVELOPMENT_UNIT tests=5 pass=5 fail=0 skipped=0\n'
bounded 45s docker run --rm "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work,readonly" \
  "$node_image" env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true \
  node --test --test-concurrency=1 --test-reporter=tap tests/seller-valuation-intake.test.cjs \
  >"$state/seller-valuation-unit.log" 2>&1 || die SELLER_VALUATION_UNIT_FAILED
grep -qx '# tests 7' "$state/seller-valuation-unit.log" && grep -qx '# pass 7' "$state/seller-valuation-unit.log" &&
  grep -qx '# fail 0' "$state/seller-valuation-unit.log" && grep -qx '# skipped 0' "$state/seller-valuation-unit.log" ||
  die EXACT_SELLER_VALUATION_UNIT_RESULT_REQUIRED
printf 'MEMBER_LANDING_SELLER_VALUATION_UNIT tests=7 pass=7 fail=0 skipped=0\n'

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

mkdir "$state/browser-run"
cp -- "$tools/run-shortlist-login.mjs" "$tools/editorial-browser.mjs" "$tools/spatial-browser.mjs" \
  "$tools/development-browser.mjs" "$tools/seller-valuation-browser.mjs" "$state/browser-run/"
bounded 180s docker pull --platform linux/amd64 "$browser_image" >/dev/null || die PINNED_BROWSER_PULL_FAILED
[[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$browser_image") == linux/amd64 ]] ||
  die EXACT_BROWSER_PLATFORM_REQUIRED
bounded 60s python3 -I -B - "$state/browser-run" <<'PY' || die PINNED_PLAYWRIGHT_REQUIRED
import base64, hashlib, io, json, sys, tarfile, urllib.request
from pathlib import Path, PurePosixPath
browser = Path(sys.argv[1])
url = 'https://registry.npmjs.org/playwright-core/-/playwright-core-1.64.1.tgz'
with urllib.request.urlopen(url, timeout=30) as response:
    assert response.geturl() == url
    raw = response.read(20 * 1024 * 1024 + 1)
assert 0 < len(raw) <= 20 * 1024 * 1024
assert base64.b64encode(hashlib.sha512(raw).digest()).decode() == 'BC4s8y6SEVA6Q1rSxZTto8hI1KUI3xXlHGZgvGqP4IqPdI/a6FX9H29IDNz7vKo9l21b6LfYosScPL6M3xxyDQ=='
target = browser/'node_modules/playwright-core'
target.mkdir(parents=True)
with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as archive:
    members = archive.getmembers()
    assert len(members) <= 6000 and sum(member.size for member in members) <= 90 * 1024 * 1024
    for member in members:
        parts = PurePosixPath(member.name).parts
        assert parts and parts[0] == 'package' and '..' not in parts and (member.isdir() or member.isfile())
        path = target.joinpath(*parts[1:])
        if member.isdir():
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as out:
                out.write(archive.extractfile(member).read())
assert json.loads((target/'package.json').read_bytes())['version'] == '1.64.1'
PY

browser_exit=0
bounded 240s docker run --rm --network none --label "$label=$run_key" \
  --user "$(id -u):$(id -g)" --read-only --cap-drop ALL --security-opt no-new-privileges:true \
  --memory 2g --memory-swap 2g --pids-limit 256 --cpus 2 --shm-size 384m \
  --tmpfs /tmp:rw,exec,size=192m --workdir /rehearsal \
  --mount "type=bind,src=$state/browser-run,dst=/rehearsal,readonly" \
  --mount "type=bind,src=$state/frontend-work/apps/landing/out,dst=/landing-export,readonly" \
  --mount "type=bind,src=$fixture_tools,dst=/listing-fixture,readonly" \
  "$browser_image" env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
  GITHUB_ACTIONS=true GITHUB_REPOSITORY=ilovedrrealty/realty-api node /rehearsal/run-shortlist-login.mjs \
  >"$state/login-browser.log" 2>&1 || browser_exit=$?
python3 -I -B - "$state/login-browser.log" "$browser_exit" <<'PY' || die LANDING_LOGIN_BROWSER_RESULT_INVALID
import json, re, sys
from pathlib import Path
p = Path(sys.argv[1])
assert p.stat().st_size <= 262144
lines = p.read_text(encoding='utf-8', errors='replace').splitlines()
if int(sys.argv[2]):
    allowed = ('host','static_export','browser','viewing_listing_en','viewing_listing_es',
               'anonymous_login','wrong_password','form_login_redirect',
               'existing_session_redirect','editorial_export','editorial_absent','editorial_published',
               'editorial_updated','editorial_removed','spatial_export','spatial_initial','spatial_initial_http',
               'spatial_initial_cards','spatial_open_map','spatial_polygon','spatial_polygon_apply','spatial_polygon_http',
               'spatial_polygon_cards','spatial_radius','spatial_radius_apply','spatial_radius_http',
               'spatial_reload','spatial_legacy_mismatch','spatial_clear','development_export',
               'development_context','development_routes','seller_export','seller_context','seller_selling','seller_cta',
               'seller_ordinary','seller_form_fields','seller_consent','seller_initial_post','seller_receipt_unconfirmed',
               'seller_retry','seller_receipt_confirmed')
    allowed += tuple(f'development_{state}_{step}'
                     for state in ('absent','published','unconfirmed','withdrawn_focus','withdrawn_reload')
                     for step in ('navigation','response','rendered'))
    codes = set('''ACTUAL_SEARCH_EXPORT_REQUIRED SEARCH_FILTERS_MUST_SURVIVE_SHAPE_CHANGES SAME_PUBLIC_EXCLUDED_COUNT_REQUIRED
                  FULLSCREEN_MAP_SURFACE_REQUIRED BROAD_RESULTS_IN_ACTIVE_SHAPE SPA_MUST_NOT_BROADEN_MISSING_SCOPE
                  LIST_RESPONSE_MISSING MAP_RESPONSE_MISSING MAP_SHIM_METHOD_MISSING MAP_INITIALIZATION_FAILED
                  BROWSER_PAGE_EXCEPTION LOOPBACK_CLEANUP_REQUIRED PLAYWRIGHT_TIMEOUT JS_TYPE_ERROR JS_SYNTAX_ERROR
                  UNCLASSIFIED_BROWSER_FAILURE DEVELOPMENT_GET_RESPONSE_MISSING DEVELOPMENT_HTTP_2XX
                  DEVELOPMENT_HTTP_3XX DEVELOPMENT_HTTP_4XX DEVELOPMENT_HTTP_5XX DEVELOPMENT_HTTP_OTHER
                  DEVELOPMENT_NO_STORE_RESPONSE_REQUIRED DEVELOPMENT_PRIVATE_RESPONSE_LEAK
                  DEVELOPMENT_WITHDRAWN_CONTENT_VISIBLE ACTUAL_DEVELOPMENT_LISTING_EXPORT_REQUIRED
                  DEVELOPMENT_FLOOR_PLAN_SAFETY_REQUIRED DATED_DEVELOPMENT_PAYMENT_REQUIRED DATED_DEVELOPMENT_UPDATE_REQUIRED
                  DEVELOPMENT_MOBILE_CARDS_REQUIRED DEVELOPMENT_PHASE_FILTER_REQUIRED DEVELOPMENT_STALE_STOCK_VISIBLE
                  DEVELOPMENT_STATIC_STOCK_OR_DRAFT_REQUIRED_ABSENT DEVELOPMENT_PUBLIC_API_LIFECYCLE_REQUIRED
                  SELLER_POST_RESPONSE_MISSING SELLER_POST_CONTRACT_REJECTED ACTUAL_SELLING_EXPORT_REQUIRED
                  SELLER_PRIVATE_FORM_INCLUDED_COMMENTS ORDINARY_SELLER_INCLUDES_VALUATION_FIELDS SELLER_MOBILE_FORM_REQUIRED
                  SELLER_CONSENT_REQUIRED SELLER_SENT_WITHOUT_CONSENT SELLER_FORM_NOT_PRESERVED_ON_RETRY
                  SELLER_UNCONFIRMED_RECEIPT_NOT_ANNOUNCED SELLER_ANONYMOUS_RECEIPT_REQUIRED
                  SELLER_SYNTHETIC_PRIVATE_DATA_IN_STATIC_HTML SELLER_SYNTHETIC_RECEIPT_COUNT_MISMATCH'''.split())
    marker = re.compile(r'VIEWINGS_LANDING_LOGIN_FAILED stage=([a-z_]+) code=([A-Z0-9_]+) phase=(caught|unhandled)')
    matched = next((m for line in lines if (m := marker.fullmatch(line)) and m[1] in allowed and m[2] in codes), None)
    if matched:
        stage, code, phase = matched.groups()
    else:
        phase = 'bootstrap'
        stage = 'browser_process'
        body = '\n'.join(lines)
        if 'ERR_MODULE_NOT_FOUND' in body:
            code = 'STATIC_IMPORT_MODULE_MISSING'
        elif 'SyntaxError:' in body:
            code = 'STATIC_IMPORT_SYNTAX_ERROR'
        elif 'TimeoutError:' in body:
            code = 'UNHANDLED_PLAYWRIGHT_TIMEOUT'
        elif 'TypeError:' in body:
            code = 'UNHANDLED_BROWSER_TYPE_ERROR'
        elif 'Executable doesn\'t exist' in body:
            code = 'CHROMIUM_EXECUTABLE_MISSING'
        else:
            code = 'NO_SAFE_BROWSER_MARKER'
    print(f'MEMBER_LANDING_LOGIN_BROWSER native={int(sys.argv[2])} stage={stage} code={code} phase={phase}')
else:
    prefix = 'VIEWINGS_LANDING_LOGIN_BROWSER '
    results = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
    assert results == [dict(status='PASS', actual_export=True, synthetic_auth_endpoint=True,
                           real_candidate_backend_in_this_container=False, rejected_password_without_cookie=True,
                           http_only_cookie=True, shortlist_regression=True, viewing_cta_en_es=True,
                           viewing_listing_redirect_en_es=True, existing_session_redirect=True,
                           loopback_only=True, fixtures_closed=True)]
    print('MEMBER_LANDING_LOGIN_BROWSER ' + json.dumps(results[0], sort_keys=True, separators=(',', ':')))
    prefix = 'EDITORIAL_RUNTIME_BROWSER '
    editorial = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
    assert editorial == [dict(status='PASS', actual_export=True, both_languages=True, body_absent_in_static_html=True,
                              runtime_publish=True, runtime_update=True, runtime_unpublish=True, noindex_reader=True,
                              no_stale_body_after_removal=True, public_no_store=True, synthetic_public_endpoint=True,
                              same_unrebuilt_export=True)]
    print('MEMBER_LANDING_EDITORIAL_BROWSER ' + json.dumps(editorial[0], sort_keys=True, separators=(',', ':')))
    prefix = 'SPATIAL_SEARCH_BROWSER '
    spatial = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
    assert spatial == [dict(status='PASS', actual_search_export=True, width_390_touch=True, local_map_shim=True,
                             polygon_map_taps=True, radius_controls=True, scope_url_reload_and_clear=True,
                             exact_list_map_requests=True, excluded_unlocated_visible=True,
                             legacy_missing_scope_fails_closed=True, external_maps_network_used=False,
                             synthetic_public_endpoint=True)]
    print('MEMBER_LANDING_SPATIAL_BROWSER ' + json.dumps(spatial[0], sort_keys=True, separators=(',', ':')))
    prefix = 'DEVELOPMENT_INVENTORY_BROWSER '
    development = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
    assert development == [dict(status='PASS', actual_listing_export=True, bilingual_390_touch_cards=True, phase_filter=True,
                                 sourced_current_and_unconfirmed=True, safe_plan_link=True, dated_updates_and_payment_labels=True,
                                 anonymous_public_no_store=True, unavailable_before_and_after=True,
                                 withdrawal_removes_same_page=True, no_false_static_stock_or_draft=True,
                                 synthetic_public_endpoint=True)]
    print('MEMBER_LANDING_DEVELOPMENT_BROWSER ' + json.dumps(development[0], sort_keys=True, separators=(',', ':')))
    prefix = 'SELLER_VALUATION_INTAKE_BROWSER '
    seller = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
    assert seller == [dict(status='PASS', actual_static_export=True, selling_cta_en_es=True,
                            mobile_structured_fields=True, contact_consent_required=True, anonymous_seller_post=True,
                            subject_only_private_channel=True, independent_submission_ids=True,
                            same_submission_id_on_missing_ack_retry=True, success_requires_valuation_ack=True,
                            ordinary_seller_unchanged=True, synthetic_loopback_only=True)]
    print('MEMBER_LANDING_SELLER_VALUATION_BROWSER ' + json.dumps(seller[0], sort_keys=True, separators=(',', ':')))
PY
[[ "$browser_exit" == 0 ]] || die LANDING_LOGIN_BROWSER_FAILED
printf 'MEMBER_LANDING_LOGIN_PAIR frontend=%s frontend_tree=%s backend=%s backend_tree=%s g06_regression_ops=%s\n' \
  "$frontend_commit" "$frontend_tree" "$backend_commit" "$backend_tree" "$g06_regression_ops"
printf 'MEMBER_LANDING_ACCEPTANCE operations=%s frontend=%s tree=%s fixture=%s run=%s attempt=1\n' \
  "$GITHUB_SHA" "$frontend_commit" "$frontend_tree" "$fixture_commit" "$GITHUB_RUN_ID"

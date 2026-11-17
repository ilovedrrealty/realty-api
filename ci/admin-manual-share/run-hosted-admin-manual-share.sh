#!/usr/bin/env bash
# Hosted-only build and browser exercise. The application and browser share an
# owned network namespace with just loopback. The build is never published.
set -euo pipefail

die() { printf 'BROWSER_REHEARSAL_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ $# == 5 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api ]] || die CI_REQUIRED
[[ ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 && ${GITHUB_RUN_ATTEMPT:-} == 1 ]] || die STANDARD_LINUX_REQUIRED
[[ ${GITHUB_REF:-} == refs/heads/codex-ci-listing-brochures-browser-20261117-v4 && ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die EXACT_EVENT_REQUIRED

ops="$1"; frontend="$2"; backend="$3"; migration="$4"; landing_relay="$5"
[[ "$ops" == "$GITHUB_WORKSPACE/browser-ops" && "$frontend" == "$GITHUB_WORKSPACE/frontend-base" &&
   "$backend" == "$GITHUB_WORKSPACE/browser-backend" && "$migration" == "$GITHUB_WORKSPACE/migration-relay" &&
   "$landing_relay" == "$GITHUB_WORKSPACE/landing-relay" ]] ||
  die CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$frontend" "$backend" "$migration" "$landing_relay"; do
  checkout_status=$(git -C "$checkout" status --porcelain=v1 --untracked-files=all) || die CLEAN_CHECKOUT_REQUIRED
  [[ $(realpath "$checkout") == "$checkout" && -z "$checkout_status" ]] || die CLEAN_CHECKOUT_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" && $(git -C "$ops" rev-parse HEAD^) == 63baee9a1cb12a074a8f8f89392b62a47ea8e062 ]] ||
  die EXACT_OPERATIONS_PARENT_REQUIRED
operations_delta=$(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) || die EXACT_OPERATIONS_DELTA_REQUIRED
[[ "$operations_delta" == $'.github/workflows/admin-manual-share.yml\nci/admin-manual-share/run-hosted-admin-manual-share.sh\nci/admin-manual-share/run-listing-brochures.mjs' ]] ||
  die EXACT_OPERATIONS_DELTA_REQUIRED
frontend_commit=2d070cb965fe2f921822fdb8436ecab72a4099f0
frontend_tree=8d6b533d9967d6a5fd335c7557e80bc321af8a0b
frontend_paths=552
frontend_brochure_test_sha=039308edad77ef1254357cda5d4332475370dd1fb7dccbaa5cb502631d05531d
frontend_blog_test_sha=265dd02c033c5783bfb04564986878bd4b863dfa5292be2b02a1bfa3867b615a
frontend_export_test_sha=3e7e7005ea90dec134deb01ea8892b9409cbe7115d5f37eba17f0d76b9cf09d6
frontend_test_count=25
backend_commit=7f8a3c4682fd0037383926051e96fe155ab77583
backend_tree=16a94a41d1b0fa07493df7a74e4a124071cf7aaf
migration_head=e12ac7d49f03
browser_failure_stages='fixture,english_mobile_listing,english_fresh_download,spanish_hidden_listing,spanish_public_rent_download,withdrawal_blocks_stale_download,withdrawn_page_hides_action,'\
'en_request_attachment,en_read_detail,en_pdf_file,en_pdf_parse,en_pdf_text,en_review_receipt,'\
'es_request_attachment,es_read_detail,es_pdf_file,es_pdf_parse,es_pdf_text,es_review_receipt'
browser_checks=(
  english_mobile_attachment_contains_fresh_native_price_and_terms
  spanish_hidden_rental_has_only_declared_usd_rate_and_safe_fields
  existing_public_exterior_media_preserved
  withdrawal_blocks_stale_click_and_hides_download_on_reload
  actual_loopback_only_without_outbound
)
[[ "$frontend_commit" =~ ^[a-f0-9]{40}$ && "$frontend_tree" =~ ^[a-f0-9]{40}$ &&
   "$frontend_brochure_test_sha" =~ ^[a-f0-9]{64}$ && "$frontend_blog_test_sha" =~ ^[a-f0-9]{64}$ &&
   "$frontend_export_test_sha" =~ ^[a-f0-9]{64}$ &&
   "$frontend_paths" =~ ^[1-9][0-9]{2,3}$ &&
   "$frontend_test_count" =~ ^[1-9][0-9]?$ ]] || die FRONTEND_PINS_INCOMPLETE
[[ "$browser_failure_stages" =~ ^[a-z][a-z0-9_]*(,[a-z][a-z0-9_]*)+$ ]] || die BROWSER_PINS_INCOMPLETE
[[ $(git -C "$frontend" rev-parse HEAD) == "$frontend_commit" &&
   $(git -C "$frontend" rev-parse 'HEAD^{tree}') == "$frontend_tree" ]] || die EXACT_FRONTEND_REQUIRED
[[ $(git -C "$backend" rev-parse HEAD) == "$backend_commit" &&
   $(git -C "$backend" rev-parse 'HEAD^{tree}') == "$backend_tree" ]] || die EXACT_BACKEND_REQUIRED
[[ $(git -C "$migration" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 ]] ||
  die EXACT_ACCEPTED_PYTHON_BUILD_REQUIRED
[[ $(git -C "$landing_relay" rev-parse HEAD) == d49acd7d48e92dac6d06b1678570bc137fb6a5ba ]] ||
  die EXACT_ACCEPTED_LANDING_TOOLS_REQUIRED

tools="$ops/ci/admin-manual-share"
dockerfile="$migration/ci/migration/services163.Dockerfile"
for file in serve_admin_share_fixture.py serve_editorial_fixture.py serve_listing_brochures_fixture.py \
  run-listing-brochures.mjs; do
  [[ -f "$tools/$file" && ! -L "$tools/$file" ]] || die EXACT_FIXTURE_SOURCE_REQUIRED
done
printf '%s  %s\n' e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9 "$dockerfile" \
  | sha256sum --check --status || die HISTORICAL_TOOL_HASH_MISMATCH

node_image='node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c'
python_digest='sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60'
python_image="docker.io/library/python:3.11-slim@$python_digest"
postgres_image='docker.io/library/postgres:17.6@sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409'
browser_image='mcr.microsoft.com/playwright@sha256:ee15712262f017b8157421b212b12099146645f44b1bc5c0514cbbe6a229b1d0'

umask 077
[[ -n ${RUNNER_TEMP:-} && -d "$RUNNER_TEMP" && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die HOST_SCRATCH_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-browser.XXXXXXXX")
nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
label='org.ilovedrrealty.browser.network-live-share'
run_key="$GITHUB_RUN_ID-$nonce"
app_image="ildr-browser-api:$run_key"
uids="$(id -u):$(id -g)"

owned() {
  [[ "$1" =~ ^[a-f0-9]{64}$ && $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$1") == "$run_key" ]]
}

cleanup() {
  local code=$? clean=1 id owned_ids owned_images tagged_id
  trap - EXIT
  set +e
  owned_ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  for id in $owned_ids; do
    [[ "$id" != "${pg:-}" ]] || continue
    if owned "$id"; then
      bounded 20s docker stop --time 5 "$id" >/dev/null 2>&1 || true
      bounded 15s docker rm -f -v "$id" >/dev/null 2>&1 || clean=0
    else
      clean=0
    fi
  done
  if [[ -n ${pg:-} ]]; then
    if owned "$pg"; then
      bounded 20s docker stop --time 5 "$pg" >/dev/null 2>&1 || true
      bounded 15s docker rm -f -v "$pg" >/dev/null 2>&1 || clean=0
    else
      clean=0
    fi
  fi
  owned_images=$(bounded 15s docker image ls -q --no-trunc --filter "label=$label=$run_key" | sort -u) || clean=0
  if [[ -n "$owned_images" ]]; then
    tagged_id=$(bounded 15s docker image inspect --format '{{.Id}}' "$app_image") || clean=0
    if [[ "$owned_images" =~ ^sha256:[a-f0-9]{64}$ && "$owned_images" == "$tagged_id" ]]; then
      bounded 15s docker image rm "$app_image" >/dev/null 2>&1 || clean=0
    else
      clean=0
    fi
  fi
  owned_images=$(bounded 15s docker image ls -q --no-trunc --filter "label=$label=$run_key") || clean=0
  owned_ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  [[ -z "$owned_ids" && -z "$owned_images" ]] || clean=0
  if [[ "$state" == "$RUNNER_TEMP"/ildr-browser.* && -d "$state" && ! -L "$state" && $(realpath "$state") == "$state" ]]; then
    rm -rf -- "$state" || clean=0
  else
    clean=0
  fi
  if [[ "$clean" != 1 || -e "$state" ]]; then
    printf 'BROWSER_REHEARSAL_CLEANUP_UNVERIFIED\n' >&2
    exit 1
  fi
  printf 'BROWSER_REHEARSAL_OWNED_CLEANUP commit=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT

mkdir "$state/frontend-work" "$state/fixtures" "$state/browser-run" "$state/python-context" "$state/landing-run"
mkdir "$state/npm-cache"
cp "$backend/requirements.txt" "$state/python-context/requirements.txt"
cp "$tools/run-listing-brochures.mjs" "$state/browser-run/"

# Public, pinned dependency preparation. No app or browser process is running yet.
for image in "$node_image" "$python_image" "$postgres_image" "$browser_image"; do
  bounded 180s docker pull --platform linux/amd64 "$image" >/dev/null || die PINNED_IMAGE_PULL_FAILED
  [[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image") == linux/amd64 ]] || die EXACT_IMAGE_PLATFORM_REQUIRED
done
bounded 600s docker build --platform linux/amd64 --pull=false --quiet --label "$label=$run_key" \
  --build-arg "PYTHON_DIGEST=$python_digest" --file "$dockerfile" --tag "$app_image" "$state/python-context" >"$state/python-build.log" 2>&1 || die API_IMAGE_BUILD_FAILED

git -C "$frontend" archive --format=tar HEAD | tar -xf - -C "$state/frontend-work" || die FRONTEND_ARCHIVE_FAILED
verify_frontend_files() {
  python3 -I -B - "$frontend" "$state/frontend-work" "$frontend_paths" <<'PY'
from hashlib import sha1
from pathlib import Path
import subprocess, sys
source, copy = map(Path, sys.argv[1:3])
files = subprocess.check_output(['git', '-C', str(source), 'ls-tree', '-rz', 'HEAD']).split(b'\0')
assert len(files) == int(sys.argv[3]) + 1 and not files[-1]
for entry in filter(None, files):
    metadata, name = entry.split(b'\t', 1)
    mode, kind, object_id = metadata.split()
    path = copy / name.decode('utf-8')
    assert kind == b'blob' and mode in (b'100644', b'100755') and path.is_file() and not path.is_symlink()
    payload = path.read_bytes()
    assert sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest().encode() == object_id
PY
}
verify_frontend_files || die EXACT_ARCHIVED_FRONTEND_REQUIRED
printf '%s  %s\n' \
  8df3c4c580c65694bc727488fc0ec02d88ab1577e5fa16c509f4897b58e60f33 "$state/frontend-work/package-lock.json" \
  45893e6dfebaad4d2e73f7e7d6b26f176883bc1ba8c8dc025f188cc1b4d12344 "$state/frontend-work/apps/landing/package.json" \
  "$frontend_brochure_test_sha" "$state/frontend-work/tests/listing-brochure.test.cjs" \
  "$frontend_blog_test_sha" "$state/frontend-work/tests/blog-articles.test.cjs" \
  "$frontend_export_test_sha" "$state/frontend-work/tests/listing-export.test.cjs" \
  | sha256sum --check --status || die EXACT_PACKAGE_AND_TEST_PINS_REQUIRED

prepare=(--label "$label=$run_key" --user "$uids" --read-only --cap-drop ALL --security-opt no-new-privileges:true
         --memory 8g --memory-swap 8g --pids-limit 256 --cpus 2 --tmpfs /tmp:rw,exec,size=128m)

install_exit=0
bounded 500s docker run "${prepare[@]}" --network bridge --workdir /work \
  --mount "type=bind,src=$state/frontend-work,dst=/work" --mount "type=bind,src=$state/npm-cache,dst=/npm-cache" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1024 \
  npm_config_registry=https://registry.npmjs.org/ npm_config_userconfig=/tmp/ildr-user.npmrc \
  npm_config_globalconfig=/tmp/ildr-global.npmrc npm_config_cache=/npm-cache \
  npm_config_update_notifier=false npm_config_audit=false npm_config_fund=false npm_config_ignore_scripts=true \
  node /usr/local/lib/node_modules/npm/bin/npm-cli.js ci --ignore-scripts --no-audit --no-fund --include=dev --include=optional \
  >"$state/npm-install.log" 2>&1 || install_exit=$?
if [[ "$install_exit" != 0 ]]; then
  python3 -I -B - "$state/npm-install.log" "$install_exit" <<'PY'
from pathlib import Path
import os, re, sys

limit = 131072
native = int(sys.argv[2])
allowed = set(b'''EAI_AGAIN ENOTFOUND ETIMEDOUT ESOCKETTIMEDOUT ERR_SOCKET_TIMEOUT ECONNREFUSED ECONNRESET
    ENETUNREACH EHOSTUNREACH E401 E403 E404 E429 E500 E502 E503 E504 EUSAGE ERESOLVE EINTEGRITY
    ETARGET ELOCKVERIFY EACCES EPERM ENOSPC ENOMEM EPIPE UNABLE_TO_VERIFY_LEAF_SIGNATURE
    SELF_SIGNED_CERT_IN_CHAIN CERT_HAS_EXPIRED'''.split())
try:
    with Path(sys.argv[1]).open('rb') as log:
        log.seek(max(0, os.fstat(log.fileno()).st_size - limit))
        tail = log.read(limit)
except OSError:
    tail = b''
matches = re.findall(rb'(?m)^npm (?:error|ERR!) code ([A-Z][A-Z0-9_]{1,45})[ \t\r]*$', tail)
codes = ','.join(item.decode('ascii') for item in sorted(set(matches) & allowed)) or 'none'
if native == 124:
    summary = 'bounded_timeout'
elif codes != 'none':
    summary = 'recognized_npm_code'
elif re.search(rb'(?m)^npm (?:error|ERR!) network(?:[ \t\r]|$)', tail):
    summary = 'npm_network_summary'
else:
    summary = 'unclassified_install_failure'
print(f'LISTING_BROCHURE_INSTALL native={native} summary={summary} codes={codes}')
PY
  die LOCKED_FRONTEND_INSTALL_FAILED
fi
printf 'LISTING_BROCHURE_INSTALL native=0 summary=success codes=none\n'
verify_frontend_files || die INSTALLED_FRONTEND_SOURCE_CHANGED
swc_exit=0
bounded 30s docker run --rm "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work,readonly" \
  "$node_image" env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true node -e '
  const fs = require("node:fs");
  if (process.version !== "v24.22.1" || process.platform !== "linux" || process.arch !== "x64" ||
      fs.readdirSync("/sys/class/net").join(",") !== "lo") process.exit(2);
  const lock = require("/work/package-lock.json").packages;
  for (const name of ["next", "@next/swc-linux-x64-gnu"]) {
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
  console.log("LISTING_BROCHURE_SWC native=0 locked_version=15.5.18 platform=linux-x64-gnu");
' >"$state/swc.log" 2>&1 || swc_exit=$?
if [[ "$swc_exit" != 0 ]]; then
  printf 'LISTING_BROCHURE_SWC native=%s\n' "$swc_exit"
  die LOCKED_LINUX_SWC_UNAVAILABLE
fi
[[ $(<"$state/swc.log") == 'LISTING_BROCHURE_SWC native=0 locked_version=15.5.18 platform=linux-x64-gnu' ]] ||
  die SWC_RESULT_REQUIRED
cat "$state/swc.log"

pretest_exit=0
bounded 80s docker run "${prepare[@]}" --network none --workdir /work \
  --mount "type=bind,src=$state/frontend-work,dst=/work" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1536 \
  node --test --test-concurrency=1 --test-reporter=tap tests/listing-export.test.cjs tests/blog-articles.test.cjs tests/listing-brochure.test.cjs \
  >"$state/landing-contract.log" 2>&1 || pretest_exit=$?
python3 -I -B - "$state/landing-contract.log" "$pretest_exit" "$frontend_test_count" <<'PY' || die DECLARED_LANDING_PRETEST_REQUIRED
from pathlib import Path
import re, sys
log = Path(sys.argv[1])
assert log.stat().st_size <= 262144
lines = log.read_bytes().splitlines()
rows = {group[1]: int(group[2]) for row in lines if (group := re.fullmatch(rb"# (tests|pass|fail|skipped) ([0-9]{1,3})", row))}
print(f"LISTING_BROCHURE_LANDING_PRETESTS native={int(sys.argv[2])} tests={rows.get(b'tests', 0)} "
      f"pass={rows.get(b'pass', 0)} fail={rows.get(b'fail', 0)}")
for row in lines:
    if group := re.fullmatch(rb"not ok ([1-9][0-9]{0,2}) - [A-Za-z0-9 _.,:/-]{1,180}", row):
        print("LISTING_BROCHURE_LANDING_PRETEST_FAILURE index=" + group[1].decode("ascii"))
assert sys.argv[2] == "0"
count = int(sys.argv[3])
assert rows == {b"tests": count, b"pass": count, b"fail": 0, b"skipped": 0}
PY
verify_frontend_files || die LANDING_PRETEST_SOURCE_CHANGED

landing_fixture="$landing_relay/ci/frontend"
landing_script="$landing_relay/ci/cumulative-landing/landing.cjs"
landing_server="$landing_relay/ci/browser/serve_landing_fixture.mjs"
printf '%s  %s\n' \
  d74afd5e90685485a5a8edb64fa72288b284b1676f3a9db8f5293423479c8842 "$landing_script" \
  a166dc60598e76b7fdcc3fe2aa5253bc56952b48fd99e4cab5eb4a24523a548e "$landing_fixture/landing_fixture.cjs" \
  0a1abbdbd65a82e67ded20a0603a21fc9321a1b1aa5fd83dbccf1c049ffc4110 "$landing_fixture/landing_fixture.test.cjs" \
  d56bafd5da3b3e54d45b98764851a1d3a042be938c8a4c378e6acadf436eb80e "$landing_server" \
  | sha256sum --check --status || die ACCEPTED_LANDING_SOURCE_REQUIRED
# Keep the accepted offline input/build harness. Its API port is also used by
# the real isolated API at browser time, in a different network namespace.
python3 -I -B - "$landing_script" "$state/landing-run/landing.cjs" <<'PY' || die LANDING_FIXTURE_PORT_BIND_REQUIRED
from pathlib import Path
import sys
source, copy = map(Path, sys.argv[1:])
data = source.read_bytes()
for before, after in (
    (b"NEXT_PUBLIC_API_URL: 'http://127.0.0.1:8917'", b"NEXT_PUBLIC_API_URL: 'http://127.0.0.1:8918'"),
    (b"await startFixture(8917)", b"await startFixture(8918)"),
):
    assert data.count(before) == 1
    data = data.replace(before, after, 1)
with copy.open("xb") as stream:
    stream.write(data)
PY
landing_exit=0
bounded 690s docker run "${prepare[@]}" --network none --workdir /work \
  --mount "type=bind,src=$state/frontend-work,dst=/work" \
  --mount "type=bind,src=$landing_fixture,dst=/fixture,readonly" \
  --mount "type=bind,src=$state/landing-run,dst=/rehearsal,readonly" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true node /rehearsal/landing.cjs \
  >"$state/landing.log" 2>&1 || landing_exit=$?
python3 -I -B - "$state/landing.log" "$state/frontend-work/apps/landing/out" "$landing_exit" <<'PY' || die LISTING_BROCHURE_LANDING_EXPORT_REQUIRED
from hashlib import sha256
from pathlib import Path
import json, re, sys
log, output = map(Path, sys.argv[1:3])
assert log.stat().st_size <= 8 * 1024 * 1024
lines = log.read_bytes().splitlines()
names = {"fixture_contract", "landing_declared_full_build_synthetic_input",
         "landing_full_strict_typecheck_with_generated_routes"}
prefix = b"CUMULATIVE_LANDING_PHASE "
phases = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
for phase in phases:
    assert phase["name"] in names and type(phase["native_exit"]) in (int, type(None))
    assert phase["native_exit"] is None or 0 <= phase["native_exit"] <= 255
    assert all(type(phase[key]) is int and 0 <= phase[key] <= 4194304 for key in ("stdout_bytes", "stderr_bytes"))
    print(f'LISTING_BROCHURE_NEXT_PHASE stage={phase["name"]} native={phase["native_exit"]}'
          f' stdout_bytes={phase["stdout_bytes"]} stderr_bytes={phase["stderr_bytes"]}')
allowed = {b"FIXTURE_PORT_REFUSED", b"FIXTURE_LISTEN_REFUSED", b"FIXTURE_BINDING_REFUSED",
           b"FIXTURE_CONTRACT_NOT_ACCEPTED", b"UNEXPECTED_GENERATED_TYPE_SOURCE_CHANGE",
           b"ACTUAL_SYNTHETIC_REQUESTS_REQUIRED", b"NATIVE_PHASE_FAILED", b"SYNTHETIC_EXPORT_AND_METADATA_REQUIRED"}
failure_prefix = b"CUMULATIVE_LANDING_FAILED "
for line in lines:
    if line.startswith(failure_prefix) and line[len(failure_prefix):] in allowed:
        print("LISTING_BROCHURE_NEXT_FAILURE " + line[len(failure_prefix):].decode("ascii"))
if sys.argv[3] != "0":
    raw = b"\n".join(lines)
    categories = (
        (b"Type error:", "TYPE_ERROR"),
        (b"Failed to compile", "COMPILE_FAILED"),
        (b"Module not found", "MODULE_NOT_FOUND"),
        (b"missing generateStaticParams", "STATIC_PARAMS_MISSING"),
        (b"Error occurred prerendering page", "PRERENDER_FAILED"),
        (b"useSearchParams() should be wrapped", "SEARCH_PARAMS_SUSPENSE_REQUIRED"),
        (b"ECONNREFUSED", "FIXTURE_CONNECTION_REFUSED"),
        (b"LISTING_EXPORT_", "LISTING_EXPORT_VALIDATION"),
        (b"fetch failed", "SYNTHETIC_FETCH_FAILED"),
        (b"NEXT_PUBLIC_API_URL", "API_URL_SETTING_REFERENCED"),
        (b"npm error Lifecycle script", "NPM_LIFECYCLE"),
    )
    for token, tag in categories:
        if token in raw:
            print(f"LISTING_BROCHURE_NEXT_DETAIL code={tag}")
    sources = re.findall(
        rb"(?m)^(?:\./)?(src/[A-Za-z0-9_./\[\]-]+\.(?:tsx?|jsx?))(?::([0-9]{1,5}):([0-9]{1,5}))?\s*$",
        raw,
    )
    for source, line, column in sorted(set(sources))[:10]:
        print("LISTING_BROCHURE_NEXT_DETAIL file=" + source.decode("ascii")
              + " line=" + (line or b"0").decode("ascii") + " column=" + (column or b"0").decode("ascii"))
    known_routes = {b"/local-guides", b"/local-guides/compare", b"/search", b"/listing/[id]"}
    paths = re.findall(rb'(?m)(?:prerendering page|Page) "(/[A-Za-z0-9_/\[\]-]+)"', raw)
    for route in sorted(set(paths) & known_routes):
        print("LISTING_BROCHURE_NEXT_DETAIL route=" + route.decode("ascii"))
    for count in re.findall(rb"(?m)^(?:#|\xe2\x84\xb9) fail ([1-9][0-9]{0,2})$", raw)[:3]:
        print("LISTING_BROCHURE_NEXT_DETAIL unit_failures=" + count.decode("ascii"))
    started = b"Creating an optimized production build" in raw or bool(re.search(rb"Next\.js [0-9]+\.[0-9]+\.[0-9]+", raw))
    print(f"LISTING_BROCHURE_NEXT_DETAIL next_started={int(started)}")
    for script, line, column in sorted(set(re.findall(
            rb"(?m)^test at (?:/work/)?tests/(listing-export|blog-articles)\.test\.cjs:([0-9]{1,4}):([0-9]{1,4})",
            raw,
    )))[:8]:
        print(f"LISTING_BROCHURE_NEXT_DETAIL test={script.decode()} line={line.decode()} column={column.decode()}")
    codes = re.findall(rb"\b(?:ERR_[A-Z0-9_]{3,45}|EACCES|EEXIST|ENOENT|ENOSPC|ENOMEM|SIGKILL|SIGSEGV|EPIPE|MODULE_NOT_FOUND)\b", raw)
    for code in sorted(set(codes))[:14]:
        print("LISTING_BROCHURE_NEXT_DETAIL native_code=" + code.decode("ascii"))
    headers = re.findall(
        rb"(?m)^\s*(?:> )?((?:Error|TypeError|AssertionError|SyntaxError|RangeError|ReferenceError)(?: \[[A-Z0-9_]{3,45}\])?): ([^\r\n]{1,320})",
        raw,
    )
    for kind, message in list(dict.fromkeys(headers))[:5]:
        # Recompose only exception headers from the offline synthetic build,
        # removing quoted values, paths, addresses and credential-shaped text.
        cleaned = re.sub(rb"['\"][^'\"]*['\"]|https?://\S+|\S+@\S+|(?:/[A-Za-z0-9_.-]+){2,}|[A-Za-z0-9_=-]{20,}", b"[redacted]", message)
        cleaned = re.sub(rb"[^A-Za-z0-9 .,:!?()_\[\]-]", b" ", cleaned)
        cleaned = b" ".join(cleaned.split())[:192]
        if cleaned:
            print(f"LISTING_BROCHURE_NEXT_DETAIL exception={kind.decode('ascii')} summary={cleaned.decode('ascii')}")
    segment = "none"
    tails = {"stdout": [], "stderr": []}
    label = b" landing_declared_full_build_synthetic_input"
    for line in lines:
        if line == b"CUMULATIVE_LANDING_STDOUT" + label:
            segment = "stdout"
        elif line == b"CUMULATIVE_LANDING_STDERR" + label:
            segment = "stderr"
        elif line.startswith((b"CUMULATIVE_LANDING_PHASE ", b"CUMULATIVE_LANDING_END ", b"CUMULATIVE_LANDING_FAILED ")):
            segment = "none"
        elif segment in tails and line.strip():
            tails[segment].append(line)
    for stream, records in tails.items():
        for line in records[-18:]:
            if re.search(rb"(?i)(?:secret|password|private_key|authorization|api.?key|access.?token|cookie|credentials)", line):
                continue
            line = re.sub(rb"['\"][^'\"]*['\"]|https?://\S+|\S+@\S+|(?:/[A-Za-z0-9_.-]+){2,}|[A-Za-z0-9_=-]{20,}", b"[redacted]", line)
            line = re.sub(rb"[^A-Za-z0-9 .,:!?()_\[\]>+&=-]", b" ", line)
            summary = b" ".join(line.split())[:168]
            if summary:
                print(f"LISTING_BROCHURE_NEXT_SYNTHETIC_TAIL stream={stream} summary={summary.decode('ascii')}")
assert sys.argv[3] == "0"
assert len(phases) == len(names) and {phase["name"] for phase in phases} == names
assert all(phase["native_exit"] == 0 and phase["child_group_absent"] == True for phase in phases)
assert sum(line.startswith(b"CUMULATIVE_LANDING_RESULT ") for line in lines) == 1
for route in ("listing/10000000-0000-0000-0000-000000000001/index.html", "search/index.html"):
    path = output / route
    assert path.is_file() and not path.is_symlink()
    data = path.read_bytes()
    assert b"Synthetic undisclosed property address" not in data and b"SYNTHETIC_BROCHURE_PRIVATE_REVIEW" not in data
    print(f"LISTING_BROCHURE_EXPORT_SHA256 route={route} sha256={sha256(data).hexdigest()}")
print("LISTING_BROCHURE_NEXT_EXPORT native=0 listing=static search=static typecheck=success")
PY
verify_frontend_files || die LANDING_BUILD_SOURCE_CHANGED

python3 -I -B - "$state/frontend-work" "$state/browser-run" <<'PY' || die LANDING_BROWSER_RUNTIME_INPUT_REQUIRED
import base64, hashlib, io, json, tarfile, urllib.request
from pathlib import Path, PurePosixPath
import sys
workspace, browser = map(Path, sys.argv[1:])
dist = workspace/'apps/landing/out'
detail = dist/'listing/10000000-0000-0000-0000-000000000001/index.html'
assert detail.is_file() and not detail.is_symlink() and 0 < detail.stat().st_size <= 1024 * 1024
assets = list((dist/'_next/static/chunks').rglob('*.js'))
assert 0 < len(assets) <= 250
assert all(f.is_file() and not f.is_symlink() and f.stat().st_size <= 16 * 1024 * 1024 for f in assets)
assert any(b'http://127.0.0.1:8918' in file.read_bytes() for file in assets)
manifest = workspace/'node_modules/pdf-lib/package.json'
assert manifest.is_file() and not manifest.is_symlink() and manifest.stat().st_size <= 16384
assert json.loads(manifest.read_bytes())['version'] == '1.17.1'
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
print('LISTING_BROCHURE_RUNTIME_INPUT landing=static pdf_lib=1.17.1 playwright=1.64.1')
PY

# The database owns an isolated network namespace containing only loopback.
db="ildr_browser_ci_$nonce"
pg=$(bounded 25s docker create --label "$label=$run_key" --network none \
  --security-opt no-new-privileges:true --memory 768m --memory-swap 768m --pids-limit 96 --cpus 1 \
  --tmpfs /var/lib/postgresql/data:rw,size=384m \
  --env POSTGRES_HOST_AUTH_METHOD=trust --env "POSTGRES_DB=$db" \
  "$postgres_image" postgres -p 55449 -h 127.0.0.1) || die POSTGRES_CREATE_FAILED
owned "$pg" && [[ $(bounded 15s docker inspect --format '{{.HostConfig.NetworkMode}}' "$pg") == none ]] || die OWNED_ISOLATED_POSTGRES_REQUIRED
bounded 25s docker start "$pg" >/dev/null || die POSTGRES_START_FAILED
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  if bounded 3s docker exec "$pg" pg_isready -h 127.0.0.1 -p 55449 -U postgres -d "$db" >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 && $(bounded 5s docker exec "$pg" ls /sys/class/net) == lo ]] || die LOOPBACK_POSTGRES_REQUIRED
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d "$db" -v ON_ERROR_STOP=1 \
  -c 'CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN' \
  -c "COMMENT ON DATABASE $db IS 'ildr-admin-share-ci:$nonce'" >/dev/null || die DATABASE_IDENTITY_MARKER_FAILED
postgres_version_num=$(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d "$db" \
  -v ON_ERROR_STOP=1 -c 'SHOW server_version_num') || die EXACT_POSTGRES_VERSION_REQUIRED
[[ "$postgres_version_num" == 170006 ]] || die EXACT_POSTGRES_VERSION_REQUIRED

runtime=(--label "$label=$run_key" --network "container:$pg" --user "$uids" --read-only --init
         --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 256 --cpus 2
         --env GITHUB_ACTIONS=true --env GITHUB_REPOSITORY=ilovedrrealty/realty-api)

api=$(bounded 25s docker create "${runtime[@]}" --memory 2g --memory-swap 2g \
  --tmpfs /tmp:rw,exec,size=128m --env "ILDR_BROWSER_CI_NONCE=$nonce" \
  --env "ILDR_BROCHURE_APP_COMMIT=$backend_commit" --env "ILDR_BROCHURE_APP_TREE=$backend_tree" \
  --env "ILDR_BROCHURE_MIGRATION=$migration_head" \
  --mount "type=bind,src=$backend,dst=/application,readonly" \
  --mount "type=bind,src=$tools,dst=/tools,readonly" \
  --mount "type=bind,src=$state/fixtures,dst=/browser-fixtures" \
  "$app_image" /usr/local/bin/python -I -B /tools/serve_listing_brochures_fixture.py) || die API_CREATE_FAILED
owned "$api" && bounded 25s docker start "$api" >/dev/null || die API_START_FAILED
landing=$(bounded 25s docker create "${runtime[@]}" --memory 512m --memory-swap 512m \
  --mount "type=bind,src=$state/frontend-work/apps/landing/out,dst=/landing-dist,readonly" \
  --mount "type=bind,src=$landing_server,dst=/serve-landing.mjs,readonly" \
  "$node_image" node /serve-landing.mjs) || die LANDING_CREATE_FAILED
owned "$landing" && bounded 25s docker start "$landing" >/dev/null || die LANDING_START_FAILED

ready=0
for ((attempt=0; attempt<90; attempt++)); do
  if [[ -s "$state/fixtures/fixtures.json" ]] && bounded 4s docker exec "$api" /usr/local/bin/python -I -B -c \
    'from urllib.request import urlopen; assert all(urlopen(url,timeout=1).status==200 for url in ("http://127.0.0.1:8918/health","http://127.0.0.1:8917/listing/10000000-0000-0000-0000-000000000001/"))' >/dev/null 2>&1; then ready=1; break; fi
  if [[ $(bounded 5s docker inspect --format '{{.State.Running}}' "$api") != true ]]; then
    if bounded 5s docker logs --tail 120 "$api" >"$state/api-start.log" 2>&1; then
      python3 -I -B - "$state/api-start.log" <<'PY'
import json,re,sys
from pathlib import Path
log=Path(sys.argv[1]); prefix=b'BROWSER_API_FIXTURE_FAILURE '
assert log.stat().st_size <= 131072
reports=[line[len(prefix):] for line in log.read_bytes().splitlines() if line.startswith(prefix)]
assert len(reports)<=1
if reports:
    value=json.loads(reports[0]); assert set(value)=={'stage','error_type','fixture_line','sqlstate'}
    assert value['stage'] in {'checkout','claim_database','configure_application','load_application','bootstrap_database',
        'apply_alembic','seed_accounts','seed_lead','seed_listing_brochure',
        'write_fixture','serve_application'}
    assert re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}',value['error_type'])
    assert value['fixture_line'] is None or type(value['fixture_line']) is int and 0<value['fixture_line']<=1000
    assert value['sqlstate'] is None or value['stage']=='apply_alembic' and re.fullmatch(r'[0-9A-Z]{5}',value['sqlstate'])
    print(prefix.decode()+json.dumps(value,sort_keys=True,separators=(',',':')))
PY
    fi
    die API_FIXTURE_EXITED_DURING_START
  fi
  sleep 1
done
[[ "$ready" == 1 && $(stat -c %a "$state/fixtures/fixtures.json") == 600 ]] || die PRIVATE_SYNTHETIC_FIXTURE_NOT_READY

browser=$(bounded 25s docker create "${runtime[@]}" --memory 2g --memory-swap 2g --shm-size 384m \
  --tmpfs /tmp:rw,exec,size=192m --env HOME=/tmp \
  --env PLAYWRIGHT_BROWSERS_PATH=/ms-playwright --env ILDR_BROWSER_FIXTURES=/browser-fixtures/fixtures.json \
  --mount "type=bind,src=$state/browser-run,dst=/rehearsal,readonly" \
  --mount "type=bind,src=$state/fixtures,dst=/browser-fixtures,readonly" \
  --mount "type=bind,src=$state/frontend-work/node_modules,dst=/package-source,readonly" \
  "$browser_image" node /rehearsal/run-listing-brochures.mjs) || die BROWSER_CREATE_FAILED
owned "$browser" || die OWNED_BROWSER_REQUIRED
client_exit=0
bounded 300s docker start --attach "$browser" >"$state/browser.log" 2>&1 || client_exit=$?
container_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$browser")
[[ "$container_state" =~ ^[0-9]+\ (true|false)\ (true|false)$ ]] || die BROWSER_NATIVE_STATE_REQUIRED
printf 'BROWSER_REHEARSAL_NATIVE client=%s state=%s\n' "$client_exit" "$container_state"

python3 -I -B - "$state/browser.log" "$browser_failure_stages" "${browser_checks[@]}" <<'PY' || die LISTING_BROCHURE_BROWSER_RESULT_REQUIRED
from pathlib import Path
import json
import re
import sys

path = Path(sys.argv[1])
assert path.stat().st_size <= 131072
lines = path.read_bytes().splitlines()
allowed_stages = set(sys.argv[2].split(','))
geometry = rb'LISTING_BROCHURE_GEOMETRY locale=(?:en|es)(?: -?[0-9]{1,4}(?:\.[0-9]{1,2})?){8}'
measurements = [line for line in lines if re.fullmatch(geometry, line)]
assert len(measurements) <= 2
for measurement in measurements:
    print(measurement.decode('ascii'))
diagnostic = rb'LISTING_BROCHURE_DIAGNOSTIC type=(?:Error|TypeError|RangeError|SyntaxError|TimeoutError|OtherError) line=(?:[0-9]{1,4}|none) code=(?:MODULE_NOT_FOUND|ERR_INVALID_ARG_TYPE|ENOENT|EACCES|Z_DATA_ERROR|none)'
diagnostics = [line for line in lines if re.fullmatch(diagnostic, line)]
assert len(diagnostics) <= 1
for row in diagnostics:
    print(row.decode('ascii'))
failure = b'LISTING_BROCHURE_BROWSER_FAILURE '
failures = [line[len(failure):] for line in lines if line.startswith(failure)]
assert len(failures) <= 1
if failures:
    match = re.fullmatch(rb'([a-z][a-z0-9_]{0,38}) ([A-Z][A-Z0-9_]{0,52})(?: status=[1-5][0-9]{2})?', failures[0])
    assert match is not None and match[1].decode('ascii') in allowed_stages
    print(failure.decode('ascii') + failures[0].decode('ascii'))
    raise SystemExit(1)

prefix = b'LISTING_BROCHURE_BROWSER '
matches = [line[len(prefix):] for line in lines if line.startswith(prefix)]
assert len(matches) == 1 and len(matches[0]) <= 8192
value = json.loads(matches[0])
assert value == {'status': 'PASS', 'checks': sys.argv[3:]}
print(prefix.decode('ascii') + json.dumps(value, separators=(',', ':')))
PY
[[ "$client_exit" == 0 && "$container_state" == '0 false false' ]] || die NATIVE_BROWSER_FAILED

bounded 20s docker stop --time 10 "$api" >/dev/null || die API_STOP_FAILED
api_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$api") || die API_SHUTDOWN_STATE_REQUIRED
[[ "$api_state" == '0 false false' ]] || die API_GRACEFUL_SHUTDOWN_REQUIRED
[[ ! -e "$state/fixtures/fixtures.json" ]] || die SYNTHETIC_LOGIN_FILE_CLEANUP_REQUIRED
printf 'BROWSER_API_FIXTURE_SHUTDOWN state=%s login_file=absent\n' "$api_state"
backend_status=$(git -C "$backend" status --porcelain=v1 --untracked-files=all) || die API_SOURCE_CHANGED
[[ -z "$backend_status" ]] || die API_SOURCE_CHANGED
printf 'LISTING_BROCHURE_ACCEPTANCE operations=%s frontend=%s frontend_tree=%s backend=%s backend_tree=%s migration=%s postgres_version_num=%s run=%s attempt=1\n' \
  "$GITHUB_SHA" "$frontend_commit" "$frontend_tree" "$backend_commit" "$backend_tree" "$migration_head" "$postgres_version_num" "$GITHUB_RUN_ID"

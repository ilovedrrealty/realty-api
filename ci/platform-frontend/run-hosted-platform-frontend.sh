#!/usr/bin/env bash
# Disposable hosted build. Generated applications and fixture never leave scratch.
set -euo pipefail
umask 077

die() { printf 'INTEGRATED_FRONTEND_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ $# == 3 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 &&
   ${GITHUB_RUN_ATTEMPT:-} == 1 && ${GITHUB_REF:-} == refs/heads/codex-ci-property-network-frontend-20261118-v2 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die HOSTED_EXACT_EVENT_REQUIRED
ops="$1"; frontend="$2"; relay="$3"
[[ "$ops" == "$GITHUB_WORKSPACE/build-ops" && "$frontend" == "$GITHUB_WORKSPACE/frontend-source" &&
   "$relay" == "$GITHUB_WORKSPACE/landing-relay" ]] || die EXACT_CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$frontend" "$relay"; do
  current=$(git -C "$checkout" status --porcelain=v1 --untracked-files=all) || die CLEAN_CHECKOUT_REQUIRED
  [[ $(realpath "$checkout") == "$checkout" && -z "$current" && ! -e "$checkout/.env" && ! -L "$checkout/.env" ]] ||
    die CLEAN_CHECKOUT_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" rev-parse 'HEAD^') == a260fb8465f53c433396884cf1ea4f20a99955aa ]] || die EXACT_OPERATIONS_REQUIRED
delta=$(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) || die EXACT_DELTA_REQUIRED
[[ "$delta" == $'.github/workflows/property-network-frontend.yml\nci/platform-frontend/run-hosted-platform-frontend.sh' ]] || die EXACT_DELTA_REQUIRED
frontend_commit=7ec86e649fa1830fa159274759e22212035469bb
frontend_tree=0e492a8ef36c25074917a1e28bed346fd48541ac
frontend_paths=563
[[ $(git -C "$frontend" rev-parse HEAD) == "$frontend_commit" &&
   $(git -C "$frontend" rev-parse 'HEAD^{tree}') == "$frontend_tree" &&
   $(git -C "$frontend" rev-parse HEAD^) == e9a5041d6ca46f9b8448ea494e635c11cdea4434 &&
   $(git -C "$frontend" rev-parse HEAD^1^1) == 480c3bd53bcfd16d95e9e4d974c97429e00a0e68 &&
   $(git -C "$frontend" rev-parse HEAD^1^2) == 12f5b00fc5aa4040cb91c8ecc483812999c0e33e ]] || die EXACT_FRONTEND_REQUIRED
[[ $(git -C "$frontend" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) == \
   $'tests/due-work-reminders.test.cjs\ntests/service-operations.test.cjs' ]] || die EXACT_TEST_ONLY_SUCCESSOR_REQUIRED
[[ $(git -C "$relay" rev-parse HEAD) == d49acd7d48e92dac6d06b1678570bc137fb6a5ba ]] || die EXACT_RELAY_REQUIRED
landing_fixture="$relay/ci/frontend"
landing_script="$relay/ci/cumulative-landing/landing.cjs"
printf '%s  %s\n' \
  d74afd5e90685485a5a8edb64fa72288b284b1676f3a9db8f5293423479c8842 "$landing_script" \
  a166dc60598e76b7fdcc3fe2aa5253bc56952b48fd99e4cab5eb4a24523a548e "$landing_fixture/landing_fixture.cjs" \
  0a1abbdbd65a82e67ded20a0603a21fc9321a1b1aa5fd83dbccf1c049ffc4110 "$landing_fixture/landing_fixture.test.cjs" \
  | sha256sum --check --status || die EXACT_FIXTURE_SHA256_REQUIRED

[[ -d ${RUNNER_TEMP:-} && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die HOST_SCRATCH_REQUIRED
nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-integrated.XXXXXXXX")
label='org.ilovedrrealty.platform.frontend'
run_key="$GITHUB_RUN_ID-$nonce"
app_image="ildr-platform-frontend:$run_key"
cleanup() {
  local status=$? clean=1 ids id
  trap - EXIT
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  for id in $ids; do
    if [[ "$id" =~ ^[a-f0-9]{64}$ && $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$id") == "$run_key" ]]; then
      bounded 15s docker rm -f -v "$id" >/dev/null 2>&1 || clean=0
    else
      clean=0
    fi
  done
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  [[ -z "$ids" ]] || clean=0
  images=$(bounded 15s docker image ls -q --no-trunc --filter "label=$label=$run_key" | sort -u) || clean=0
  if [[ -n "$images" ]]; then
    tagged=$(bounded 15s docker image inspect --format '{{.Id}}' "$app_image") || clean=0
    if [[ "$images" =~ ^sha256:[a-f0-9]{64}$ && "$images" == "$tagged" ]]; then
      bounded 15s docker image rm "$app_image" >/dev/null 2>&1 || clean=0
    else
      clean=0
    fi
  fi
  images=$(bounded 15s docker image ls -q --no-trunc --filter "label=$label=$run_key") || clean=0
  [[ -z "$images" ]] || clean=0
  if [[ "$state" == "$RUNNER_TEMP"/ildr-integrated.* && $(realpath "$state") == "$state" && -O "$state" && ! -L "$state" ]]; then
    bounded 40s rm --recursive --force --one-file-system -- "$state" || clean=0
  else
    clean=0
  fi
  [[ ! -e "$state" && "$clean" == 1 ]] || die OWNED_CLEANUP_NOT_VERIFIED
  printf 'INTEGRATED_FRONTEND_OWNED_CLEANUP operations=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$status"
}
trap cleanup EXIT
mkdir "$state/frontend-work" "$state/npm-cache" "$state/landing-run"
source_repo="$frontend"
git -C "$source_repo" archive --format=tar "$frontend_commit" | tar -xf - -C "$state/frontend-work" || die ARCHIVE_REQUIRED
printf 'PLATFORM_FRONTEND_DIRECT_SOURCE commit=%s tree=%s live_parent=480c3bd53bcfd16d95e9e4d974c97429e00a0e68 paths=%s\n' \
  "$frontend_commit" "$frontend_tree" "$frontend_paths"
verify_source() {
  python3 -I -B - "$source_repo" "$state/frontend-work" "$frontend_commit" "$frontend_paths" <<'PY'
from hashlib import sha1
from pathlib import Path
import subprocess, sys

source, copy = map(Path, sys.argv[1:3])
entries = subprocess.check_output(["git", "-C", str(source), "ls-tree", "-rz", sys.argv[3]]).split(b"\0")
assert entries[-1] == b"" and len(entries) - 1 == int(sys.argv[4])
for entry in entries[:-1]:
    metadata, name = entry.split(b"\t", 1)
    mode, kind, object_id = metadata.split()
    path = copy / name.decode("utf-8")
    assert kind == b"blob" and mode in (b"100644", b"100755") and path.is_file() and not path.is_symlink()
    payload = path.read_bytes()
    assert sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest().encode() == object_id
PY
}
verify_source || die ARCHIVED_FRONTEND_MISMATCH
(
  cd "$state/frontend-work"
  sha256sum --check --status <<'PINS'
8df3c4c580c65694bc727488fc0ec02d88ab1577e5fa16c509f4897b58e60f33  package-lock.json
4f4587eb432013b20c771d3a045fb04b67d146794c47c007e805299d2c562a43  apps/dashboard/package.json
45893e6dfebaad4d2e73f7e7d6b26f176883bc1ba8c8dc025f188cc1b4d12344  apps/landing/package.json
PINS
) || die PACKAGE_SHA256_REQUIRED

node_image='node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c'
bounded 180s docker pull --platform linux/amd64 "$node_image" >/dev/null || die PINNED_NODE_PULL_FAILED
[[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$node_image") == linux/amd64 ]] || die PINNED_NODE_PLATFORM_REQUIRED
prepare=(--rm --label "$label=$run_key" --user "$(id -u):$(id -g)" --read-only --cap-drop ALL
         --security-opt no-new-privileges:true --memory 8g --memory-swap 8g --pids-limit 256 --cpus 2
         --tmpfs /tmp:rw,exec,size=128m --workdir /work)
native=0
bounded 500s docker run "${prepare[@]}" --network bridge \
  --mount "type=bind,src=$state/frontend-work,dst=/work" --mount "type=bind,src=$state/npm-cache,dst=/npm-cache" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1024 \
  npm_config_registry=https://registry.npmjs.org/ npm_config_userconfig=/tmp/ildr-user.npmrc \
  npm_config_globalconfig=/tmp/ildr-global.npmrc npm_config_cache=/npm-cache \
  npm_config_update_notifier=false npm_config_audit=false npm_config_fund=false npm_config_ignore_scripts=true \
  node /usr/local/lib/node_modules/npm/bin/npm-cli.js ci --ignore-scripts --no-audit --no-fund --include=dev --include=optional \
  >"$state/install.log" 2>&1 || native=$?
python3 -I -B - "$state/install.log" "$native" <<'PY' || die LOCKED_INSTALL_REQUIRED
from pathlib import Path
import re, sys

with Path(sys.argv[1]).open("rb") as log:
    log.seek(max(0, Path(sys.argv[1]).stat().st_size - 131072))
    data = log.read(131072)
allowed = {b'ENOSPC', b'ENOMEM', b'EINTEGRITY', b'EAI_AGAIN', b'ENOTFOUND', b'ETIMEDOUT', b'ECONNRESET', b'E429', b'E502', b'E503', b'E504', b'EACCES', b'EUSAGE'}
codes = set(re.findall(rb"(?m)^npm (?:error|ERR!) code ([A-Z0-9_]{2,45})\s*$", data)) & allowed
print(f"INTEGRATED_FRONTEND_INSTALL native={int(sys.argv[2])} codes=" + (b",".join(sorted(codes)).decode("ascii") or "none"))
assert sys.argv[2] == "0"
PY
verify_source || die INSTALLED_FRONTEND_MISMATCH

bounded 30s docker run "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work,readonly" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true node -e '
    const fs = require("node:fs");
    if (process.version !== "v24.22.1" || process.platform !== "linux" || process.arch !== "x64" ||
        fs.readdirSync("/sys/class/net").join(",") !== "lo") process.exit(2);
    const lock = require("/work/package-lock.json").packages;
    for (const name of ["next", "@next/swc-linux-x64-gnu"]) {
      const path = "/work/node_modules/" + name + "/package.json";
      if (!fs.existsSync(path) || !lock["node_modules/" + name].integrity ||
          lock["node_modules/" + name].version !== "15.5.18" || require(path).version !== "15.5.18") process.exit(3);
    }
    if (typeof require("/work/node_modules/@next/swc-linux-x64-gnu").transformSync !== "function") process.exit(4);
  ' >"$state/swc.log" 2>&1 || die LOCKED_OFFLINE_SWC_REQUIRED
printf 'INTEGRATED_FRONTEND_SWC native=0 version=15.5.18 platform=linux-x64-gnu\n'

tests=(tests/admin-analytics.test.cjs
       tests/arrival-guide.test.cjs
       tests/auth-locale.test.cjs
       tests/blog-articles.test.cjs
       tests/bulk-import-template.test.cjs
       tests/communications.test.cjs
       tests/contact-share.test.cjs
       tests/dashboard-tools.test.cjs
       tests/development-inventory-admin-fields.test.cjs
       tests/development-inventory-admin.test.cjs
       tests/development-inventory-public.test.cjs
       tests/dr-resources.test.cjs
       tests/due-work-reminders.test.cjs
       tests/editorial-admin.test.cjs
       tests/google-auth-fallback.test.cjs
       tests/history-shares.test.cjs
       tests/inquiry-feeds.test.cjs
       tests/lead-delivery.test.cjs
       tests/lead-tracking.test.cjs
       tests/listing-brochure.test.cjs
       tests/listing-export.test.cjs
       tests/listing-floor-plans.test.cjs
       tests/listing-performance.test.cjs
       tests/listing-room-tour.test.cjs
       tests/listing-short-gallery.test.cjs
       tests/local-market-report.test.cjs
       tests/location-review.test.cjs
       tests/maintenance-reminders.test.cjs
       tests/management-navigation.test.cjs
       tests/network-freshness.test.cjs
       tests/owner-arrival-activity.test.cjs
       tests/professionals-admin-entry.test.cjs
       tests/professionals-contract.test.cjs
       tests/property-inquiry.test.cjs
       tests/property-management.test.cjs
       tests/property-network-ui.test.cjs
       tests/property-network.test.cjs
       tests/property-viewings.test.cjs
       tests/public-editorial.test.cjs
       tests/public-image-url.test.cjs
       tests/purchase-cost-worksheet.test.cjs
       tests/registry-custody.test.cjs
       tests/roi-calculator.test.cjs
       tests/search-map.test.cjs
       tests/seller-valuation-intake.test.cjs
       tests/seller-valuation-workspace.test.cjs
       tests/service-conversation.test.cjs
       tests/service-favorites.test.cjs
       tests/service-operations.test.cjs
       tests/service-rebook.test.cjs
       tests/service-review.test.cjs
       tests/service-workflow.test.cjs
       tests/shared-shortlists.test.cjs
       tests/spatial-search-geometry.test.cjs
       tests/transaction-documents.test.cjs
       tests/transaction-rooms.test.cjs)
native=0
bounded 600s docker run "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1536 \
  node --test --test-concurrency=1 --test-reporter=tap "${tests[@]}" >"$state/contracts.log" 2>&1 || native=$?
python3 -I -B - "$state/contracts.log" "$native" <<'PY' || die EXACT_CONTRACTS_REQUIRED
from pathlib import Path
import json, re, sys

source = Path(sys.argv[1])
assert source.stat().st_size <= 4 * 1024 * 1024
data = source.read_bytes()
rows = {name: int(value) for name, value in re.findall(rb"(?m)^# (tests|pass|fail|skipped) ([0-9]{1,5})$", data)}
print("INTEGRATED_FRONTEND_CONTRACTS native=" + str(int(sys.argv[2])) + " " +
      " ".join(f"{key.decode()}={rows.get(key, 0)}" for key in (b"tests", b"pass", b"fail", b"skipped")))
failures = list(re.finditer(rb"(?m)^not ok ([1-9][0-9]{0,4}) - ([^\r\n]{1,500})$", data))
for position, failure in enumerate(failures[:12]):
    end = failures[position + 1].start() if position + 1 < len(failures) else len(data)
    block = data[failure.start():end]
    location = re.search(rb"(?m)^  location: ['\"]?/work/(tests/[A-Za-z0-9_./-]+\.test\.cjs):([0-9]{1,5}):", block)
    code = re.search(rb"(?m)^  code: ['\"]?([A-Z][A-Z0-9_]{1,60})", block)
    dependency = re.search(rb"Unexpected dependency ([A-Za-z0-9_./@-]{1,120})", block)
    title = failure[2].decode("utf-8", errors="replace")
    safe_title = (len(title) <= 240 and not re.search(r"[@\x00-\x1f]|://|[a-f0-9]{32}", title))
    report = {"index": int(failure[1]), "file": location[1].decode() if location else None,
              "line": int(location[2]) if location else None,
              "title": title if safe_title else None,
              "code": code[1].decode() if code else None,
              "missing_fixture_dependency": dependency[1].decode() if dependency else None}
    print("INTEGRATED_CONTRACT_FAILURE " + json.dumps(report, ensure_ascii=True, sort_keys=True))
assert sys.argv[2] == "0" and set(rows) == {b"tests", b"pass", b"fail", b"skipped"}
assert rows == {b"tests": 458, b"pass": 458, b"fail": 0, b"skipped": 0}
PY
verify_source || die CONTRACTS_ALTERED_SOURCE

native=0
bounded 360s docker run "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1536 \
  npm_config_userconfig=/tmp/ildr-user.npmrc npm_config_globalconfig=/tmp/ildr-global.npmrc \
  npm_config_cache=/tmp/npm npm_config_audit=false npm_config_fund=false npm_config_update_notifier=false \
  VITE_API_URL=http://127.0.0.1:8918 VITE_LANDING_URL=http://127.0.0.1:8917 \
  VITE_GOOGLE_CLIENT_ID=synthetic-ci.apps.googleusercontent.com \
  sh -c 'npm run typecheck --workspace=dashboard && npm run build --workspace=dashboard' \
  >"$state/dashboard.log" 2>&1 || native=$?
python3 -I -B - "$state/dashboard.log" "$state/frontend-work/apps/dashboard/dist/index.html" "$native" <<'PY' || die STRICT_DASHBOARD_REQUIRED
from pathlib import Path
import re, sys

log, index = map(Path, sys.argv[1:3])
assert log.stat().st_size <= 1048576
data = log.read_bytes()
if sys.argv[3] != "0":
    for file, line, col, code in re.findall(
        rb"(?m)^((?:src|\.\./\.\./packages)/[A-Za-z0-9_./-]+\.(?:ts|tsx))\(([0-9]{1,5}),([0-9]{1,5})\): error (TS[0-9]{4}):", data
    )[:12]:
        print(f"INTEGRATED_DASHBOARD_DIAGNOSTIC {file.decode()}:{line.decode()}:{col.decode()} {code.decode()}")
    print(f"INTEGRATED_DASHBOARD native={int(sys.argv[3])}")
    raise SystemExit(1)
assert b"tsc --noEmit" in data and b"vite build" in data and index.is_file() and not index.is_symlink()
assert b"127.0.0.1:8918" in b"".join(f.read_bytes() for f in index.parent.glob("assets/*.js") if f.is_file())
print("INTEGRATED_DASHBOARD native=0 typecheck=success build=success")
PY
verify_source || die DASHBOARD_ALTERED_SOURCE

# Build the unmodified application against the same loopback API origin used
# at browser time. Only the accepted build fixture's two port literals change.
python3 -I -B - "$landing_script" "$state/landing-run/landing.cjs" <<'PY_PORT'
from pathlib import Path
import sys
source, target = map(Path, sys.argv[1:])
data = source.read_bytes()
for before, after in (
    (b"NEXT_PUBLIC_API_URL: 'http://127.0.0.1:8917'", b"NEXT_PUBLIC_API_URL: 'http://127.0.0.1:8918'"),
    (b"await startFixture(8917)", b"await startFixture(8918)"),
):
    assert data.count(before) == 1
    data = data.replace(before, after, 1)
with target.open("xb") as stream:
    stream.write(data)
PY_PORT
native=0
bounded 690s docker run "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work" \
  --mount "type=bind,src=$landing_fixture,dst=/fixture,readonly" \
  --mount "type=bind,src=$state/landing-run,dst=/rehearsal,readonly" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true node /rehearsal/landing.cjs \
  >"$state/landing.log" 2>&1 || native=$?
python3 -I -B - "$state/landing.log" "$state/frontend-work/apps/landing/out" "$native" <<'PY' || die SYNTHETIC_STATIC_EXPORT_REQUIRED
from hashlib import sha256
from pathlib import Path
import json, re, sys

log, output = map(Path, sys.argv[1:3])
assert log.stat().st_size <= 8 * 1024 * 1024
data = log.read_bytes()
lines = data.splitlines()
names = ["fixture_contract", "landing_declared_full_build_synthetic_input", "landing_full_strict_typecheck_with_generated_routes"]
prefix = b"CUMULATIVE_LANDING_PHASE "
phases = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
for phase in phases:
    assert phase["name"] in names and type(phase["native_exit"]) in (int, type(None))
    assert phase["native_exit"] is None or 0 <= phase["native_exit"] <= 255
    print(f'INTEGRATED_NEXT_PHASE name={phase["name"]} native={phase["native_exit"]}')
if sys.argv[3] != "0":
    known = {b"EXACT_RUNTIME_REQUIRED", b"LOOPBACK_ONLY_REQUIRED", b"NATIVE_PHASE_FAILED", b"FIXTURE_CLOSE_REQUIRED",
             b"FIXTURE_CONTRACT_NOT_ACCEPTED", b"UNEXPECTED_GENERATED_TYPE_SOURCE_CHANGE",
             b"ACTUAL_SYNTHETIC_REQUESTS_REQUIRED", b"SYNTHETIC_EXPORT_AND_METADATA_REQUIRED"}
    for code in re.findall(rb"(?m)^CUMULATIVE_LANDING_FAILED ([A-Z_]+)$", data):
        if code in known:
            print("INTEGRATED_NEXT_FAILURE code=" + code.decode("ascii"))
    for token, code in ((b"Type error:", "TYPE_ERROR"), (b"Module not found", "MODULE_NOT_FOUND"),
                        (b"missing generateStaticParams", "STATIC_PARAMS_MISSING"), (b"prerendering page", "PRERENDER_FAILED"),
                        (b"useSearchParams() should be wrapped", "SEARCH_PARAMS_SUSPENSE_REQUIRED"), (b"ECONNREFUSED", "LOOPBACK_REFUSED")):
        if token in data:
            print("INTEGRATED_NEXT_DETAIL code=" + code)
    for name, line, col in sorted(set(re.findall(rb"(?m)^(?:\./)?(src/[A-Za-z0-9_./\[\]-]+\.(?:ts|tsx)):([0-9]{1,5}):([0-9]{1,5})", data)))[:12]:
        print(f"INTEGRATED_NEXT_DIAGNOSTIC {name.decode()}:{line.decode()}:{col.decode()}")
    raise SystemExit(1)
assert [phase["name"] for phase in phases] == names
assert all(phase["native_exit"] == 0 and phase["child_group_absent"] is True for phase in phases)
prefix = b"CUMULATIVE_LANDING_RESULT "
results = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
assert len(results) == 1
result = results[0]
assert result["status"] == "PASS" and result["fixture"] == "loopback-only" and result["fixture_closed"] is True
assert result["synthetic_listing_export_and_structured_metadata"] is True and result["output_must_not_be_deployed"] is True
counts = result["counts"]
assert counts["inventory"] >= 1 and counts["detail"] >= 1 and counts["refused"] == 0
assert counts["total"] == counts["inventory"] + counts["detail"] <= 128
for route in ("index.html", "login/index.html", "blog/index.html", "professionals/index.html",
              "search/index.html", "rentals/index.html", "local-guides/index.html", "local-guides/read/index.html",
              "local-guides/compare/index.html", "local-guides/market-report/index.html", "sitemap.xml"):
    path = output / route
    assert path.is_file() and not path.is_symlink()
    print(f"INTEGRATED_EXPORT_SHA256 route={route} sha256={sha256(path.read_bytes()).hexdigest()}")
print(f'INTEGRATED_NEXT_EXPORT native=0 typecheck=success static=success fixture_closed=true inventory={counts["inventory"]} detail={counts["detail"]}')
PY
verify_source || die STATIC_EXPORT_ALTERED_SOURCE
printf 'PLATFORM_FRONTEND_BUILD_ACCEPTANCE operations=%s frontend=%s frontend_tree=%s paths=%s test_files=56 run=%s attempt=1\n' \
  "$GITHUB_SHA" "$frontend_commit" "$frontend_tree" "$frontend_paths" "$GITHUB_RUN_ID"

[[ -z $(git -C "$frontend" status --porcelain=v1 --untracked-files=all) ]] || die SOURCE_CHECKOUT_CHANGED

#!/usr/bin/env bash
# Disposable hosted build. Only reviewed source is reconstructed; output stays in scratch.
set -euo pipefail
umask 077

die() { printf 'INTEGRATED_FRONTEND_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ $# == 3 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 &&
   ${GITHUB_RUN_ATTEMPT:-} == 1 && ${GITHUB_REF:-} == refs/heads/codex-ci-f01-f03-frontend-20261117 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die HOSTED_EXACT_EVENT_REQUIRED
ops="$1"; frontend="$2"; relay="$3"
[[ "$ops" == "$GITHUB_WORKSPACE/build-ops" && "$frontend" == "$GITHUB_WORKSPACE/frontend-base" &&
   "$relay" == "$GITHUB_WORKSPACE/landing-relay" ]] || die EXACT_CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$frontend" "$relay"; do
  current=$(git -C "$checkout" status --porcelain=v1 --untracked-files=all) || die CLEAN_CHECKOUT_REQUIRED
  [[ $(realpath "$checkout") == "$checkout" && -z "$current" && ! -e "$checkout/.env" && ! -L "$checkout/.env" ]] ||
    die CLEAN_CHECKOUT_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" rev-parse 'HEAD^') == c57b2836e331dc02a346a85786f3ab674a60a9ac ]] || die EXACT_OPERATIONS_REQUIRED

delta=$(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) || die EXACT_DELTA_REQUIRED
[[ "$delta" == $'.github/workflows/integrated-frontend-build.yml\nci/cumulative-landing/f01-f03-customer-step.commit\nci/cumulative-landing/f01-f03-customer-step.patch\nci/cumulative-landing/f01-f03-frontend.commit\nci/cumulative-landing/f01-f03-frontend.patch\nci/cumulative-landing/f01-f03-settings-mobile.commit\nci/cumulative-landing/f01-f03-settings-mobile.patch\nci/cumulative-landing/run-integrated-frontend.sh' ]] || die EXACT_DELTA_REQUIRED
frontend_base_commit=b5df6a9321365e827c01df556c09b8808151e59a
frontend_base_tree=27f08141cd78943e4c32fe4212d871e5153022ae
frontend_commit=977ca90b30c5cb1897eb186fec8a51c0711883f7
frontend_tree=2e0f96561a531eb883b88112cad8b4b809cb3c26
frontend_paths=391
[[ $(git -C "$frontend" rev-parse HEAD) == "$frontend_base_commit" &&
   $(git -C "$frontend" rev-parse 'HEAD^{tree}') == "$frontend_base_tree" ]] || die EXACT_FRONTEND_BASE_REQUIRED
printf '%s  %s\n' \
  373b27bcae0f101e91cea4656bbef179d42fa09d18d1c4462711d20cc1d98bbe "$ops/ci/cumulative-landing/f01-f03-frontend.patch" \
  479d36b81860692fdcac904860981a2621b460280731ce68318fec08f326b434 "$ops/ci/cumulative-landing/f01-f03-frontend.commit" \
  0d8a9c03e1ba1811bdc36621cb33bfb90a2debf6cbf2c646579599e9b69ec71a "$ops/ci/cumulative-landing/f01-f03-customer-step.patch" \
  26eff6da47bf78e2bc6108cfd57bb0249a0d555a1e2be78ec818215305cbe5fc "$ops/ci/cumulative-landing/f01-f03-customer-step.commit" \
  29386027b94f6c74a079a0fd53097968b6ca8979e544337beffd033c727fe54d "$ops/ci/cumulative-landing/f01-f03-settings-mobile.patch" \
  cd16b4204e04122b0e9da5c3b600fb7f841b7ab4a32c041fd5419243af4d06c3 "$ops/ci/cumulative-landing/f01-f03-settings-mobile.commit" \
  | sha256sum --check --status || die EXACT_RECONSTRUCTION_INPUTS_REQUIRED
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
label='org.ilovedrrealty.integrated.frontend-build'
run_key="$GITHUB_RUN_ID-$nonce"
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
source_repo="$state/frontend-objects"
git init --quiet --object-format=sha1 "$source_repo" || die SCRATCH_OBJECT_DATABASE_REQUIRED
printf '%s\n' "$frontend/.git/objects" >"$source_repo/.git/objects/info/alternates"
git -C "$source_repo" read-tree "$frontend_base_commit" || die READ_FRONTEND_BASE_REQUIRED
git -C "$source_repo" apply --cached --check --whitespace=error-all "$ops/ci/cumulative-landing/f01-f03-frontend.patch" || die REVIEWED_PATCH_CHECK_REQUIRED
git -C "$source_repo" apply --cached --whitespace=error-all "$ops/ci/cumulative-landing/f01-f03-frontend.patch" || die REVIEWED_PATCH_REQUIRED
[[ $(git -C "$source_repo" write-tree) == c3ed974dd9924968b90f284159d57a86d76f8b70 &&
   $(git -C "$source_repo" hash-object -t commit -w "$ops/ci/cumulative-landing/f01-f03-frontend.commit") == ebfdc8c93d3db51b58564420c6ee65502cf9b72a &&
   $(git -C "$source_repo" rev-parse 'ebfdc8c93d3db51b58564420c6ee65502cf9b72a^') == b5df6a9321365e827c01df556c09b8808151e59a ]] || die EXACT_RECONSTRUCTED_COMMIT_REQUIRED
git -C "$source_repo" apply --cached --check --whitespace=error-all "$ops/ci/cumulative-landing/f01-f03-customer-step.patch" || die REVIEWED_PATCH_CHECK_REQUIRED
git -C "$source_repo" apply --cached --whitespace=error-all "$ops/ci/cumulative-landing/f01-f03-customer-step.patch" || die REVIEWED_PATCH_REQUIRED
[[ $(git -C "$source_repo" write-tree) == 669c5b5e26c161e5146ea52921c9b2f7b9dfa3ff &&
   $(git -C "$source_repo" hash-object -t commit -w "$ops/ci/cumulative-landing/f01-f03-customer-step.commit") == b881b9f25eb50e5b61b262a9c3f64757841f4352 &&
   $(git -C "$source_repo" rev-parse 'b881b9f25eb50e5b61b262a9c3f64757841f4352^') == ebfdc8c93d3db51b58564420c6ee65502cf9b72a ]] || die EXACT_RECONSTRUCTED_COMMIT_REQUIRED
git -C "$source_repo" apply --cached --check --whitespace=error-all "$ops/ci/cumulative-landing/f01-f03-settings-mobile.patch" || die REVIEWED_PATCH_CHECK_REQUIRED
git -C "$source_repo" apply --cached --whitespace=error-all "$ops/ci/cumulative-landing/f01-f03-settings-mobile.patch" || die REVIEWED_PATCH_REQUIRED
[[ $(git -C "$source_repo" write-tree) == 2e0f96561a531eb883b88112cad8b4b809cb3c26 &&
   $(git -C "$source_repo" hash-object -t commit -w "$ops/ci/cumulative-landing/f01-f03-settings-mobile.commit") == 977ca90b30c5cb1897eb186fec8a51c0711883f7 &&
   $(git -C "$source_repo" rev-parse '977ca90b30c5cb1897eb186fec8a51c0711883f7^') == b881b9f25eb50e5b61b262a9c3f64757841f4352 ]] || die EXACT_RECONSTRUCTED_COMMIT_REQUIRED
git -C "$source_repo" diff --quiet "$frontend_base_commit" "$frontend_commit" -- \
  apps/dashboard/src/components/admin/LeadDetailPanel.tsx \
  apps/dashboard/src/components/shared/ShareContactDialog.tsx apps/dashboard/src/lib/contactShare.ts \
  apps/dashboard/src/locale/en/contact_share.json apps/dashboard/src/locale/es/contact_share.json \
  apps/dashboard/src/api/tradeContacts.ts apps/dashboard/src/pages/admin/TradeContacts.tsx || die LIVE_SHARE_AND_TRADE_INPUTS_CHANGED
printf 'INTEGRATED_FRONTEND_RECONSTRUCTION commit=%s tree=%s live_parent=%s paths=%s\n' \
  "$frontend_commit" "$frontend_tree" "$frontend_base_commit" "$frontend_paths"
git -C "$source_repo" archive --format=tar "$frontend_commit" | tar -xf - -C "$state/frontend-work" || die ARCHIVE_REQUIRED
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
ddcbb0e45b77854a30f8f22a767be96e5555ca7800652d452fe35b25792281f3  package-lock.json
4f4587eb432013b20c771d3a045fb04b67d146794c47c007e805299d2c562a43  apps/dashboard/package.json
d32f9a37792098bcad7deae4501e9a407747b8d6dcd95c97302605b0f4404dcb  apps/landing/package.json
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

tests=(tests/lead-delivery.test.cjs tests/communications.test.cjs tests/property-inquiry.test.cjs
       tests/inquiry-feeds.test.cjs tests/auth-locale.test.cjs tests/google-auth-fallback.test.cjs
       tests/contact-share.test.cjs tests/lead-tracking.test.cjs)
native=0
bounded 300s docker run "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1536 \
  node --test --test-concurrency=1 --test-reporter=tap "${tests[@]}" >"$state/contracts.log" 2>&1 || native=$?
python3 -I -B - "$state/contracts.log" "$native" <<'PY' || die EXACT_CONTRACTS_REQUIRED
from pathlib import Path
import re, sys

source = Path(sys.argv[1])
assert source.stat().st_size <= 524288
data = source.read_bytes()
rows = {name: int(value) for name, value in re.findall(rb"(?m)^# (tests|pass|fail|skipped) ([0-9]{1,3})$", data)}
print("INTEGRATED_FRONTEND_CONTRACTS native=" + str(int(sys.argv[2])) + " " +
      " ".join(f"{key.decode()}={rows.get(key, 0)}" for key in (b"tests", b"pass", b"fail", b"skipped")))
for index in re.findall(rb"(?m)^not ok ([1-9][0-9]{0,2}) - ", data)[:12]:
    print("INTEGRATED_CONTRACT_FAILURE index=" + index.decode("ascii"))
assert sys.argv[2] == "0" and rows == {b"tests": 69, b"pass": 69, b"fail": 0, b"skipped": 0}
PY
verify_source || die CONTRACTS_ALTERED_SOURCE

native=0
bounded 360s docker run "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1536 \
  npm_config_userconfig=/tmp/ildr-user.npmrc npm_config_globalconfig=/tmp/ildr-global.npmrc \
  npm_config_cache=/tmp/npm npm_config_audit=false npm_config_fund=false npm_config_update_notifier=false \
  VITE_API_URL=http://127.0.0.1:8917 VITE_LANDING_URL=http://127.0.0.1:8918 \
  VITE_GOOGLE_CLIENT_ID=synthetic-ci.apps.googleusercontent.com \
  bash --noprofile --norc -euo pipefail -c 'node /usr/local/lib/node_modules/npm/bin/npm-cli.js run typecheck --workspace=dashboard && node /usr/local/lib/node_modules/npm/bin/npm-cli.js run build --workspace=dashboard' \
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
assert b"> tsc --noEmit" in data and b"> vite build" in data and index.is_file() and not index.is_symlink()
assert b"127.0.0.1:8917" in b"".join(f.read_bytes() for f in index.parent.glob("assets/*.js") if f.is_file())
print("INTEGRATED_DASHBOARD native=0 typecheck=success build=success")
PY
verify_source || die DASHBOARD_ALTERED_SOURCE

cp "$landing_script" "$state/landing-run/landing.cjs"
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
              "search/index.html", "sitemap.xml"):
    path = output / route
    assert path.is_file() and not path.is_symlink()
    print(f"INTEGRATED_EXPORT_SHA256 route={route} sha256={sha256(path.read_bytes()).hexdigest()}")
print(f'INTEGRATED_NEXT_EXPORT native=0 typecheck=success static=success fixture_closed=true inventory={counts["inventory"]} detail={counts["detail"]}')
PY
verify_source || die STATIC_EXPORT_ALTERED_SOURCE
printf 'INTEGRATED_FRONTEND_ACCEPTANCE operations=%s frontend=%s frontend_tree=%s paths=%s tests=69 run=%s attempt=1\n' \
  "$GITHUB_SHA" "$frontend_commit" "$frontend_tree" "$frontend_paths" "$GITHUB_RUN_ID"

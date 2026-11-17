#!/usr/bin/env bash
# Disposable hosted build. Generated applications and fixture never leave scratch.
set -euo pipefail
umask 077

die() { printf 'INTEGRATED_FRONTEND_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ $# == 4 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 &&
   ${GITHUB_RUN_ATTEMPT:-} == 1 && ${GITHUB_REF:-} == refs/heads/codex-ci-f09-deletion-frontend-20261117 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die HOSTED_EXACT_EVENT_REQUIRED
ops="$1"; frontend="$2"; live="$3"; relay="$4"
[[ "$ops" == "$GITHUB_WORKSPACE/build-ops" && "$frontend" == "$GITHUB_WORKSPACE/frontend-base" &&
   "$live" == "$GITHUB_WORKSPACE/frontend-live" &&
   "$relay" == "$GITHUB_WORKSPACE/landing-relay" ]] || die EXACT_CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$frontend" "$live" "$relay"; do
  current=$(git -C "$checkout" status --porcelain=v1 --untracked-files=all) || die CLEAN_CHECKOUT_REQUIRED
  [[ $(realpath "$checkout") == "$checkout" && -z "$current" && ! -e "$checkout/.env" && ! -L "$checkout/.env" ]] ||
    die CLEAN_CHECKOUT_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" rev-parse 'HEAD^') == dc6f985c4679145e815363a0cd915387c473fab9 ]] || die EXACT_OPERATIONS_REQUIRED
delta=$(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) || die EXACT_DELTA_REQUIRED
[[ "$delta" == $'.github/workflows/integrated-frontend-build.yml\nci/cumulative-landing/account-deletion-frontend.commit\nci/cumulative-landing/account-deletion-frontend.patch\nci/cumulative-landing/auth-fallback-fixture.commit\nci/cumulative-landing/auth-fallback-fixture.patch\nci/cumulative-landing/run-integrated-frontend.sh' ]] ||
  die EXACT_DELTA_REQUIRED
frontend_base_commit=99bc8d4c0ee4f3f0b72cf174530971f6a35f0ab3
frontend_base_tree=4053ae36068790f5f4562ad256dd8a0018fac6ae
frontend_live_commit=b5df6a9321365e827c01df556c09b8808151e59a
frontend_live_tree=27f08141cd78943e4c32fe4212d871e5153022ae
frontend_combined_commit=12f5b00fc5aa4040cb91c8ecc483812999c0e33e
frontend_combined_tree=9f1817d1f744d3c57c365f18435c79051219f0df
frontend_newsletter_commit=5994fbe6d1c09b3f74000ca20261778f0c46887e
frontend_newsletter_tree=d7d803a5b53f70e077ea4995ac1f1ba288a8d7da
frontend_token_commit=4d0a99e0bef524fe00eab5f2c0916d91768a4efb
frontend_token_tree=ca59517c57b8e06ffe5fd2840340ee739e29937b
frontend_auth_fixture_commit=90b238ea2e81e2ec279cbc18546125478bffceb1
frontend_auth_fixture_tree=0da98d76419ff0501b87b112d975037496b9583f
frontend_commit=69d64e6bb7ba4d748267d22b7e215bd53089f325
frontend_tree=2570e8ccc497bd03fbee402897d67cbd9ad9a294
frontend_paths=575
[[ $(git -C "$frontend" rev-parse HEAD) == "$frontend_base_commit" &&
   $(git -C "$frontend" rev-parse 'HEAD^{tree}') == "$frontend_base_tree" &&
   $(git -C "$live" rev-parse HEAD) == "$frontend_live_commit" &&
   $(git -C "$live" rev-parse 'HEAD^{tree}') == "$frontend_live_tree" ]] || die EXACT_FRONTEND_PARENTS_REQUIRED
frontend_patch="$ops/ci/cumulative-landing/integrated-trade-frontend.patch"
frontend_payload="$ops/ci/cumulative-landing/integrated-trade-frontend.commit"
newsletter_patch="$ops/ci/cumulative-landing/newsletter-frontend.patch"
newsletter_payload="$ops/ci/cumulative-landing/newsletter-frontend.commit"
token_patch="$ops/ci/cumulative-landing/newsletter-token-format.patch"
token_payload="$ops/ci/cumulative-landing/newsletter-token-format.commit"
auth_fixture_patch="$ops/ci/cumulative-landing/auth-fallback-fixture.patch"
auth_fixture_payload="$ops/ci/cumulative-landing/auth-fallback-fixture.commit"
deletion_patch="$ops/ci/cumulative-landing/account-deletion-frontend.patch"
deletion_payload="$ops/ci/cumulative-landing/account-deletion-frontend.commit"
printf '%s  %s\n' \
  bab3d68fa5740ef914deb0b3d623feb7a9641aa160f0c502daf1078f23fcf7ae "$frontend_patch" \
  9f955c1533cbe0fde9965c7c6122f089b0bdfbd546ed2cb53557fd86d3fe1636 "$frontend_payload" \
  df8f19de80b143d3176409d05f5402964fe7e710e5686accc525dfe530f19e6c "$newsletter_patch" \
  a2d1178b3cd61d0e00a6172395cd9330ddac7decc2a3a46016d5666a13bfb040 "$newsletter_payload" \
  ef4f871d8719f7e2a45b7ca1ea4eb4065e52eb7b71b89ea5a75e6345827a5009 "$token_patch" \
  154685af21a73f4885ae20d266a4e67397f8124b58e9367cf8ede1327afa5404 "$token_payload" \
  5b723caae48f8867102646f262a6540a116ef89dca4ed8a81a0298df1ee33c20 "$auth_fixture_patch" \
  59ea851b47d3f6f012cd91109246c6a8acc65fd2bfc89dd03c71d6370da3f71a "$auth_fixture_payload" \
  677f123321b22aece9f7fd5b396c3d4c76457723d588190ed5c5f6b911d093b8 "$deletion_patch" \
  8bde73aa77efe883779189a40540272660b778478b07681e0704e45141132c6f "$deletion_payload" \
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
printf '%s\n' "$frontend/.git/objects" "$live/.git/objects" >"$source_repo/.git/objects/info/alternates"
git -C "$source_repo" read-tree "$frontend_base_commit" || die READ_FRONTEND_BASE_REQUIRED
git -C "$source_repo" apply --cached --check --whitespace=error-all "$frontend_patch" ||
  die REVIEWED_PATCH_CHECK_REQUIRED
git -C "$source_repo" apply --cached --whitespace=error-all "$frontend_patch" || die REVIEWED_PATCH_REQUIRED
[[ $(git -C "$source_repo" write-tree) == "$frontend_combined_tree" ]] || die EXACT_RECONSTRUCTED_TREE_REQUIRED
[[ $(git -C "$source_repo" hash-object -t commit -w "$frontend_payload") == "$frontend_combined_commit" &&
   $(git -C "$source_repo" rev-parse "$frontend_combined_commit^{tree}") == "$frontend_combined_tree" &&
   $(git -C "$source_repo" rev-parse "$frontend_combined_commit^1") == "$frontend_live_commit" &&
   $(git -C "$source_repo" rev-parse "$frontend_combined_commit^2") == "$frontend_base_commit" ]] ||
  die EXACT_RECONSTRUCTED_MERGE_REQUIRED
frontend_delta=$(git -C "$source_repo" diff-tree --no-commit-id --name-only -r \
  "$frontend_base_commit" "$frontend_combined_commit") || die EXACT_FRONTEND_DELTA_REQUIRED
[[ "$frontend_delta" == $'apps/dashboard/src/api/tradeContacts.ts\napps/dashboard/src/layouts/DashLayout.tsx\napps/dashboard/src/locale/en/admin.json\napps/dashboard/src/locale/en/nav.json\napps/dashboard/src/locale/es/admin.json\napps/dashboard/src/locale/es/nav.json\napps/dashboard/src/pages/AdminDash.tsx\napps/dashboard/src/pages/admin/TradeContacts.tsx' ]] ||
  die EXACT_FRONTEND_DELTA_REQUIRED
git -C "$source_repo" diff --quiet "$frontend_live_commit" "$frontend_combined_commit" -- \
  apps/dashboard/src/api/tradeContacts.ts apps/dashboard/src/pages/admin/TradeContacts.tsx ||
  die EXACT_LIVE_TRADE_COMPONENTS_REQUIRED
printf 'INTEGRATED_FRONTEND_RECONSTRUCTION commit=%s tree=%s live_parent=%s integrated_parent=%s delta=8\n' \
  "$frontend_combined_commit" "$frontend_combined_tree" "$frontend_live_commit" "$frontend_base_commit"
git -C "$source_repo" apply --cached --check --unidiff-zero --whitespace=error-all "$newsletter_patch" ||
  die REVIEWED_NEWSLETTER_PATCH_CHECK_REQUIRED
git -C "$source_repo" apply --cached --unidiff-zero --whitespace=error-all "$newsletter_patch" ||
  die REVIEWED_NEWSLETTER_PATCH_REQUIRED
[[ $(git -C "$source_repo" write-tree) == "$frontend_newsletter_tree" &&
   $(git -C "$source_repo" hash-object -t commit -w "$newsletter_payload") == "$frontend_newsletter_commit" &&
   $(git -C "$source_repo" rev-parse "$frontend_newsletter_commit^") == "$frontend_combined_commit" ]] ||
  die EXACT_RECONSTRUCTED_NEWSLETTER_REQUIRED
newsletter_delta=$(git -C "$source_repo" diff-tree --no-commit-id --name-only -r \
  "$frontend_combined_commit" "$frontend_newsletter_commit") || die EXACT_NEWSLETTER_SCOPE_REQUIRED
[[ "$newsletter_delta" == $'apps/landing/src/api/newsletter.ts\napps/landing/src/app/layout.tsx\napps/landing/src/app/newsletter/page.tsx\napps/landing/src/app/privacy-policy/page.tsx\napps/landing/src/components/LandingRouteShell.tsx\napps/landing/src/components/NewsletterSignup.tsx\napps/landing/src/locales/en/blog.json\napps/landing/src/locales/es/blog.json\napps/landing/src/views/Blog.tsx\napps/landing/src/views/NewsletterPreferences.tsx\ntests/blog-articles.test.cjs\ntests/lead-tracking.test.cjs\ntests/newsletter.test.cjs' ]] ||
  die EXACT_NEWSLETTER_SCOPE_REQUIRED
git -C "$source_repo" apply --cached --check --unidiff-zero --whitespace=error-all "$token_patch" ||
  die REVIEWED_TOKEN_FORMAT_CHECK_REQUIRED
git -C "$source_repo" apply --cached --unidiff-zero --whitespace=error-all "$token_patch" ||
  die REVIEWED_TOKEN_FORMAT_REQUIRED
[[ $(git -C "$source_repo" write-tree) == "$frontend_token_tree" &&
   $(git -C "$source_repo" hash-object -t commit -w "$token_payload") == "$frontend_token_commit" &&
   $(git -C "$source_repo" rev-parse "$frontend_token_commit^") == "$frontend_newsletter_commit" ]] ||
  die EXACT_TOKEN_FORMAT_COMMIT_REQUIRED
[[ $(git -C "$source_repo" diff-tree --no-commit-id --name-only -r "$frontend_newsletter_commit" "$frontend_token_commit") == \
   $'apps/landing/src/api/newsletter.ts\ntests/newsletter.test.cjs' ]] || die EXACT_TOKEN_FORMAT_SCOPE_REQUIRED
git -C "$source_repo" diff --quiet "$frontend_combined_commit" "$frontend_token_commit" -- apps/dashboard ||
  die DASHBOARD_SCOPE_CHANGED
printf 'NEWSLETTER_FRONTEND_RECONSTRUCTION commit=%s tree=%s parent=%s delta=13\n' \
  "$frontend_token_commit" "$frontend_token_tree" "$frontend_combined_commit"
git -C "$source_repo" apply --cached --check --unidiff-zero --whitespace=error-all "$auth_fixture_patch" ||
  die REVIEWED_AUTH_FIXTURE_CHECK_REQUIRED
git -C "$source_repo" apply --cached --unidiff-zero --whitespace=error-all "$auth_fixture_patch" ||
  die REVIEWED_AUTH_FIXTURE_REQUIRED
[[ $(git -C "$source_repo" write-tree) == "$frontend_auth_fixture_tree" &&
   $(git -C "$source_repo" hash-object -t commit -w "$auth_fixture_payload") == "$frontend_auth_fixture_commit" &&
   $(git -C "$source_repo" rev-parse "$frontend_auth_fixture_commit^") == "$frontend_token_commit" ]] ||
  die EXACT_AUTH_FIXTURE_COMMIT_REQUIRED
[[ $(git -C "$source_repo" diff-tree --no-commit-id --name-only -r "$frontend_token_commit" "$frontend_auth_fixture_commit") == \
   tests/google-auth-fallback.test.cjs ]] || die EXACT_AUTH_FIXTURE_SCOPE_REQUIRED
printf 'AUTH_FALLBACK_FIXTURE_RECONSTRUCTION commit=%s tree=%s parent=%s delta=1\n' \
  "$frontend_auth_fixture_commit" "$frontend_auth_fixture_tree" "$frontend_token_commit"

git -C "$source_repo" apply --cached --check --unidiff-zero --whitespace=error-all "$deletion_patch" ||
  die REVIEWED_ACCOUNT_DELETION_PATCH_CHECK_REQUIRED
git -C "$source_repo" apply --cached --unidiff-zero --whitespace=error-all "$deletion_patch" ||
  die REVIEWED_ACCOUNT_DELETION_PATCH_REQUIRED
[[ $(git -C "$source_repo" write-tree) == "$frontend_tree" &&
   $(git -C "$source_repo" hash-object -t commit -w "$deletion_payload") == "$frontend_commit" &&
   $(git -C "$source_repo" rev-parse "$frontend_commit^") == "$frontend_auth_fixture_commit" ]] ||
  die EXACT_ACCOUNT_DELETION_COMMIT_REQUIRED
deletion_delta=$(git -C "$source_repo" diff-tree --no-commit-id --name-only -r \
  "$frontend_auth_fixture_commit" "$frontend_commit") || die EXACT_ACCOUNT_DELETION_SCOPE_REQUIRED
[[ "$deletion_delta" == $'apps/dashboard/src/api/accountDeletion.ts\napps/dashboard/src/api/auth.ts\napps/dashboard/src/components/AccountDeletionRequest.tsx\napps/dashboard/src/i18n.ts\napps/dashboard/src/layouts/DashLayout.tsx\napps/dashboard/src/locale/en/account_deletion.json\napps/dashboard/src/locale/en/nav.json\napps/dashboard/src/locale/en/settings.json\napps/dashboard/src/locale/es/account_deletion.json\napps/dashboard/src/locale/es/nav.json\napps/dashboard/src/locale/es/settings.json\napps/dashboard/src/pages/AdminDash.tsx\napps/dashboard/src/pages/Settings.tsx\napps/dashboard/src/pages/admin/AccountDeletion.tsx\napps/dashboard/src/pages/admin/Settings.tsx\ntests/account-deletion.test.cjs' ]] || die EXACT_ACCOUNT_DELETION_SCOPE_REQUIRED
git -C "$source_repo" diff --quiet "$frontend_token_commit" "$frontend_commit" -- apps/landing ||
  die NEWSLETTER_PRODUCT_SOURCE_CHANGED
printf 'ACCOUNT_DELETION_FRONTEND_RECONSTRUCTION commit=%s tree=%s parent=%s delta=16\n' \
  "$frontend_commit" "$frontend_tree" "$frontend_auth_fixture_commit"

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
8df3c4c580c65694bc727488fc0ec02d88ab1577e5fa16c509f4897b58e60f33  package-lock.json
1126aeb69e32b34394cca03ac440848f7b88b339042496cb6722a1781a204337  apps/dashboard/package.json
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

tests=(tests/blog-articles.test.cjs tests/editorial-admin.test.cjs tests/listing-brochure.test.cjs
       tests/listing-export.test.cjs tests/listing-floor-plans.test.cjs tests/listing-room-tour.test.cjs
       tests/local-market-report.test.cjs tests/location-review.test.cjs tests/management-navigation.test.cjs
       tests/professionals-admin-entry.test.cjs tests/property-viewings.test.cjs
       tests/public-editorial.test.cjs tests/purchase-cost-worksheet.test.cjs tests/shared-shortlists.test.cjs
       tests/transaction-documents.test.cjs tests/transaction-rooms.test.cjs
       tests/newsletter.test.cjs tests/lead-tracking.test.cjs
       tests/google-auth-fallback.test.cjs tests/account-deletion.test.cjs)
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
assert sys.argv[2] == "0" and rows == {b"tests": 146, b"pass": 146, b"fail": 0, b"skipped": 0}
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
  node /usr/local/lib/node_modules/npm/bin/npm-cli.js run build --workspace=dashboard \
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
assert b"tsc --noEmit && vite build" in data and index.is_file() and not index.is_symlink()
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
              "search/index.html", "local-guides/index.html", "local-guides/read/index.html",
              "local-guides/compare/index.html", "local-guides/market-report/index.html", "newsletter/index.html", "sitemap.xml"):
    path = output / route
    assert path.is_file() and not path.is_symlink()
    print(f"INTEGRATED_EXPORT_SHA256 route={route} sha256={sha256(path.read_bytes()).hexdigest()}")
from html.parser import HTMLParser
from urllib.parse import urlsplit

class NewsletterHtml(HTMLParser):
    def __init__(self):
        super().__init__()
        self.metadata = {}
        self.external_scripts = []
        self.iframes = 0

    def handle_starttag(self, tag, attrs):
        value = dict(attrs)
        if tag == "meta" and "name" in value:
            self.metadata[value["name"].lower()] = value.get("content", "")
        if tag == "script" and "src" in value:
            target = urlsplit(value["src"])
            if target.netloc or target.scheme:
                self.external_scripts.append(target.netloc)
        if tag == "iframe":
            self.iframes += 1

newsletter_html = (output / "newsletter/index.html").read_text(encoding="utf-8")
page = NewsletterHtml()
page.feed(newsletter_html)
assert page.metadata.get("referrer") == "no-referrer"
assert "noindex" in page.metadata.get("robots", "") and "nofollow" in page.metadata.get("robots", "")
assert not page.external_scripts and not page.iframes
assert "Newsletter preferences" in newsletter_html
assert not any(host in newsletter_html for host in ("code.tidio.co", "googletagmanager.com", "accounts.google.com"))
assert "/newsletter/" not in (output / "sitemap.xml").read_text(encoding="utf-8")
print("NEWSLETTER_STATIC_EXPORT page=verified robots=noindex,nofollow referrer=no-referrer external_integrations=absent")
print(f'INTEGRATED_NEXT_EXPORT native=0 typecheck=success static=success fixture_closed=true inventory={counts["inventory"]} detail={counts["detail"]}')
PY
verify_source || die STATIC_EXPORT_ALTERED_SOURCE
printf 'INTEGRATED_FRONTEND_ACCEPTANCE operations=%s frontend=%s frontend_tree=%s paths=%s tests=146 run=%s attempt=1\n' \
  "$GITHUB_SHA" "$frontend_commit" "$frontend_tree" "$frontend_paths" "$GITHUB_RUN_ID"

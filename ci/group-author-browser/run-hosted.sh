#!/usr/bin/env bash
# Exact sources; one immutable build; fresh owned DB and private fixture per phase.
set -euo pipefail
umask 077
die() { printf 'GROUP_AUTHOR_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }
[[ $# == 8 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 &&
   ${GITHUB_RUN_ATTEMPT:-} == 1 && ${GITHUB_REF:-} == refs/heads/codex-ci-group-author-browser-retained-v2-20261126 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die HOSTED_EXACT_EVENT_REQUIRED
ops="$1"; frontend="$2"; baseline="$3"; backend="$4"; migration="$5"; relay="$6"; previous="$7"; reference="$8"
[[ "$ops" == "$GITHUB_WORKSPACE/build-ops" && "$frontend" == "$GITHUB_WORKSPACE/frontend-source" &&
   "$baseline" == "$GITHUB_WORKSPACE/current-platform" && "$backend" == "$GITHUB_WORKSPACE/browser-backend" &&
   "$migration" == "$GITHUB_WORKSPACE/migration-relay" &&
   "$relay" == "$GITHUB_WORKSPACE/browser-relay" && "$previous" == "$GITHUB_WORKSPACE/browser-previous" && "$reference" == "$GITHUB_WORKSPACE/profile-baseline106" ]] || die EXACT_CHECKOUT_PATHS_REQUIRED
for checkout in "$ops" "$frontend" "$baseline" "$backend" "$migration" "$relay" "$previous" "$reference"; do
  [[ $(realpath "$checkout") == "$checkout" && -z $(git -C "$checkout" status --porcelain=v1 --untracked-files=all) &&
     ! -e "$checkout/.env" && ! -L "$checkout/.env" ]] || die CLEAN_CHECKOUT_REQUIRED
done
expected_operations=$(cat <<'EXACT_OPERATIONS'
.github/workflows/group-author-browser.yml
ci/group-author-browser/PLAN.json
ci/group-author-browser/run-hosted.sh
EXACT_OPERATIONS
)
operations_delta=$(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) || die OPERATIONS_DIFF_REQUIRED
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" show -s --format=%P HEAD) == a4e0e57db3e4e72f9c92642115977530e2fde85f &&
   $(git -C "$ops" rev-parse HEAD^^) == 9051cbf266932b0157690a2d57df16c0dbb23fa6 &&
   "$operations_delta" == "$expected_operations" ]] || die EXACT_OPERATIONS_REQUIRED
python3 -I -B - "$ops" <<'PY_OPERATIONS_REUSE'
from pathlib import Path
import subprocess,sys
root=Path(sys.argv[1])
def git(*args):return subprocess.check_output(['git','-C',str(root),*args])
def tree(commit):return dict((line.split(b'\t',1)[1].decode(),line.split(b'\t',1)[0]) for line in git('ls-tree','-rz',commit).split(b'\0') if line)
old,middle,new=tree('HEAD^^'),tree('HEAD^'),tree('HEAD')
paths={'.github/workflows/group-author-browser.yml','ci/group-author-browser/PLAN.json','ci/group-author-browser/check-result.py','ci/group-author-browser/run-browser.mjs','ci/group-author-browser/run-hosted.sh','ci/group-author-browser/serve_fixture.py'}
changed={'.github/workflows/group-author-browser.yml','ci/group-author-browser/PLAN.json','ci/group-author-browser/run-hosted.sh'}
assert len(old)==415 and len(middle)==len(new)==421 and set(middle)-set(old)==paths and not set(old)-set(middle)
assert set(middle)==set(new) and {name for name,value in middle.items() if new[name]!=value}==changed
assert all(new[name]==middle[name]==value for name,value in old.items())
pins={'ci/group-author-browser/run-browser.mjs':'9f5f4902e70349a51fdde0dec8b49862a47be297dbdf601043d22a36af222fef','ci/group-author-browser/serve_fixture.py':'df83d51fb80ca1a6f4c03bc34a0f25b401d33419d1a3c29935ef8d0c470ef6ad','ci/group-author-browser/check-result.py':'e42b2f3a8e36dd801f47adb5e2ed6a81522da77fb670a6c6185f2d71355e9be4'}
from hashlib import sha256
for name,digest in pins.items():assert sha256((root/name).read_bytes()).hexdigest()==digest
PY_OPERATIONS_REUSE
backend_commit=6ef69b249c573eedaea962dad530eaed0a612b24
backend_tree=f1ef617e51e0d8a900d9f4bfc15a559bf676276f
frontend_commit=a8069c847a75823fc5a62101895a80d8fc1f8b1d
frontend_tree=3fbc3dc23f6ca5fec9515655c26e7d122fcd2679
frontend_paths=620
[[ $(git -C "$backend" rev-parse HEAD) == "$backend_commit" && $(git -C "$backend" rev-parse HEAD^{tree}) == "$backend_tree" &&
   $(git -C "$frontend" rev-parse HEAD) == "$frontend_commit" && $(git -C "$frontend" rev-parse HEAD^{tree}) == "$frontend_tree" &&
   $(git -C "$frontend" show -s --format=%P HEAD) == "ee32af9f5c0db6f79412c7c089e7f9f7143f42d3 0bc3fc968199490344c79c9cbed44afb0875eb18" &&
   $(git -C "$frontend" show -s --format=%P 5ecb7c6e16268f1e8edd1b54d70f11e605e57a02) == "bb4a9c626fb96f93ae6d6bc55b5a2a67f85411ba 44a3fdae157039f42fad562bde281f7205ca19f3" &&
   $(git -C "$baseline" rev-parse HEAD) == 01695530df670a94ac28f8b3bf36cc34b5b7a942 &&
   $(git -C "$baseline" rev-parse HEAD^{tree}) == e88ea0c57f9fac39549322532a5992d5a2927676 &&
   $(git -C "$migration" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 &&
   $(git -C "$relay" rev-parse HEAD) == 3007728fd67c5889a8373e41ff3ffa5cc50d839b &&
   $(git -C "$relay" rev-parse HEAD^{tree}) == 80868dce147c2cd4609e303493669d0a19f09741 &&
   $(git -C "$previous" rev-parse HEAD) == 8957b18520d15dac503e0e4a5ce4b2455135d601 &&
   $(git -C "$previous" rev-parse HEAD^{tree}) == 11ab69db36eb30881e4f3086578201f584c4092d &&
   $(git -C "$reference" rev-parse HEAD) == 17943ba1714673c51c43cdbb800106f91612f527 && $(git -C "$reference" rev-parse HEAD^{tree}) == ff940a7b6e983d4c669202746d9676b4b82e9a06 ]] || die EXACT_SOURCES_REQUIRED
[[ $(git -C "$backend" show -s --format=%P HEAD) == a75f9c26f3bceb14afcb6e1150e1b2a3d50fbada &&
   $(git -C "$frontend" rev-parse '06d7f74965896ae23d47e7701fc1dbfd9815cc40^{commit}') == 06d7f74965896ae23d47e7701fc1dbfd9815cc40 ]] || die EXACT_PARENTS_AND_REVIEW_BASE_REQUIRED
git -C "$frontend" merge-base --is-ancestor 06d7f74965896ae23d47e7701fc1dbfd9815cc40 HEAD || die REVIEW_BASE_ANCESTRY_REQUIRED
git -C "$backend" cat-file -e '8957b18520d15dac503e0e4a5ce4b2455135d601^{commit}' || die BACKEND_BASE_REQUIRED
git -C "$backend" merge-base --is-ancestor 8957b18520d15dac503e0e4a5ce4b2455135d601 HEAD || die BACKEND_BASE_ANCESTRY_REQUIRED
backend_dependency_delta=$(git -C "$backend" diff --name-only 8957b18520d15dac503e0e4a5ce4b2455135d601 HEAD -- requirements.txt) || die BACKEND_DEPENDENCY_DIFF_REQUIRED
frontend_manifest_delta=$(git -C "$frontend" diff --name-only 06d7f74965896ae23d47e7701fc1dbfd9815cc40 HEAD -- package.json package-lock.json apps/dashboard/package.json apps/landing/package.json) || die FRONTEND_MANIFEST_DIFF_REQUIRED
[[ -z "$backend_dependency_delta" && -z "$frontend_manifest_delta" ]] || die UNCHANGED_DEPENDENCIES_AND_MANIFESTS_REQUIRED
python3 -I -B - "$backend" <<'PY_BACKEND_COMPOSITION'
from pathlib import Path
import subprocess,sys
root=Path(sys.argv[1])
def git(*args):return subprocess.check_output(['git','-C',str(root),*args])
def tree(commit):return dict((line.split(b'\t',1)[1].decode(),line.split(b'\t',1)[0]) for line in git('ls-tree','-rz',commit).split(b'\0') if line)
base=tree('a75f9c26f3bceb14afcb6e1150e1b2a3d50fbada');current=tree('HEAD')
paths={'routes/social_groups.py','services/social_groups.py','tests/test_social_groups.py'}
assert set(git('diff','--name-only','a75f9c26f3bceb14afcb6e1150e1b2a3d50fbada','HEAD').decode().splitlines())==paths
assert len(base)==len(current)==367 and all(current[name]==value for name,value in base.items() if name not in paths)
PY_BACKEND_COMPOSITION
python3 -I -B - "$frontend" <<'PY_FRONTEND_COMPOSITION'
from pathlib import Path
from hashlib import sha256
import subprocess,sys
root=Path(sys.argv[1])
def git(*args):return subprocess.check_output(['git','-C',str(root),*args])
def tree(commit):return dict((line.split(b'\t',1)[1].decode(),line.split(b'\t',1)[0]) for line in git('ls-tree','-rz',commit).split(b'\0') if line)
base=tree('5ecb7c6e16268f1e8edd1b54d70f11e605e57a02');current=tree('HEAD')
pins={'apps/dashboard/src/api/socialGroups.ts': '1f2c4513f5a10009366b3bda6d3f012790c5b688f8b2260f0e02529cb1957d01', 'apps/dashboard/src/components/network/GroupFeed.tsx': '31d06471196994fe11c2fc7f6299b2f8825be228da6d8635429e441415ce6d10', 'apps/dashboard/src/components/network/GroupWorkspace.tsx': '3020da0bbc78a35e920e3625bbf9e41adc0f0434deaf97cb048212439e2a235c', 'apps/dashboard/src/components/network/MemberCommunity.tsx': '42827187beaa5019539a6a2426bf4abbb9b84971350ebb53d345b283ac7dd77c', 'apps/dashboard/src/locale/en/property_network.json': '21295196c6cc4529c92975768c21ba7c75324c70c7b68aaf6d969b4d0cad64e1', 'apps/dashboard/src/locale/es/property_network.json': '0b6792d51c989a58f215a107a0e0a819d6b0a9ee6cf814c25368c9b846a3ea50', 'tests/property-network-ui.test.cjs': 'c198eacca873af98873775c768cb609b43471dc65c358480f0d85a3441db38e3', 'tests/realtor-sale-catalog.test.cjs': 'b2a45690dd716d6ba0bb6b1126ca4e20ff32a48a73938d52bbf4db262d3d33d7', 'tests/service-operations.test.cjs': '4a42d9a0a5d84b2d59c0802d7891b72e2c71b58d76d45fa5864cbad41cd1aa0f', 'tests/social-groups.test.cjs': '2dd092bccf9c50cdf09ae5a5b9d6dfe1f7b6d6ceb50cdb3177b25cc5b1d7a803'}
assert set(git('diff','--name-only','5ecb7c6e16268f1e8edd1b54d70f11e605e57a02','HEAD').decode().splitlines())==set(pins)
assert len(base)==len(current)==620 and sum(current[name]==value for name,value in base.items() if name not in pins)==610
for name,digest in pins.items():assert sha256(git('show','HEAD:'+name)).hexdigest()==digest
assert len([name for name in current if name.startswith('tests/') and name.endswith('.test.cjs')])==65
PY_FRONTEND_COMPOSITION
printf 'GROUP_AUTHOR_BUILD_SCOPE frontend_source=a8069c847a75823fc5a62101895a80d8fc1f8b1d backend_source=6ef69b249c573eedaea962dad530eaed0a612b24 expected_full_tests=549 retained_tests_run=45902156502 retained_strict_run=45902156502 current_tests=false current_strict=false native162=retained_not_rerun profile24_32=separate_gate_not_rerun\n'
python3 -I -B - "$baseline/requirements.txt" "$backend/requirements.txt" <<'PY_REQUIREMENTS'
from pathlib import Path
import sys
assert Path(sys.argv[2]).read_bytes()==Path(sys.argv[1]).read_bytes()+b'openpyxl==3.1.5\ndefusedxml==0.7.1\net-xmlfile==2.0.0\n'
PY_REQUIREMENTS
[[ -d ${RUNNER_TEMP:-} && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die HOST_SCRATCH_REQUIRED
nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-integrated.XXXXXXXX")
label='org.ilovedrrealty.group.author.browser.acceptance'
run_key="$GITHUB_RUN_ID-$nonce"
app_image="ildr-platform-browser:$run_key"
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
mkdir "$state/frontend-work" "$state/npm-cache"
source_repo="$frontend"
git -C "$source_repo" archive --format=tar "$frontend_commit" | tar -xf - -C "$state/frontend-work" || die ARCHIVE_REQUIRED
printf 'PLATFORM_FRONTEND_DIRECT_SOURCE commit=%s tree=%s review_base=06d7f74965896ae23d47e7701fc1dbfd9815cc40 paths=%s\n' \
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
python3 -I -B - "$state/frontend-work/tests" <<'PY_TEST_FILES'
from pathlib import Path
import sys
assert len(list(Path(sys.argv[1]).glob('*.test.cjs')))==65
PY_TEST_FILES
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

# These exact unchanged-source phases are retained only from Root's actual primary receipt.
retained_log_sha=a1730d941824f2131087cf461dd12adaec7ebe03f926b09362a869c1229b4efd
retained_acceptance_sha=03e1b66bbc538d3440d48ff113de5bc5acab6dc7e98fe7149816488dc8812cd5
[[ "$retained_log_sha" =~ ^[a-f0-9]{64}$ && "$retained_acceptance_sha" =~ ^[a-f0-9]{64}$ ]] || die RETAINED_ACTUAL_PRIMARY_PROOF_REQUIRED
printf 'GROUP_AUTHOR_RETAINED_FRONTEND run=45902156502 job=140190268622 frontend=%s backend=%s contracts=549 pass=549 fail=0 cancelled=0 skipped=0 todo=0 test_files=65 standalone_typecheck_native=0 fresh_current=false log_sha256=%s root_acceptance_sha256=%s\n' \
  "$frontend_commit" "$backend_commit" "$retained_log_sha" "$retained_acceptance_sha"
verify_source || die RETAINED_FRONTEND_SOURCE_MISMATCH

native=0
bounded 360s docker run "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1536 \
  npm_config_userconfig=/tmp/ildr-user.npmrc npm_config_globalconfig=/tmp/ildr-global.npmrc \
  npm_config_cache=/tmp/npm npm_config_audit=false npm_config_fund=false npm_config_update_notifier=false \
  VITE_API_URL=http://127.0.0.1:8918 VITE_LANDING_URL=http://127.0.0.1:8917 \
  VITE_GOOGLE_CLIENT_ID=synthetic-ci.apps.googleusercontent.com \
  sh -c 'npm run build --workspace=dashboard' \
  >"$state/dashboard.log" 2>&1 || native=$?
python3 -I -B - "$state/dashboard.log" "$state/frontend-work/apps/dashboard/dist/index.html" "$native" <<'PY' || die ACTUAL_DASHBOARD_BUILD_REQUIRED
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
assert b"vite build" in data and index.is_file() and not index.is_symlink()
assert b"127.0.0.1:8918" in b"".join(f.read_bytes() for f in index.parent.glob("assets/*.js") if f.is_file())
print("GROUP_AUTHOR_DASHBOARD native=0 build=success standalone_typecheck=retained_run_45902156502")
PY
verify_source || die DASHBOARD_ALTERED_SOURCE

tools="$ops/ci/group-author-browser"
diagnostic_tools="$tools"
inbox_helpers="$ops/ci/native-inbox/verify.py"
landing_server="$ops/ci/buyer-phone/serve-landing.mjs"
dockerfile="$migration/ci/migration/services163.Dockerfile"
dashboard_server="$relay/ci/platform-browser/accepted/serve_dashboard_fixture.mjs"
printf '%s  %s\n' \
  e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9 "$dockerfile" \
  29e0041be48ea60430b9166c65f00190cf726452a0e62979a6075156f9b81ff3 "$dashboard_server" \
  f03f06dae7c31f3415dad15492ff581ff94f6c87a519449f84d64780c10e0cbd "$landing_server" \
  388fcae33bf63234df9739ac7405c1a2d49b4914af2a32d56e9023fdb7ed144e "$inbox_helpers" | sha256sum --check --status || die ACCEPTED_RUNTIME_TOOLS_REQUIRED
python_digest='sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60'
python_image="docker.io/library/python:3.11-slim@$python_digest"
postgres_image='docker.io/library/postgres:17.6@sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409'
browser_image='mcr.microsoft.com/playwright@sha256:ee15712262f017b8157421b212b12099146645f44b1bc5c0514cbbe6a229b1d0'
for image in "$python_image" "$postgres_image" "$browser_image"; do
  bounded 180s docker pull --platform linux/amd64 "$image" >/dev/null || die PINNED_RUNTIME_IMAGE_PULL_FAILED
  [[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image") == linux/amd64 ]] || die EXACT_RUNTIME_IMAGE_PLATFORM_REQUIRED
done
mkdir "$state/python-context" "$state/fixtures" "$state/browser-run"
cp "$baseline/requirements.txt" "$state/python-context/requirements.txt"
cp "$dockerfile" "$state/python-context/Dockerfile"
cat >>"$state/python-context/Dockerfile" <<'DOCKER_READERS'
RUN mkdir -p /opt/contact-import && /usr/local/bin/python -I -B -c "import importlib.metadata as m,json,re;from pathlib import Path;Path('/opt/contact-import/baseline-inventory.json').write_text(json.dumps({re.sub(r'[-_.]+','-',d.metadata['Name'].lower()):d.version for d in m.distributions()},sort_keys=True))"
COPY readers.txt /opt/contact-import/readers.txt
RUN /usr/local/bin/python -I -B -m pip install --retries 0 --timeout 30 --no-cache-dir --no-compile --no-deps --only-binary=:all: --require-hashes --requirement /opt/contact-import/readers.txt \
    && /usr/local/bin/python -I -B -m pip check
DOCKER_READERS
cat >"$state/python-context/readers.txt" <<'READER_WHEELS'
openpyxl==3.1.5 --hash=sha256:5282c12b107bffeef825f4617dc029afaf41d0ea60823bbb665ef3079dc79de2
defusedxml==0.7.1 --hash=sha256:a352e7e428770286cc899e2542b6cdaedb2b4953ff269a210103ec58f6198a61
et-xmlfile==2.0.0 --hash=sha256:7a91720bc756843502c3b7504c77b8fe44217c85c537d85037f0f536151b2caa
READER_WHEELS

cp -R "$tools/." "$state/browser-run/"
cp "$diagnostic_tools/run-browser.mjs" "$state/browser-run/run-browser.mjs"
bounded 600s docker build --platform linux/amd64 --pull=false --quiet --label "$label=$run_key" \
  --build-arg "PYTHON_DIGEST=$python_digest" --file "$state/python-context/Dockerfile" --tag "$app_image" "$state/python-context" \
  >"$state/python-build.log" 2>&1 || die PINNED_API_DEPENDENCIES_REQUIRED

python3 -I -B - "$state/frontend-work" "$state/browser-run" <<'PY_BROWSER'
import base64, hashlib, io, json, tarfile, urllib.request
from pathlib import Path, PurePosixPath
import sys
workspace, browser = map(Path, sys.argv[1:])
output = workspace/'apps/dashboard/dist'
assert (output/'index.html').is_file() and (output/'templates/contact-import.csv').is_file()
for language in ('en', 'es'):
    def copy(name):
        return json.loads((workspace/f'apps/dashboard/src/locale/{language}/{name}.json').read_bytes())
    (browser/f'{language}.json').write_text(json.dumps(copy('property_network'),ensure_ascii=False),encoding='utf-8')
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
            with path.open('xb') as stream:
                stream.write(archive.extractfile(member).read())
assert json.loads((target/'package.json').read_bytes())['version'] == '1.64.1'
PY_BROWSER

pg=$(bounded 25s docker create --label "$label=$run_key" --network none \
  --security-opt no-new-privileges:true --memory 768m --memory-swap 768m --pids-limit 96 --cpus 1 \
  --tmpfs /var/lib/postgresql/data:rw,size=384m \
  --env POSTGRES_HOST_AUTH_METHOD=trust --env POSTGRES_DB=postgres \
  "$postgres_image" postgres -p 55449 -h 127.0.0.1) || die POSTGRES_CREATE_FAILED
[[ "$pg" =~ ^[a-f0-9]{64}$ && $(bounded 15s docker inspect --format "{{index .Config.Labels \"$label\"}}" "$pg") == "$run_key" &&
   $(bounded 15s docker inspect --format '{{.HostConfig.NetworkMode}}' "$pg") == none ]] || die OWNED_LOOPBACK_DATABASE_REQUIRED
bounded 25s docker start "$pg" >/dev/null || die POSTGRES_START_FAILED
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  if bounded 3s docker exec "$pg" pg_isready -h 127.0.0.1 -p 55449 -U postgres -d postgres >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 && $(bounded 5s docker exec "$pg" ls /sys/class/net) == lo ]] || die POSTGRES_READY_REQUIRED
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c 'CREATE ROLE anon NOLOGIN NOINHERIT; CREATE ROLE authenticated NOLOGIN NOINHERIT; CREATE ROLE service_role NOLOGIN NOINHERIT' \
  >/dev/null || die SYNTHETIC_DATABASE_ROLES_REQUIRED
[[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres \
  -v ON_ERROR_STOP=1 -c 'SHOW server_version_num') == 170006 ]] || die EXACT_POSTGRES_REQUIRED

runtime=(--label "$label=$run_key" --network "container:$pg" --user "$(id -u):$(id -g)" --read-only --init
         --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 256 --cpus 2)
dashboard=$(bounded 25s docker create "${runtime[@]}" --memory 192m --memory-swap 192m \
  --tmpfs /tmp:rw,size=32m --mount "type=bind,src=$state/frontend-work/apps/dashboard/dist,dst=/dashboard-dist,readonly" \
  --mount "type=bind,src=$dashboard_server,dst=/serve-dashboard.mjs,readonly" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp GITHUB_ACTIONS=true GITHUB_REPOSITORY=ilovedrrealty/realty-api \
  node /serve-dashboard.mjs) || die DASHBOARD_SERVER_CREATE_FAILED
bounded 20s docker start "$dashboard" >/dev/null || die STATIC_SERVER_START_FAILED

phases=(group_author)
drivers=(run-browser.mjs)
for index in "${!phases[@]}"; do
  phase="${phases[$index]}"
  phase_nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
  [[ "$phase_nonce" =~ ^[a-f0-9]{32}$ ]] || die PHASE_NONCE_REQUIRED
  db="ildr_browser_ci_$phase_nonce"
  marker="ildr-group-author-browser-ci:$phase_nonce"
  bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "CREATE DATABASE $db" -c "COMMENT ON DATABASE $db IS '$marker'" >/dev/null || die OWNED_PHASE_DATABASE_REQUIRED
  mkdir "$state/fixtures/$phase"
  api=$(bounded 25s docker create "${runtime[@]}" --memory 2g --memory-swap 2g --tmpfs /tmp:rw,exec,size=128m \
    --mount "type=bind,src=$backend,dst=/application,readonly" --mount "type=bind,src=$previous,dst=/previous,readonly" --mount "type=bind,src=$reference,dst=/baseline106,readonly" --mount "type=bind,src=$ops/ci,dst=/ci-tools,readonly" \
    --mount "type=bind,src=$state/fixtures/$phase,dst=/browser-fixtures" "$app_image" \
    env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp PYTHONDONTWRITEBYTECODE=1 \
    GITHUB_ACTIONS=true GITHUB_REPOSITORY=ilovedrrealty/realty-api ILDR_BROWSER_CI_NONCE="$phase_nonce" \
    python -I -B /ci-tools/group-author-browser/serve_fixture.py) || die API_CREATE_FAILED
  bounded 20s docker start "$api" >/dev/null || die API_START_FAILED
  ready=0
  for ((attempt=0; attempt<240; attempt++)); do
    if [[ -f "$state/fixtures/$phase/fixtures.json" ]] && bounded 4s docker exec "$api" python -I -B -c \
      'import urllib.request; assert all(urllib.request.urlopen(url,timeout=2).status==200 for url in ("http://127.0.0.1:8918/health","http://127.0.0.1:4175/users"))' >/dev/null 2>&1; then
      ready=1; break
    fi
    [[ $(bounded 4s docker inspect --format '{{.State.Running}}' "$api") == true ]] || break
    sleep 1
  done
  if [[ "$ready" != 1 ]]; then
    bounded 8s docker logs "$api" >"$state/api-$phase.log" 2>&1 || true
    python3 -I -B "$diagnostic_tools/check-result.py" api "$state/api-$phase.log" 1 || true
    die ACTUAL_PHASE_API_REQUIRED
  fi
  landing_native=0
  bounded 480s docker run "${prepare[@]}" --network "container:$pg" \
    --mount "type=bind,src=$state/frontend-work,dst=/work" "$node_image" \
    env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1536 \
    NEXT_TELEMETRY_DISABLED=1 NEXT_PUBLIC_API_URL=http://127.0.0.1:8918 NEXT_PUBLIC_SITE_ENV=ci \
    NEXT_PUBLIC_DASHBOARD_URL=http://127.0.0.1:4175 \
    sh -c 'npm run build --workspace=landing' >"$state/landing.log" 2>&1 || landing_native=$?
  python3 -I -B - "$state/landing.log" "$state/frontend-work/apps/landing/out" "$landing_native" <<'PY_LANDING' || die ACTUAL_NEXT_SIGNUP_EXPORT_REQUIRED
from pathlib import Path
import sys
log,output=map(Path,sys.argv[1:3])
assert log.stat().st_size<=2*1024*1024
exports=[output/name/'index.html' for name in ('login','signup','verify','team','blog','calculator')]
print('GROUP_AUTHOR_LANDING_BUILD native='+sys.argv[3]+' required_exports='+str(sum(path.is_file() and not path.is_symlink() for path in exports)))
assert sys.argv[3]=='0' and all(path.is_file() and not path.is_symlink() for path in exports)
PY_LANDING
  verify_source || die LANDING_ALTERED_FRONTEND_SOURCE
  landing=$(bounded 25s docker create "${runtime[@]}" --memory 192m --memory-swap 192m \
    --tmpfs /tmp:rw,size=32m --mount "type=bind,src=$state/frontend-work/apps/landing/out,dst=/landing-out,readonly" \
    --mount "type=bind,src=$landing_server,dst=/serve-landing.mjs,readonly" "$node_image" \
    env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp GITHUB_ACTIONS=true GITHUB_REPOSITORY=ilovedrrealty/realty-api \
    node /serve-landing.mjs) || die LANDING_SERVER_CREATE_FAILED
  bounded 20s docker start "$landing" >/dev/null || die LANDING_SERVER_START_FAILED
  bounded 10s docker exec "$api" python -I -B -c \
    'import urllib.request; assert urllib.request.urlopen("http://127.0.0.1:8917/team/",timeout=5).status==200' \
    >/dev/null || die ACTUAL_LANDING_SIGNUP_REQUIRED
  browser_native=0
  bounded 600s docker run --rm "${runtime[@]}" --memory 2g --memory-swap 2g --shm-size 256m \
    --tmpfs /tmp:rw,exec,size=256m --workdir /browser-run \
    --mount "type=bind,src=$state/browser-run,dst=/browser-run,readonly" \
    --mount "type=bind,src=$state/browser-run,dst=/rehearsal,readonly" \
    --mount "type=bind,src=$state/fixtures/$phase,dst=/browser-fixtures,readonly" "$browser_image" \
    env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp GITHUB_ACTIONS=true GITHUB_REPOSITORY=ilovedrrealty/realty-api \
    ILDR_BROWSER_FIXTURES=/browser-fixtures/fixtures.json PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    node "/browser-run/${drivers[$index]}" >"$state/browser-$phase.log" 2>&1 || browser_native=$?
  # Preserve the genuine API final-state proof even when browser assertions fail.
  bounded 35s docker stop --time 25 "$api" >/dev/null || die API_GRACEFUL_STOP_REQUIRED
  api_native=$(bounded 5s docker inspect --format '{{.State.ExitCode}}' "$api")
  bounded 10s docker logs "$api" >"$state/api-$phase.log" 2>&1 || die API_LOG_REQUIRED
  process_ok=0
  python3 -I -B - "$api" "$pg" <<'PY_PROCESS' || process_ok=$?
import json,subprocess,sys
states=[json.loads(subprocess.check_output(['docker','inspect','--format','{{json .State}}',identifier])) for identifier in sys.argv[1:]]
a,p=states
record={'api_exit':a['ExitCode'],'api_running':a['Running'],'api_oom':a['OOMKilled'],'postgres_running':p['Running'],'postgres_oom':p['OOMKilled']}
assert type(record['api_exit']) is int and all(type(record[key]) is bool for key in record if key!='api_exit')
print('GROUP_AUTHOR_PROCESS '+json.dumps(record,separators=(',',':')))
assert record=={'api_exit':0,'api_running':False,'api_oom':False,'postgres_running':True,'postgres_oom':False}
PY_PROCESS
  browser_ok=0; api_ok=0
  python3 -I -B "$diagnostic_tools/check-result.py" browser "$state/browser-$phase.log" "$browser_native" || browser_ok=$?
  python3 -I -B "$diagnostic_tools/check-result.py" api "$state/api-$phase.log" "$api_native" || api_ok=$?
  [[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres \
    -v ON_ERROR_STOP=1 -c "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname='$db'") == "$marker" ]] || die OWNED_DATABASE_DROP_GUARD
  bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE $db" >/dev/null || die OWNED_DATABASE_DROP_REQUIRED
  [[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres \
    -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_database WHERE datname='$db'") == 0 ]] || die OWNED_DATABASE_ABSENCE_REQUIRED
  bounded 15s docker rm -v "$api" >/dev/null || die PHASE_API_REMOVAL_REQUIRED
  [[ "$browser_ok" == 0 && "$api_ok" == 0 && "$process_ok" == 0 &&
     -z $(find "$state/fixtures/$phase" -mindepth 1 -maxdepth 1 -print -quit) ]] || die EXACT_BROWSER_AND_AUTHORITATIVE_FACTS_REQUIRED
  verify_source || die BROWSER_ALTERED_FRONTEND_SOURCE
  [[ -z $(git -C "$backend" status --porcelain=v1 --untracked-files=all) ]] || die BROWSER_ALTERED_BACKEND_SOURCE
done
printf 'GROUP_AUTHOR_HOSTED_RESULT operations=%s frontend=%s backend=%s retained_native162_run=45652164070 retained_frontend_tests=549 retained_frontend_run=45902156502 retained_standalone_strict_run=45902156502 current_frontend_tests=false current_standalone_strict=false dashboard=current landing=current browser=exact_checker_pass authoritative_fixture=exact_checker_pass live_provider_calls=0 run=%s attempt=1\n' \
  "$GITHUB_SHA" "$frontend_commit" "$backend_commit" "$GITHUB_RUN_ID"

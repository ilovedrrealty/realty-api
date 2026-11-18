#!/usr/bin/env bash
set -euo pipefail
die() { printf 'PROFESSIONAL_GOOGLE_NATIVE_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }
[[ $# == 5 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${GITHUB_REF:-} == refs/heads/codex-ci-professional-google-native-20261118 && ${GITHUB_RUN_ATTEMPT:-} == 1 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 ]] || die EXACT_HOSTED_EVENT_REQUIRED
ops="$1"; application="$2"; baseline="$3"; recipe="$4"; previous="$5"
[[ "$ops" == "$GITHUB_WORKSPACE/map-ops" && "$application" == "$GITHUB_WORKSPACE/map-app" &&
   "$baseline" == "$GITHUB_WORKSPACE/map-base" && "$recipe" == "$GITHUB_WORKSPACE/map-recipe" && "$previous" == "$GITHUB_WORKSPACE/map-previous" ]] || die EXACT_PATHS_REQUIRED
for path in "$ops" "$application" "$baseline" "$recipe" "$previous"; do
  [[ $(realpath "$path") == "$path" && -z $(git -C "$path" status --porcelain=v1 --untracked-files=all) &&
     ! -e "$path/.env" && ! -L "$path/.env" ]] || die CLEAN_SOURCE_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" show -s --format=%P HEAD) == b7c3de95a74ed84bc4aaed3e06cada2d3685f3a1 &&
   $(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) == $'.github/workflows/professional-google-native.yml\nci/professional-google-native/run-hosted-native.sh\nci/professional-google-native/verify-native.py' &&
   $(git -C "$application" rev-parse HEAD) == d9cfc94a1ebc45b8f3c391a69cbb8d0d8b25d26b &&
   $(git -C "$application" rev-parse 'HEAD^{tree}') == 4a00441d18cf55c3e26414d1a7c2b49f791ee33d &&
   $(git -C "$previous" rev-parse HEAD) == 4e23cc8b643a4b07f4e14042e530e6271dad9c8f &&
   $(git -C "$previous" rev-parse 'HEAD^{tree}') == 8093ea7aa9fd8f87a842e2527c715a8c482ac1fc &&
   $(git -C "$baseline" rev-parse HEAD) == 01695530df670a94ac28f8b3bf36cc34b5b7a942 &&
   $(git -C "$baseline" rev-parse 'HEAD^{tree}') == e88ea0c57f9fac39545322532a5992d5a2927676 &&
   $(git -C "$recipe" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 ]] || die EXACT_PINS_REQUIRED
[[ -z $(git -C "$application" diff --name-only 4e23cc8b643a4b07f4e14042e530e6271dad9c8f HEAD -- alembic models requirements.txt) ]] || die EXISTING_SCHEMA_DEPENDENCY_BYTES_REQUIRED
git -C "$application" merge-base --is-ancestor 4e23cc8b643a4b07f4e14042e530e6271dad9c8f HEAD || die EXACT_PREDECESSOR_REQUIRED
[[ $(git -C "$application" diff --name-only 4e23cc8b643a4b07f4e14042e530e6271dad9c8f HEAD) == $'routes/professionals.py\nschemas/professional_google.py\nschemas/professionals.py\ntests/test_professional_google_links.py\ntests/test_professionals.py\nutils/professional_google.py' ]] || die EXACT_GOOGLE_SLICE_REQUIRED
dockerfile="$recipe/ci/migration/services163.Dockerfile"
printf '%s  %s\n' e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9 "$dockerfile" |
  sha256sum --check --status || die ACCEPTED_RECIPE_REQUIRED
python3 -I -B - "$baseline/requirements.txt" "$application/requirements.txt" <<'PY_REQUIREMENTS'
from pathlib import Path
import sys
assert Path(sys.argv[2]).read_bytes() == Path(sys.argv[1]).read_bytes() + b'openpyxl==3.1.5\ndefusedxml==0.7.1\net-xmlfile==2.0.0\n'
PY_REQUIREMENTS
umask 077
[[ -d ${RUNNER_TEMP:-} && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die OWNED_SCRATCH_REQUIRED
nonce=$(python3 -I -B -c 'from uuid import uuid4;print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-professional-google-native.XXXXXXXX")
label=org.ilovedrrealty.professional-google-native
run_key="$GITHUB_RUN_ID-$nonce"
image="ildr-professional-google-native:$run_key"
python_digest=sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
postgres='docker.io/library/postgres:17.6@sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409'
database="ildr_professional_google_$nonce"
pg=''
owned() { [[ "$1" =~ ^[a-f0-9]{64}$ && $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$1") == "$run_key" ]]; }
cleanup() {
  local code=$? clean=1 ids id images tagged
  trap - EXIT
  set +e
  ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  for id in $ids; do
    [[ "$id" != "$pg" ]] || continue
    if owned "$id"; then bounded 20s docker rm -f -v "$id" >/dev/null 2>&1 || clean=0; else clean=0; fi
  done
  if [[ -n "$pg" ]]; then
    if owned "$pg"; then bounded 20s docker rm -f -v "$pg" >/dev/null 2>&1 || clean=0; else clean=0; fi
  fi
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
  if [[ "$state" == "$RUNNER_TEMP"/ildr-professional-google-native.* && -d "$state" && ! -L "$state" && $(realpath "$state") == "$state" ]]; then
    rm -rf -- "$state" || clean=0
  else clean=0; fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'PROFESSIONAL_GOOGLE_NATIVE_OWNED_CLEANUP commit=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT
mkdir "$state/build"
cp "$baseline/requirements.txt" "$state/build/requirements.txt"
cp "$dockerfile" "$state/build/Dockerfile"
cat >>"$state/build/Dockerfile" <<'DOCKER_READERS'
RUN mkdir -p /opt/directory-map && /usr/local/bin/python -I -B -c "import importlib.metadata as m,json,re;from pathlib import Path;Path('/opt/directory-map/baseline-inventory.json').write_text(json.dumps({re.sub(r'[-_.]+','-',d.metadata['Name'].lower()):d.version for d in m.distributions()},sort_keys=True))"
COPY readers.txt /opt/directory-map/readers.txt
RUN /usr/local/bin/python -I -B -m pip install --retries 0 --timeout 30 --no-cache-dir --no-compile --no-deps --only-binary=:all: --require-hashes --requirement /opt/directory-map/readers.txt \
    && /usr/local/bin/python -I -B -m pip check
DOCKER_READERS
cat >"$state/build/readers.txt" <<'READER_WHEELS'
openpyxl==3.1.5 --hash=sha256:5282c12b107bffeef825f4617dc029afaf41d0ea60823bbb665ef3079dc79de2
defusedxml==0.7.1 --hash=sha256:a352e7e428770286cc899e2542b6cdaedb2b4953ff269a210103ec58f6198a61
et-xmlfile==2.0.0 --hash=sha256:7a91720bc756843502c3b7504c77b8fe44217c85c537d85037f0f536151b2caa
READER_WHEELS
for base in "docker.io/library/python:3.11-slim@$python_digest" "$postgres"; do
  bounded 180s docker pull --platform linux/amd64 "$base" >/dev/null || die PINNED_IMAGE_PULL_FAILED
  [[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$base") == linux/amd64 ]] || die EXACT_IMAGE_PLATFORM_REQUIRED
done
bounded 600s docker build --platform linux/amd64 --pull=false --quiet --label "$label=$run_key" \
  --build-arg "PYTHON_DIGEST=$python_digest" --file "$state/build/Dockerfile" --tag "$image" "$state/build" \
  >"$state/build.log" 2>&1 || die DEPENDENCY_IMAGE_BUILD_FAILED
pg=$(bounded 25s docker create --label "$label=$run_key" --network none --security-opt no-new-privileges:true \
  --memory 768m --memory-swap 768m --pids-limit 96 --cpus 1 --tmpfs /var/lib/postgresql/data:rw,size=384m \
  --env POSTGRES_HOST_AUTH_METHOD=trust --env "POSTGRES_DB=$database" \
  "$postgres" postgres -p 55449 -h 127.0.0.1) || die POSTGRES_CREATE_FAILED
owned "$pg" && [[ $(bounded 15s docker inspect --format '{{.HostConfig.NetworkMode}}' "$pg") == none ]] || die OWNED_ISOLATED_POSTGRES_REQUIRED
bounded 25s docker start "$pg" >/dev/null || die POSTGRES_START_FAILED
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  if bounded 3s docker exec "$pg" pg_isready -h 127.0.0.1 -p 55449 -U postgres -d "$database" >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 && $(bounded 5s docker exec "$pg" ls /sys/class/net) == lo ]] || die LOOPBACK_POSTGRES_REQUIRED
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d "$database" -v ON_ERROR_STOP=1 \
  -c 'CREATE ROLE anon NOLOGIN NOINHERIT; CREATE ROLE authenticated NOLOGIN NOINHERIT; CREATE ROLE service_role NOLOGIN NOINHERIT' \
  -c 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role' \
  -c "COMMENT ON DATABASE $database IS 'ildr-professional-google-ci:$nonce'" >/dev/null || die DATABASE_MARKER_REQUIRED
mkdir "$state/rollback"
python3 -I -B - "$previous" "$application" "$state/rollback" "$state/rollback-manifest.json" <<'PY_ROLLBACK'
from pathlib import Path
import hashlib,json,subprocess,sys
previous,application,target,manifest=map(Path,sys.argv[1:])
assert target.is_dir() and not target.is_symlink() and not list(target.iterdir())
overlay=['schemas/professional_google.py','schemas/professionals.py','utils/professional_google.py']
files={}
for record in subprocess.check_output(['git','-C',str(previous),'ls-tree','-rz','HEAD']).split(b'\0'):
    if not record:continue
    info,name=record.split(b'\t',1);mode,kind,oid=info.decode().split();name=name.decode()
    assert kind=='blob' and mode in {'100644','100755'} and '..' not in Path(name).parts and not Path(name).is_absolute() and name!='.env'
    data=subprocess.check_output(['git','-C',str(previous),'cat-file','blob',oid])
    dest=target/name;assert dest.resolve().is_relative_to(target.resolve())
    dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data);dest.chmod(int(mode,8)&0o777)
    files[name]=hashlib.sha256(data).hexdigest()
for name in overlay:
    data=subprocess.check_output(['git','-C',str(application),'cat-file','blob','HEAD:'+name])
    dest=target/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
    files[name]=hashlib.sha256(data).hexdigest()
manifest.write_text(json.dumps({'base':'4e23cc8b643a4b07f4e14042e530e6271dad9c8f','overlay':overlay,'files':files},sort_keys=True))
PY_ROLLBACK
container=$(bounded 25s docker create --label "$label=$run_key" --network "container:$pg" \
  --user "$(id -u):$(id -g)" --read-only --init --cap-drop ALL --security-opt no-new-privileges:true \
  --pids-limit 192 --cpus 2 --memory 1536m --memory-swap 1536m --tmpfs /tmp:rw,exec,size=128m \
  --env GITHUB_ACTIONS=true --env GITHUB_REPOSITORY=ilovedrrealty/realty-api --env "ILDR_PROFESSIONAL_GOOGLE_CI_NONCE=$nonce" \
  --env "DATABASE_URL=postgresql+psycopg2://postgres@127.0.0.1:55449/$database" \
  --mount "type=bind,src=$application,dst=/application,readonly" --mount "type=bind,src=$ops/ci/social-groups-native,dst=/tools,readonly" \
  --mount "type=bind,src=$ops/ci/professional-google-native,dst=/google-tools,readonly" \
  --mount "type=bind,src=$state/rollback,dst=/rollback,readonly" \
  --mount "type=bind,src=$state/rollback-manifest.json,dst=/rollback-manifest.json,readonly" \
  "$image" /usr/local/bin/python -I -B /google-tools/verify-native.py) || die NATIVE_CONTAINER_CREATE_FAILED
owned "$container" || die OWNED_NATIVE_CONTAINER_REQUIRED
native_exit=0
bounded 600s docker start --attach "$container" >"$state/native.log" 2>&1 || native_exit=$?
native_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$container")
printf 'PROFESSIONAL_GOOGLE_NATIVE_PROCESS client=%s state=%s\n' "$native_exit" "$native_state"
python3 -I -B - "$state/native.log" <<'PY_REPORT'
from pathlib import Path
import json,re,sys
path=Path(sys.argv[1]);assert path.stat().st_size<=131072
prefix=b'PROFESSIONAL_GOOGLE_NATIVE_RESULT '
rows=[json.loads(line[len(prefix):]) for line in path.read_bytes().splitlines() if line.startswith(prefix)]
assert len(rows)==1
report=rows[0]
assert report.get('status') in {'PASS','FAIL'}
for suite in report.get('suites',[]):
    assert (suite['module'],suite['class'],suite['expected']) in {
        ('test_professional_google_links','ProfessionalGoogleLinksTest',7),('test_professionals','ProfessionalTests',31)}
    for diagnostic in suite['diagnostics']:
        assert re.fullmatch(r'test_[a-z_]+|setup_or_cleanup',diagnostic['test'])
        assert re.fullmatch(r'[A-Za-z_]{1,80}',diagnostic['error_type'])
print('PROFESSIONAL_GOOGLE_NATIVE_RESULT '+json.dumps(report,sort_keys=True,separators=(',',':')))
assert report.get('unexpected_transport_attempts')==0
assert report['status']=='PASS' and report['tests']==38 and report['schema_tables']==99
assert report['protected_owner_contacts']==82 and report['protected_hidden_profiles']==46
assert report['retained_jobs']==9 and report['retained_cms_version']==7 and report['retained_floorplans']==2
assert report['compatible_rollback_reads']==6 and report['all_catalog_rows_sequences_unchanged'] is True
assert report['professional_schema_cleanup_verified'] is True
PY_REPORT
[[ "$native_exit" == 0 && "$native_state" == '0 false false' ]] || die NATIVE_RUNTIME_FAILED
for path in "$application" "$baseline" "$recipe" "$previous"; do
  [[ -z $(git -C "$path" status --porcelain=v1 --untracked-files=all) ]] || die MOUNTED_SOURCE_CHANGED
done
printf 'PROFESSIONAL_GOOGLE_NATIVE_ACCEPTANCE commit=%s application=%s tests=38 run=%s attempt=1\n' \
  "$GITHUB_SHA" d9cfc94a1ebc45b8f3c391a69cbb8d0d8b25d26b "$GITHUB_RUN_ID"

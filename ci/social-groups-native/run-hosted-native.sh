#!/usr/bin/env bash
set -euo pipefail
die() { printf 'SOCIAL_GROUPS_NATIVE_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }
[[ $# == 5 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${GITHUB_REF:-} == refs/heads/codex-ci-social-groups-native-v2-20261118 && ${GITHUB_RUN_ATTEMPT:-} == 1 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 ]] || die EXACT_HOSTED_EVENT_REQUIRED
ops="$1"; application="$2"; baseline="$3"; recipe="$4"; previous="$5"
[[ "$ops" == "$GITHUB_WORKSPACE/groups-ops" && "$application" == "$GITHUB_WORKSPACE/groups-app" &&
   "$baseline" == "$GITHUB_WORKSPACE/groups-base" && "$recipe" == "$GITHUB_WORKSPACE/groups-recipe" && "$previous" == "$GITHUB_WORKSPACE/groups-previous" ]] || die EXACT_PATHS_REQUIRED
for path in "$ops" "$application" "$baseline" "$recipe" "$previous"; do
  [[ $(realpath "$path") == "$path" && -z $(git -C "$path" status --porcelain=v1 --untracked-files=all) &&
     ! -e "$path/.env" && ! -L "$path/.env" ]] || die CLEAN_SOURCE_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" show -s --format=%P HEAD) == b7c3de95a74ed84bc4aaed3e06cada2d3685f3a1 &&
   $(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) == $'.github/workflows/social-groups-native.yml\nci/social-groups-native/README.md\nci/social-groups-native/records.py\nci/social-groups-native/run-hosted-native.sh\nci/social-groups-native/verify.py' &&
   $(git -C "$application" rev-parse HEAD) == 4e23cc8b643a4b07f4e14042e530e6271dad9c8f &&
   $(git -C "$application" rev-parse 'HEAD^{tree}') == 8093ea7aa9fd8f87a842e2527c715a8c482ac1fc &&
   $(git -C "$previous" rev-parse HEAD) == 4d21cb3ae2b14475828ff8fbe710b22c34c3a53b &&
   $(git -C "$previous" rev-parse 'HEAD^{tree}') == 1e3f72978515a58733188caa235cb6ee61190a26 &&
   $(git -C "$baseline" rev-parse HEAD) == 01695530df670a94ac28f8b3bf36cc34b5b7a942 &&
   $(git -C "$baseline" rev-parse 'HEAD^{tree}') == e88ea0c57f9fac39545322532a5992d5a2927676 &&
   $(git -C "$recipe" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 ]] || die EXACT_PINS_REQUIRED
git -C "$application" merge-base --is-ancestor 4d21cb3ae2b14475828ff8fbe710b22c34c3a53b HEAD || die EXACT_PREDECESSOR_REQUIRED
[[ $(git -C "$application" diff --name-only 4d21cb3ae2b14475828ff8fbe710b22c34c3a53b HEAD) == $'alembic/versions/c4e2a7319b60_private_social_groups.py\nmain.py\nmodels/__init__.py\nmodels/social_groups.py\nroutes/social_groups.py\nschemas/social_groups.py\nservices/social_groups.py\ntests/test_social_groups.py' ]] || die EXACT_GROUPS_SLICE_REQUIRED
[[ -z $(git -C "$application" diff --name-only 4d21cb3ae2b14475828ff8fbe710b22c34c3a53b HEAD -- requirements.txt) ]] || die EXISTING_DEPENDENCIES_REQUIRED
[[ $(git -C "$application" show -s --format=%P HEAD) == 4404b3f1c4160b0df7c71d56780f410128c9d446 &&
   $(git -C "$application" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) == $'schemas/social_groups.py\nservices/social_groups.py\ntests/test_social_groups.py' ]] || die EXACT_POST_AUDIENCE_FIX_REQUIRED
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
state=$(mktemp -d "$RUNNER_TEMP/ildr-social-groups-native.XXXXXXXX")
label=org.ilovedrrealty.social-groups-native
run_key="$GITHUB_RUN_ID-$nonce"
image="ildr-social-groups-native:$run_key"
python_digest=sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60
postgres='docker.io/library/postgres:17.6@sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409'
database=postgres
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
  if [[ "$state" == "$RUNNER_TEMP"/ildr-social-groups-native.* && -d "$state" && ! -L "$state" && $(realpath "$state") == "$state" ]]; then
    rm -rf -- "$state" || clean=0
  else clean=0; fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'SOCIAL_GROUPS_NATIVE_OWNED_CLEANUP commit=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
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
bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
  -c 'CREATE ROLE anon NOLOGIN NOINHERIT; CREATE ROLE authenticated NOLOGIN NOINHERIT; CREATE ROLE service_role NOLOGIN NOINHERIT' >/dev/null || die ISOLATED_ROLES_REQUIRED
mkdir "$state/rollback"
git -C "$previous" archive --format=tar 4d21cb3ae2b14475828ff8fbe710b22c34c3a53b | tar -xf - -C "$state/rollback"
migration=alembic/versions/c4e2a7319b60_private_social_groups.py
git -C "$application" cat-file blob "4e23cc8b643a4b07f4e14042e530e6271dad9c8f:$migration" >"$state/rollback/$migration"
run_mode() {
  local mode="$1" container client_exit=0 native_state
  container=$(bounded 25s docker create --label "$label=$run_key" --network "container:$pg" \
    --user "$(id -u):$(id -g)" --read-only --init --cap-drop ALL --security-opt no-new-privileges:true \
    --pids-limit 192 --cpus 2 --memory 1536m --memory-swap 1536m --tmpfs /tmp:rw,exec,size=128m \
    --env GITHUB_ACTIONS=true --env GITHUB_REPOSITORY=ilovedrrealty/realty-api --env "ILDR_GROUPS_CI_NONCE=$nonce" \
    --mount "type=bind,src=$application,dst=/application,readonly" \
    --mount "type=bind,src=$previous,dst=/previous,readonly" \
    --mount "type=bind,src=$state/rollback,dst=/rollback,readonly" \
    --mount "type=bind,src=$ops/ci/social-groups-native,dst=/tools,readonly" \
    "$image" /usr/local/bin/python -I -B /tools/verify.py "$mode") || die NATIVE_CONTAINER_CREATE_FAILED
  owned "$container" || die OWNED_NATIVE_CONTAINER_REQUIRED
  bounded 900s docker start --attach "$container" >"$state/$mode.log" 2>&1 || client_exit=$?
  native_state=$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$container")
  printf 'SOCIAL_GROUPS_NATIVE_PROCESS mode=%s client=%s state=%s\n' "$mode" "$client_exit" "$native_state"
  python3 -I -B - "$state/$mode.log" "$mode" <<'PY_REPORT'
from pathlib import Path
import json,re,sys
path=Path(sys.argv[1]);assert path.stat().st_size<=131072
prefix=b'GROUPS_NATIVE_RESULT '
lines=path.read_bytes().splitlines()
reports=[json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
assert len(reports)==1
report=reports[0]
allowed_failure={'status','phase','reason','error_type'}
run_keys={'status','mode','tests','fresh_tables','forward_from_tables','forward_tables','old_tables_preserved','new_private_tables','accepted_jobs_retained','hidden_profiles_retained','owner_contacts_retained','cms_version_retained','floorplan_rows_retained','actual_old_reads','populated_downgrade_refused','external_provider_attempts','fresh_forward_catalog_sha256'}
restore_keys={'status','mode','tables','constraints','old_raw_check_variations','new_raw_check_variations','raw_exact_constraints','server_pretty_constraints_equal','native_deparser_controls','floorplan_rows','all_rows_sequences_equal','eight_raw_catalog_components_equal','external_provider_attempts'}
if report.get('status')=='FAIL':
    assert set(report)==allowed_failure and all(isinstance(v,str) and re.fullmatch(r'[A-Za-z_]{1,100}',v) for v in report.values())
else:
    assert set(report)==(run_keys if sys.argv[2]=='run' else restore_keys)
    assert report.get('status')=='PASS' and report.get('mode')==sys.argv[2] and report['external_provider_attempts']==0
    if sys.argv[2]=='run':
        expected={'tests':24,'fresh_tables':99,'forward_from_tables':92,'forward_tables':99,'old_tables_preserved':92,'new_private_tables':7,'accepted_jobs_retained':9,'hidden_profiles_retained':46,'owner_contacts_retained':82,'cms_version_retained':7,'floorplan_rows_retained':2,'actual_old_reads':9,'external_provider_attempts':0}
        assert all(type(report[k]) is int and report[k]==v for k,v in expected.items())
        assert report['populated_downgrade_refused'] is True
        assert re.fullmatch(r'[0-9a-f]{64}',report['fresh_forward_catalog_sha256'])
    else:
        expected={'tables':99,'constraints':529,'old_raw_check_variations':13,'server_pretty_constraints_equal':529,'native_deparser_controls':3,'floorplan_rows':2,'external_provider_attempts':0}
        assert all(type(report[k]) is int and report[k]==v for k,v in expected.items())
        assert type(report['new_raw_check_variations']) is int and 0<=report['new_raw_check_variations']<=4
        assert type(report['raw_exact_constraints']) is int and report['raw_exact_constraints']==516-report['new_raw_check_variations']
        assert report['all_rows_sequences_equal'] is True and report['eight_raw_catalog_components_equal'] is True
for line in lines:
    if line.startswith(b'GROUPS_NATIVE_TEST_DIAGNOSTIC '):
        row=json.loads(line[len(b'GROUPS_NATIVE_TEST_DIAGNOSTIC '):])
        assert set(row)=={'class','test','error_type'} and row['class'] in {'GroupInputTest','SocialGroupsTest'}
        assert re.fullmatch(r'test_[a-z_]+|setup_or_cleanup',row['test']) and re.fullmatch(r'[A-Za-z_]{1,80}',row['error_type'])
        print('GROUPS_NATIVE_TEST_DIAGNOSTIC '+json.dumps(row,sort_keys=True))
print('GROUPS_NATIVE_RESULT '+json.dumps(report,sort_keys=True,separators=(',',':')))
assert report['status']=='PASS'
PY_REPORT
  [[ "$client_exit" == 0 && "$native_state" == '0 false false' ]] || die NATIVE_RUNTIME_FAILED
}
run_mode run
forward="ildr_groups_forward_$nonce"
restored="ildr_groups_restore_$nonce"
bounded 90s docker exec "$pg" pg_dump -h 127.0.0.1 -p 55449 -U postgres --format=custom --schema=public --strict-names \
  --lock-wait-timeout=15000 --dbname="$forward" >"$state/full-public.dump" 2>"$state/dump.log" || die OWNED_CUSTOM_ARCHIVE_FAILED
python3 -I -B - "$state/full-public.dump" <<'PY_DUMP'
from pathlib import Path
import sys
p=Path(sys.argv[1]);assert 5<p.stat().st_size<=16777216
with p.open('rb') as f:assert f.read(5)==b'PGDMP'
PY_DUMP
bounded 15s docker exec "$pg" psql -X -h127.0.0.1 -p55449 -Upostgres -dpostgres -v ON_ERROR_STOP=1 \
  -c "CREATE DATABASE $restored TEMPLATE template0" -c "COMMENT ON DATABASE $restored IS 'ildr-social-groups-ci:$nonce:restore'" >/dev/null || die OWNED_RESTORE_DATABASE_REQUIRED
bounded 90s docker exec -i "$pg" pg_restore -h127.0.0.1 -p55449 -Upostgres --dbname="$restored" --exit-on-error --single-transaction --clean --if-exists \
  <"$state/full-public.dump" >"$state/restore.log" 2>&1 || die OWNED_CUSTOM_RESTORE_FAILED
run_mode restore
for path in "$application" "$baseline" "$recipe" "$previous"; do
  [[ -z $(git -C "$path" status --porcelain=v1 --untracked-files=all) ]] || die MOUNTED_SOURCE_CHANGED
done
printf 'SOCIAL_GROUPS_NATIVE_ACCEPTANCE commit=%s application=%s tests=24 tables=99 run=%s attempt=1\n' \
  "$GITHUB_SHA" 4e23cc8b643a4b07f4e14042e530e6271dad9c8f "$GITHUB_RUN_ID"

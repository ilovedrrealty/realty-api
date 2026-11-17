#!/usr/bin/env bash
# Only dependency preparation uses public registries. Test data is synthetic.
set -euo pipefail

die() { printf 'FLOOR_PLANS_NATIVE_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }

[[ ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 &&
   ${GITHUB_RUN_ATTEMPT:-} == 1 && ${GITHUB_REF:-} == refs/heads/codex-ci-listing-floor-plans-native-20261117 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ ]] || die EXACT_HOSTED_EVENT_REQUIRED

ops="$GITHUB_WORKSPACE/rehearsal-ops"
backend="$GITHUB_WORKSPACE/floor-plan-backend"
relay="$GITHUB_WORKSPACE/migration-relay"
backend_commit=eb0a47c7fe18feb5c0695608bc0ebd6d5c048298
backend_tree=0ab55d99df8804a1f16a487e8286180b2325d6b6
fixture_hash=9dd8f4eed3225cbb3b6a1f68141ec521837fe2c33f03b6a76c9c9b3aa12ae36e
recipe_hash=e546d8c5668b87ef28e604107bbb330e6f4b3873cf0208b4d24ca378bd0851f9
for checkout in "$ops" "$backend" "$relay"; do
  working_status=$(git -C "$checkout" status --porcelain=v1 --untracked-files=all) || die CLEAN_CHECKOUT_REQUIRED
  [[ $(realpath "$checkout") == "$checkout" && -z "$working_status" && ! -e "$checkout/.env" &&
     ! -L "$checkout/.env" ]] || die CLEAN_CHECKOUT_REQUIRED
done
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" rev-parse 'HEAD^') == 9958a614f45e68a796e4bb0692921a94f1d9a781 ]] ||
  die EXACT_OPERATIONS_REQUIRED
operations_delta=$(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) || die EXACT_OPERATIONS_REQUIRED
[[ "$operations_delta" == $'.github/workflows/listing-floor-plans-native.yml\nci/listing-floor-plans/run-hosted-native.sh' ]] ||
  die EXACT_OPERATIONS_REQUIRED
[[ $(git -C "$backend" rev-parse HEAD) == "$backend_commit" &&
   $(git -C "$backend" rev-parse 'HEAD^{tree}') == "$backend_tree" &&
   $(git -C "$backend" rev-parse 'HEAD^') == f10409cd12be49718505876125e429c0b0e4b62d &&
   $(git -C "$relay" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 ]] ||
  die EXACT_APPLICATION_REQUIRED
app_delta=$(git -C "$backend" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) || die EXACT_APPLICATION_REQUIRED
[[ "$app_delta" == $'alembic/versions/86fc1b2e4a79_private_listing_floor_plans.py\nmain.py\nmodels/__init__.py\nmodels/listing_floor_plan.py\nroutes/listing_floor_plans.py\nschemas/listing_floor_plan.py\nservices/listing_floor_plans.py\ntests/test_listing_floor_plans.py' ]] ||
  die EXACT_APPLICATION_REQUIRED
dockerfile="$relay/ci/migration/services163.Dockerfile"
printf '%s  %s\n' "$recipe_hash" "$dockerfile" "$fixture_hash" "$backend/tests/test_listing_floor_plans.py" |
  sha256sum --check --status || die FIXTURE_OR_DEPENDENCY_HASH_MISMATCH

umask 077
[[ -d "$RUNNER_TEMP" && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die HOST_SCRATCH_REQUIRED
nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-listing-floor-plans.XXXXXXXX")
run_key="$GITHUB_RUN_ID-$nonce"
label='org.ilovedrrealty.listing-floor-plans.rehearsal'
app_image="ildr-listing-floor-plans:$run_key"
python_digest='sha256:673719e071ecf27434fd2635bbd8d00f49f4fdd81f243143438a42ced8bedf60'
python_image="docker.io/library/python:3.11-slim@$python_digest"
postgres_image='docker.io/library/postgres:17.6@sha256:b86568d3e0fe1dfaeff52714f9da36f206a30e4c49131b82bf96982d78627409'

owned() {
  [[ "$1" =~ ^[a-f0-9]{64}$ &&
     $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$1") == "$run_key" ]]
}

cleanup() {
  local code=$? clean=1 id inventory images tagged_id
  trap - EXIT
  set +e
  inventory=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  # Stop test processes before the namespace they share.
  for id in $inventory; do
    if [[ "$id" == "${pg:-}" ]]; then
      continue
    fi
    if ! owned "$id" || ! bounded 15s docker rm -f -v "$id" >/dev/null 2>&1; then
      clean=0
    fi
  done
  if [[ -n ${pg:-} ]]; then
    if ! owned "$pg" || ! bounded 15s docker rm -f -v "$pg" >/dev/null 2>&1; then
      clean=0
    fi
  fi
  images=$(bounded 15s docker image ls -q --no-trunc --filter "label=$label=$run_key" | sort -u) || clean=0
  if [[ -n "$images" ]]; then
    tagged_id=$(bounded 15s docker image inspect --format '{{.Id}}' "$app_image") || clean=0
    if [[ "$images" =~ ^sha256:[a-f0-9]{64}$ && "$images" == "$tagged_id" ]]; then
      bounded 15s docker image rm "$app_image" >/dev/null 2>&1 || clean=0
    else
      clean=0
    fi
  fi
  inventory=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
  images=$(bounded 15s docker image ls -q --no-trunc --filter "label=$label=$run_key") || clean=0
  [[ -z "$inventory" && -z "$images" ]] || clean=0
  if [[ "$state" == "$RUNNER_TEMP"/ildr-listing-floor-plans.* && -d "$state" &&
        ! -L "$state" && $(realpath "$state") == "$state" ]]; then
    rm -rf -- "$state" || clean=0
  else
    clean=0
  fi
  [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
  printf 'FLOOR_PLANS_NATIVE_CLEANUP operations=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
  exit "$code"
}
trap cleanup EXIT

mkdir "$state/context"
cp "$backend/requirements.txt" "$state/context/requirements.txt"
for image in "$python_image" "$postgres_image"; do
  bounded 180s docker pull --platform linux/amd64 "$image" >"$state/pull.log" 2>&1 || die PINNED_IMAGE_PULL_FAILED
  [[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image") == linux/amd64 ]] ||
    die EXACT_IMAGE_PLATFORM_REQUIRED
done
bounded 550s docker build --platform linux/amd64 --pull=false --quiet --label "$label=$run_key" \
  --build-arg "PYTHON_DIGEST=$python_digest" --file "$dockerfile" --tag "$app_image" "$state/context" \
  >"$state/build.log" 2>&1 || die PYTHON_DEPENDENCY_BUILD_FAILED

db="ildr_d05_test_$nonce"
pg=$(bounded 25s docker create --label "$label=$run_key" --network none \
  --security-opt no-new-privileges:true --memory 768m --memory-swap 768m --pids-limit 96 --cpus 1 \
  --tmpfs /var/lib/postgresql/data:rw,size=384m --env POSTGRES_HOST_AUTH_METHOD=trust --env "POSTGRES_DB=$db" \
  "$postgres_image" postgres -p 55449 -h 127.0.0.1) || die POSTGRES_CREATE_FAILED
owned "$pg" && [[ $(bounded 15s docker inspect --format '{{.HostConfig.NetworkMode}}' "$pg") == none ]] ||
  die OWNED_ISOLATED_POSTGRES_REQUIRED
bounded 25s docker start "$pg" >/dev/null || die POSTGRES_START_FAILED
ready=0
for ((attempt=0; attempt<45; attempt++)); do
  if bounded 3s docker exec "$pg" pg_isready -h 127.0.0.1 -p 55449 -U postgres -d "$db" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
[[ "$ready" == 1 && $(bounded 5s docker exec "$pg" ls /sys/class/net) == lo ]] || die LOOPBACK_POSTGRES_REQUIRED
psql=(docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -v ON_ERROR_STOP=1)
bounded 10s "${psql[@]}" -d "$db" -c "COMMENT ON DATABASE $db IS 'ildr-d05-ci:$nonce'" >/dev/null 2>&1 ||
  die DATABASE_IDENTITY_MARKER_FAILED

native=0
bounded 105s docker run --rm --label "$label=$run_key" --user "$(id -u):$(id -g)" --read-only --init \
  --cap-drop ALL --security-opt no-new-privileges:true --pids-limit 128 --cpus 2 --memory 1g --memory-swap 1g \
  --tmpfs /tmp:rw,size=64m --network "container:$pg" --workdir /application \
  --mount "type=bind,src=$backend,dst=/application,readonly" -i "$app_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin /usr/local/bin/python -I -B - "$db" "$nonce" "$fixture_hash" \
  >"$state/native.log" 2>&1 <<'PY' || native=$?
import hashlib
import os
from pathlib import Path
import re
import sys
import unittest

import psycopg2

db, nonce, fixture_hash = sys.argv[1:]
app = Path("/application")
assert os.statvfs(app).f_flag & os.ST_RDONLY
assert {interface.name for interface in Path("/sys/class/net").iterdir()} == {"lo"}
assert re.fullmatch(r"[0-9a-f]{32}", nonce) and db == f"ildr_d05_test_{nonce}"
assert hashlib.sha256((app / "tests/test_listing_floor_plans.py").read_bytes()).hexdigest() == fixture_hash
with psycopg2.connect(host="127.0.0.1", port=55449, dbname=db, user="postgres", connect_timeout=5) as connection:
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT current_database(), current_setting('server_version_num'),
                   shobj_description(oid, 'pg_database')
            FROM pg_database WHERE datname = current_database()
        """)
        assert cursor.fetchone() == (db, "170006", f"ildr-d05-ci:{nonce}")

os.environ["SEARCH_FILTER_TEST_DATABASE_URL"] = f"postgresql+psycopg2://postgres@127.0.0.1:55449/{db}"
sys.path.insert(0, str(app))
suite = unittest.TestLoader().discover(str(app / "tests"), pattern="test_listing_floor_plans.py")
assert suite.countTestCases() == 4
result = unittest.TextTestRunner(verbosity=2).run(suite)
assert result.testsRun == 4 and result.wasSuccessful() and not result.skipped and not result.expectedFailures
with connection, connection.cursor() as cursor:
    cursor.execute("SELECT count(*) FROM pg_namespace WHERE left(nspname, 16) = 'floor_plan_test_'")
    assert cursor.fetchone() == (0,)
connection.close()
print("FLOOR_PLANS_NATIVE_TESTS tests=4 passed=4 postgres=170006 fixture_cleanup=verified")

# Create a second, independently restorable schema with both file states.
from importlib.util import module_from_spec, spec_from_file_location
from uuid import UUID

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from database import Base
from models.listing import Listing
from models.user import User
from schemas.listing_floor_plan import PublishFloorPlan
from services import listing_floor_plans as plans
from test_listing_floor_plans import plan_image
from utils.transaction_document_storage import prepare_document

restore_schema = f"floor_plan_recovery_{nonce}"
bootstrap = create_engine(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"])
with bootstrap.begin() as database:
    database.execute(text(f'CREATE SCHEMA "{restore_schema}"'))
bootstrap.dispose()
engine = create_engine(os.environ["SEARCH_FILTER_TEST_DATABASE_URL"],
                       connect_args={"options": f"-csearch_path={restore_schema}"})
Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in ("users", "listings")])
migration_path = app / "alembic/versions/86fc1b2e4a79_private_listing_floor_plans.py"
spec = spec_from_file_location(migration_path.stem, migration_path)
migration = module_from_spec(spec)
spec.loader.exec_module(migration)
with engine.begin() as database:
    migration.op = Operations(MigrationContext.configure(database))
    migration.upgrade()
with Session(engine) as session:
    owner = User(role="admin", email="recovery-admin@example.invalid", status="active", email_verified=True,
                 display_name="Synthetic floorplan custodian")
    session.add(owner)
    session.flush()
    listing = Listing(title="Synthetic floor plan recovery", type="villa", transaction="sale",
                      price=100, location="Cabarete", status="active", submitted_by=owner.id)
    session.add(listing)
    session.flush()
    listing_id, owner_id = listing.id, owner.id
    session.commit()
    data = plan_image("white")
    published = plans.stage(session, listing_id, owner_id, None, data, prepare_document(data, "image/png"))["draft"]
    plans.publish(session, listing_id, owner_id, PublishFloorPlan(
        draft_id=UUID(str(published["id"])), expected_published_id=None, owner_permission_confirmed=True))
    data = plan_image("black")
    draft = plans.stage(session, listing_id, owner_id, None, data, prepare_document(data, "image/png"))["draft"]
assert draft["sha256"] != published["sha256"]
engine.dispose()
print(f"FLOOR_PLAN_RESTORE_SOURCE draft={draft['sha256']} published={published['sha256']}")
PY

python3 -I -B - "$state/native.log" "$native" <<'PY' || die NATIVE_TEST_VERIFICATION_FAILED
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
status = int(sys.argv[2])
assert path.stat().st_size <= 131072
lines = path.read_bytes().splitlines()
safe = b"FLOOR_PLANS_NATIVE_TESTS tests=4 passed=4 postgres=170006 fixture_cleanup=verified"
if status or lines.count(safe) != 1:
    print(f"FLOOR_PLANS_NATIVE_FAILURE code={status}")
    allowed = re.compile(rb"(?:FAIL|ERROR): (test_[a-z0-9_]{1,100}|setUpClass|tearDownClass) \(")
    for line in lines:
        match = allowed.match(line)
        if match:
            print("FLOOR_PLANS_NATIVE_CASE", match.group(1).decode("ascii"))
    sites = re.compile(rb'''^\s*File "/application/(tests/test_listing_floor_plans|schemas/listing_floor_plan|routes/listing_floor_plans|services/listing_floor_plans)\.py", line ([0-9]{1,4}), ''')
    for header, line in sorted({m.groups() for line in lines if (m := sites.match(line))}):
        print("FLOOR_PLANS_NATIVE_SITE", header.decode("ascii"), line.decode("ascii"))
    for code in sorted({m.group(1) for line in lines if (m := re.match(rb"^(?:psycopg2|sqlalchemy)\.errors\.([a-zA-Z]{1,65}):", line))}):
        print("FLOOR_PLANS_NATIVE_DB_ERROR", code.decode("ascii"))
    sys.exit(1)
print(safe.decode("ascii"))
PY

restore_schema="floor_plan_recovery_$nonce"
restore_db="ildr_d05_restore_$nonce"
[[ "$restore_schema" =~ ^floor_plan_recovery_[a-f0-9]{32}$ && "$restore_db" =~ ^ildr_d05_restore_[a-f0-9]{32}$ ]] ||
  die EXACT_RESTORE_SCOPE_REQUIRED
expected=$(sed -nE 's/^FLOOR_PLAN_RESTORE_SOURCE draft=([a-f0-9]{64}) published=([a-f0-9]{64})$/\1:\2/p' "$state/native.log")
[[ "$expected" =~ ^[a-f0-9]{64}:[a-f0-9]{64}$ ]] || die RESTORE_FIXTURE_REQUIRED
owned "$pg" && [[ $(bounded 5s docker exec "$pg" ls /sys/class/net) == lo ]] || die LOOPBACK_POSTGRES_REQUIRED
bounded 30s docker exec "$pg" createdb -h 127.0.0.1 -p 55449 -U postgres "$restore_db" \
  >"$state/create-restore.log" 2>&1 || die DISPOSABLE_RESTORE_DATABASE_FAILED
bounded 90s docker exec "$pg" /bin/bash -euo pipefail -c '
  pg_dump -h 127.0.0.1 -p 55449 -U postgres --format=custom --schema="$2" "$1" |
    pg_restore -h 127.0.0.1 -p 55449 -U postgres --exit-on-error --dbname="$3"
' -- "$db" "$restore_schema" "$restore_db" >"$state/archive-restore.log" 2>&1 || die FLOOR_PLAN_ARCHIVE_RESTORE_FAILED
restored=$(bounded 15s docker exec -e "PGOPTIONS=-csearch_path=$restore_schema" "$pg" \
  psql -X -q -At -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 55449 -U postgres -d "$restore_db" -c "
    SELECT string_agg(encode(sha256(payload), 'hex'), ':' ORDER BY stage) || ':' || count(*) || ':' ||
           (SELECT relrowsecurity FROM pg_class WHERE oid = 'listing_floor_plans'::regclass)::text
    FROM listing_floor_plans
    WHERE sha256 = encode(sha256(payload), 'hex') AND octet_length(payload) = byte_count
      AND listing_id IN (SELECT id FROM listings WHERE status = 'active')
      AND ((stage = 'draft' AND approved_at IS NULL)
           OR (stage = 'published' AND approved_at IS NOT NULL))
  " 2>"$state/restore-proof.log") || die RESTORED_FLOOR_PLAN_QUERY_FAILED
[[ "$restored" == "$expected:2:true" ]] || die RESTORED_FLOOR_PLAN_BYTES_OR_PRIVACY_MISMATCH
printf 'FLOOR_PLANS_NATIVE_RESTORE native_pg_dump=true files=2 exact_sha256=true rls=true\n'

marker=$(printf 'FLOOR_PLANS_NATIVE_PASS operations=%s run=%s backend=%s tree=%s fixture_sha256=%s recipe_sha256=%s postgres=170006 tests=4 attempt=1' \
  "$GITHUB_SHA" "$GITHUB_RUN_ID" "$backend_commit" "$backend_tree" "$fixture_hash" "$recipe_hash")
checksum=$(printf '%s\n' "$marker" | sha256sum)
printf '%s\nFLOOR_PLANS_NATIVE_EVIDENCE sha256=%s\n' "$marker" "${checksum%% *}"

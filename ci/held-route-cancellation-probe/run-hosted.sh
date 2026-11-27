#!/usr/bin/env bash
set -euo pipefail
umask 077
die() { printf 'HELD_ROUTE_PROBE_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }
[[ $# == 1 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${GITHUB_REF:-} == refs/heads/codex-ci-held-route-cancellation-probe-20261127 && ${GITHUB_RUN_ATTEMPT:-} == 1 &&
   ${GITHUB_SHA:-} =~ ^[a-f0-9]{40}$ && ${GITHUB_RUN_ID:-} =~ ^[1-9][0-9]{0,19}$ &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 ]] || die EXACT_HOSTED_EVENT_REQUIRED
ops="$1"
[[ "$ops" == "$GITHUB_WORKSPACE/probe-ops" && $(realpath "$ops") == "$ops" && ! -e "$ops/.env" && ! -L "$ops/.env" ]] || die EXACT_SOURCE_PATH_REQUIRED
python3 -I -S -B - "$ops" "$GITHUB_SHA" <<'PY_SOURCE'
from pathlib import Path
import json,subprocess,sys
root=Path(sys.argv[1]);base='4f121400809cf95e9d46c108ae0247b388d9488e'
def git(*a):return subprocess.check_output(['git','-C',str(root),*a])
def tree(ref):return {p.decode():m for m,p in (r.split(b'\t',1) for r in git('ls-tree','-r',ref).splitlines())}
assert not git('status','--porcelain=v1','--untracked-files=all')
assert git('rev-parse','HEAD').decode().strip()==sys.argv[2]
assert git('show','-s','--format=%P','HEAD').decode().strip()==base
assert git('rev-parse',base+'^{tree}').decode().strip()=='df956cd4ea946973ce37fd071d38e04fb6e6de39'
old,current=tree(base),tree('HEAD')
paths={'.github/workflows/held-route-cancellation-probe.yml',*[f'ci/held-route-cancellation-probe/{p}' for p in ('PLAN.json','run-hosted.sh','probe.mjs')]}
assert len(old)==455 and len(current)==459 and current.keys()-old.keys()==paths and all(current[p]==v for p,v in old.items())
assert set(git('diff','--name-only',base,'HEAD').decode().splitlines())==paths
assert set(git('diff','--diff-filter=A','--name-only',base,'HEAD').decode().splitlines())==paths
assert all((root/p).is_file() and not (root/p).is_symlink() for p in paths)
plan=json.loads((root/'ci/held-route-cancellation-probe/PLAN.json').read_bytes())
assert plan['cases']==['abort','reload','close'] and plan['product_acceptance'] is False
print('HELD_ROUTE_PROBE_SOURCE exact=true inherited=455 current=459 cases=3 application=false database=false')
PY_SOURCE
[[ -d ${RUNNER_TEMP:-} && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die OWNED_SCRATCH_REQUIRED
nonce=$(python3 -I -S -B -c 'from uuid import uuid4;print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-cancel-probe.XXXXXXXX")
label=org.ilovedrrealty.held-route-probe
run_key="$GITHUB_RUN_ID-$nonce"
native=''
owned() { [[ "$1" =~ ^[a-f0-9]{64}$ && $(bounded 15s docker inspect --type container --format "{{index .Config.Labels \"$label\"}}" "$1") == "$run_key" ]]; }
cleanup() {
 local code=$? clean=1 ids id
 trap - EXIT
 set +e
 ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
 for id in $ids; do
  if owned "$id"; then bounded 20s docker rm -f -v "$id" >/dev/null 2>&1 || clean=0; else clean=0; fi
 done
 ids=$(bounded 15s docker ps -aq --no-trunc --filter "label=$label=$run_key") || clean=0
 [[ -z "$ids" ]] || clean=0
 if [[ "$state" == "$RUNNER_TEMP"/ildr-cancel-probe.* && -d "$state" && ! -L "$state" && -O "$state" && $(realpath "$state") == "$state" ]]; then
  bounded 30s rm --recursive --force --one-file-system -- "$state" || clean=0
 else clean=0; fi
 [[ "$clean" == 1 && ! -e "$state" ]] || die OWNED_CLEANUP_UNVERIFIED
 printf 'HELD_ROUTE_PROBE_OWNED_CLEANUP operations=%s run=%s attempt=1\n' "$GITHUB_SHA" "$GITHUB_RUN_ID"
 exit "$code"
}
trap cleanup EXIT
mkdir "$state/probe"
cp "$ops/ci/held-route-cancellation-probe/probe.mjs" "$state/probe/probe.mjs"
bounded 90s python3 -I -S -B - "$state/probe" <<'PY_PACKAGE'
import base64,hashlib,io,json,sys,tarfile,urllib.request
from pathlib import Path,PurePosixPath
url='https://registry.npmjs.org/playwright-core/-/playwright-core-1.64.1.tgz'
with urllib.request.urlopen(url,timeout=30) as response:
 assert response.geturl()==url
 raw=response.read(20*1024*1024+1)
assert 0<len(raw)<=20*1024*1024
assert base64.b64encode(hashlib.sha512(raw).digest()).decode()=='BC4s8y6SEVA6Q1rSxZTto8hI1KUI3xXlHGZgvGqP4IqPdI/a6FX9H29IDNz7vKo9l21b6LfYosScPL6M3xxyDQ=='
target=Path(sys.argv[1])/'node_modules/playwright-core';target.mkdir(parents=True)
with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
 members=archive.getmembers();assert len(members)<=6000 and sum(m.size for m in members)<=90*1024*1024
 for member in members:
  parts=PurePosixPath(member.name).parts
  assert parts and parts[0]=='package' and '..' not in parts and (member.isdir() or member.isfile())
  path=target.joinpath(*parts[1:])
  if member.isdir():path.mkdir(parents=True,exist_ok=True)
  else:
   path.parent.mkdir(parents=True,exist_ok=True)
   with path.open('xb') as stream:stream.write(archive.extractfile(member).read())
assert json.loads((target/'package.json').read_bytes())['version']=='1.64.1'
PY_PACKAGE
browser_image='mcr.microsoft.com/playwright@sha256:ee15712262f017b8157421b212b12099146645f44b1bc5c0514cbbe6a229b1d0'
bounded 180s docker pull --platform linux/amd64 "$browser_image" >/dev/null || die PINNED_IMAGE_PULL_REQUIRED
[[ $(bounded 15s docker image inspect --format '{{.Os}}/{{.Architecture}}' "$browser_image") == linux/amd64 ]] || die EXACT_IMAGE_PLATFORM_REQUIRED
native=$(bounded 25s docker create --label "$label=$run_key" --network none --read-only --init \
 --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges:true \
 --pids-limit 256 --cpus 2 --memory 2g --memory-swap 2g --shm-size 256m --tmpfs /tmp:rw,exec,size=256m \
 --mount "type=bind,src=$state/probe,dst=/probe,readonly" "$browser_image" \
 env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
 NODE_OPTIONS=--max-old-space-size=1536 node /probe/probe.mjs) || die PROBE_CONTAINER_CREATE_REQUIRED
owned "$native" && [[ $(bounded 15s docker inspect --format '{{.HostConfig.NetworkMode}}' "$native") == none ]] || die OWNED_NETWORK_NONE_REQUIRED
client=0
bounded 90s docker start --attach "$native" >"$state/probe.log" 2>&1 || client=$?
read -r exit_code running oom <<<"$(bounded 15s docker inspect --format '{{.State.ExitCode}} {{.State.Running}} {{.State.OOMKilled}}' "$native")"
[[ "$client" =~ ^[0-9]{1,3}$ && "$exit_code" =~ ^[0-9]{1,3}$ && "$running" =~ ^(true|false)$ && "$oom" =~ ^(true|false)$ ]] || die TYPED_PROCESS_REQUIRED
printf 'HELD_ROUTE_PROBE_PROCESS client=%s native=%s running=%s oom=%s\n' "$client" "$exit_code" "$running" "$oom"
if [[ "$running" == true ]]; then bounded 20s docker stop --time 10 "$native" >/dev/null || die STOP_REQUIRED; fi
[[ $(bounded 10s docker inspect --format '{{.State.Running}}' "$native") == false ]] || die STOPPED_REQUIRED
python3 -I -S -B - "$state/probe.log" "$client" "$exit_code" "$running" "$oom" <<'PY_RESULT'
from pathlib import Path
import json,re,sys
def require(value):
 if not value:raise ValueError('invalid_probe')
def pairs(values):
 out={}
 for k,v in values:require(k not in out);out[k]=v
 return out
def integer(v,low,high):return type(v) is int and low<=v<=high
def parse(raw):
 require(type(raw) is bytes and len(raw)<=32768 and b'\0' not in raw)
 records=[]
 for line in raw.decode('utf-8').splitlines():
  require(len(line.encode())<=1024)
  match=re.fullmatch(r'HELD_ROUTE_PROBE_(CASE|RESULT|FAILURE) (.+)',line);require(match is not None)
  kind,value=match[1],json.loads(match[2],object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
  require(type(value) is dict)
  if kind=='CASE':
   keys={'case','real_status','server_fetches','original_page','before_failed','before_page_failed','before_failure','before_finished','after_failed','after_page_failed','after_failure','after_finished','after_response_status','fulfill','handler_done','response_disposed','native_abort','action_settled_before_release','old_page_closed','document_retired','new_page','stale_value_present','before_observation_ms','after_observation_ms'}
   require(set(value)==keys and value['case'] in ('abort','reload','close'))
   require(type(value['real_status']) is int and value['real_status']==200 and type(value['server_fetches']) is int and value['server_fetches']==1)
   for key in ('before_failed','before_page_failed','before_finished','after_failed','after_page_failed','after_finished'):require(integer(value[key],0,1))
   for key in ('original_page','before_failure','after_failure','handler_done','response_disposed','action_settled_before_release','old_page_closed','document_retired','new_page','stale_value_present'):require(type(value[key]) is bool)
   require(value['original_page'] and value['handler_done'] and value['response_disposed'] and not value['stale_value_present'])
   require(value['old_page_closed']==value['new_page']==(value['case']=='close') and value['document_retired']==(value['case']!='abort'))
   require(value['native_abort'] is True if value['case']=='abort' else value['native_abort'] is None)
   require(value['after_response_status'] is None or type(value['after_response_status']) is int and value['after_response_status']==200)
   require(type(value['fulfill']) is str and value['fulfill'] in ('not_attempted_failed','returned','target_closed','other_error'))
   require(integer(value['before_observation_ms'],0,20000) and integer(value['after_observation_ms'],0,20000))
   require(value['after_failed']>=value['before_failed'] and value['after_page_failed']>=value['before_page_failed'] and value['after_finished']>=value['before_finished'])
  elif kind=='RESULT':
   expected={'status':'OBSERVED','cases':3,'contexts_closed':3,'server_fetches':3,'external_requests':0,'browser_closed':True,'servers_closed':2,'product_acceptance':False}
   require(set(value)==set(expected) and all(type(value[k]) is type(v) and value[k]==v for k,v in expected.items()))
  else:
   require(set(value)=={'stage','reason'} and value['stage'] in ('setup','abort','reload','close','cleanup') and value['reason'] in ('deadline','invariant_or_lifecycle'))
  records.append((kind,value));require(len(records)<=5)
 return records
def accepted(records,client,native,running,oom):
 require(type(client) is int and type(native) is int and 0<=client<=255 and 0<=native<=255 and type(running) is bool and type(oom) is bool)
 return (client==native==0 and not running and not oom
  and [v['case'] for k,v in records if k=='CASE']==['abort','reload','close']
  and [k for k,_ in records]==['CASE','CASE','CASE','RESULT'])
try:
 require(len(sys.argv)==6 and all(re.fullmatch(r'(?:0|[1-9][0-9]{0,2})',v) for v in sys.argv[2:4]) and all(v in ('true','false') for v in sys.argv[4:]))
 path=Path(sys.argv[1]);require(path.is_file() and not path.is_symlink() and path.stat().st_size<=32768)
 records=parse(path.read_bytes())
 for kind,value in records:print('HELD_ROUTE_PROBE_VALIDATED_'+kind+' '+json.dumps(value,sort_keys=True,separators=(',',':')))
 require(accepted(records,int(sys.argv[2]),int(sys.argv[3]),sys.argv[4]=='true',sys.argv[5]=='true'))
 print('HELD_ROUTE_PROBE_OBSERVATION_COMPLETE product_acceptance=false')
except Exception:
 print('HELD_ROUTE_PROBE_CHECK_FAILED reason=INVALID_OR_INCOMPLETE_OBSERVATION')
 raise SystemExit(1) from None
PY_RESULT

#!/usr/bin/env bash
# Exact sources; one immutable build; fresh owned DB and private fixture per phase.
set -euo pipefail
umask 077
die() { printf 'SIGNUP_HISTORY_REFUSED %s\n' "$1" >&2; exit 1; }
bounded() { timeout --kill-after=8s "$@"; }
[[ $# == 8 && ${GITHUB_ACTIONS:-} == true && ${GITHUB_REPOSITORY:-} == ilovedrrealty/realty-api &&
   ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux && ${RUNNER_ARCH:-} == X64 &&
   ${GITHUB_RUN_ATTEMPT:-} == 1 && ${GITHUB_REF:-} == refs/heads/codex-ci-signup-email-history-browser-v4-20261126 &&
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
.github/workflows/signup-email-history-browser.yml
ci/signup-email-history-browser/PLAN.json
ci/signup-email-history-browser/run-hosted.sh
EXACT_OPERATIONS
)
operations_delta=$(git -C "$ops" diff-tree --no-commit-id --name-only -r HEAD^ HEAD) || die OPERATIONS_DIFF_REQUIRED
[[ $(git -C "$ops" rev-parse HEAD) == "$GITHUB_SHA" &&
   $(git -C "$ops" show -s --format=%P HEAD) == 1bea5657249790d13ccd3cb2a3afb514ecb66481 &&
   "$operations_delta" == "$expected_operations" ]] || die EXACT_OPERATIONS_REQUIRED
python3 -I -B - "$ops" <<'PY_OPERATIONS_REUSE'
from pathlib import Path
import subprocess,sys
root=Path(sys.argv[1])
def git(*args):return subprocess.check_output(['git','-C',str(root),*args])
def tree(commit):return dict((line.split(b'\t',1)[1].decode(),line.split(b'\t',1)[0]) for line in git('ls-tree','-rz',commit).split(b'\0') if line)
old,new=tree('HEAD^'),tree('HEAD')
paths={'.github/workflows/signup-email-history-browser.yml','ci/signup-email-history-browser/PLAN.json','ci/signup-email-history-browser/run-hosted.sh'}
assert len(old)==len(new)==431 and old.keys()==new.keys()
assert {name for name in old if old[name]!=new[name]}==paths
assert all(new[name]==value for name,value in old.items() if name not in paths)
for name in paths:assert (root/name).is_file() and not (root/name).is_symlink()
for name in ('ci/signup-email-history-browser/serve_fixture.py','ci/signup-email-history-browser/run-browser.mjs','ci/signup-email-history-browser/check-result.py'):
    assert old[name]==new[name]
PY_OPERATIONS_REUSE
backend_commit=26ca867a1008868f7dc5f322d28dc289a7763980
backend_tree=3a30b7e8c5b53827b97374c18806a7cb03ef9c5c
frontend_commit=2e1fce3cb9373724c9c34cecf5ab43f2451bed40
frontend_tree=34ea2cfc7810d13c7c40c29ac5a26ed032f2456a
frontend_paths=622
[[ $(git -C "$backend" rev-parse HEAD) == "$backend_commit" && $(git -C "$backend" rev-parse HEAD^{tree}) == "$backend_tree" &&
   $(git -C "$frontend" rev-parse HEAD) == "$frontend_commit" && $(git -C "$frontend" rev-parse HEAD^{tree}) == "$frontend_tree" &&
   $(git -C "$frontend" show -s --format=%P HEAD) == de4d2b21f8e08720211407eb1449d6f85cfa9b34 &&
   $(git -C "$frontend" show -s --format=%P HEAD^) == 95077fb8e5cb784ed47be6a78d9632e62a0b9bf4 &&
   $(git -C "$frontend" show -s --format=%P HEAD^^) == a8069c847a75823fc5a62101895a80d8fc1f8b1d &&
   $(git -C "$frontend" show -s --format=%P a8069c847a75823fc5a62101895a80d8fc1f8b1d) == "ee32af9f5c0db6f79412c7c089e7f9f7143f42d3 0bc3fc968199490344c79c9cbed44afb0875eb18" &&
   $(git -C "$frontend" show -s --format=%P 5ecb7c6e16268f1e8edd1b54d70f11e605e57a02) == "bb4a9c626fb96f93ae6d6bc55b5a2a67f85411ba 44a3fdae157039f42fad562bde281f7205ca19f3" &&
   $(git -C "$baseline" rev-parse HEAD) == 01695530df670a94ac28f8b3bf36cc34b5b7a942 &&
   $(git -C "$baseline" rev-parse HEAD^{tree}) == e88ea0c57f9fac39545322532a5992d5a2927676 &&
   $(git -C "$migration" rev-parse HEAD) == c193a925ba57ea2d67ea48c44e5262817885c8a2 &&
   $(git -C "$relay" rev-parse HEAD) == 3007728fd67c5889a8373e41ff3ffa5cc50d839b &&
   $(git -C "$relay" rev-parse HEAD^{tree}) == 80868dce147c2cd4609e303493669d0a19f09741 &&
   $(git -C "$previous" rev-parse HEAD) == 8957b18520d15dac503e0e4a5ce4b2455135d601 &&
   $(git -C "$previous" rev-parse HEAD^{tree}) == 11ab69db36eb30881e4f3086578201f584c4092d &&
   $(git -C "$reference" rev-parse HEAD) == 17943ba1714673c51c43cdbb800106f91612f527 && $(git -C "$reference" rev-parse HEAD^{tree}) == ff940a7b6e983d4c669202746d9676b4b82e9a06 ]] || die EXACT_SOURCES_REQUIRED
[[ $(git -C "$backend" show -s --format=%P HEAD) == c4de3e2e25fd3699ccb776c740d6413b402ed317 &&
   $(git -C "$backend" show -s --format=%P HEAD^) == 6ef69b249c573eedaea962dad530eaed0a612b24 &&
   $(git -C "$frontend" rev-parse '06d7f74965896ae23d47e7701fc1dbfd9815cc40^{commit}') == 06d7f74965896ae23d47e7701fc1dbfd9815cc40 ]] || die EXACT_PARENTS_AND_REVIEW_BASE_REQUIRED
git -C "$frontend" merge-base --is-ancestor 06d7f74965896ae23d47e7701fc1dbfd9815cc40 HEAD || die REVIEW_BASE_ANCESTRY_REQUIRED
git -C "$backend" cat-file -e '8957b18520d15dac503e0e4a5ce4b2455135d601^{commit}' || die BACKEND_BASE_REQUIRED
git -C "$backend" merge-base --is-ancestor 8957b18520d15dac503e0e4a5ce4b2455135d601 HEAD || die BACKEND_BASE_ANCESTRY_REQUIRED
backend_dependency_delta=$(git -C "$backend" diff --name-only 8957b18520d15dac503e0e4a5ce4b2455135d601 HEAD -- requirements.txt) || die BACKEND_DEPENDENCY_DIFF_REQUIRED
frontend_manifest_delta=$(git -C "$frontend" diff --name-only 06d7f74965896ae23d47e7701fc1dbfd9815cc40 HEAD -- package.json package-lock.json apps/dashboard/package.json apps/landing/package.json) || die FRONTEND_MANIFEST_DIFF_REQUIRED
[[ -z "$backend_dependency_delta" && -z "$frontend_manifest_delta" ]] || die UNCHANGED_DEPENDENCIES_AND_MANIFESTS_REQUIRED
python3 -I -B - "$backend" <<'PY_BACKEND_COMPOSITION'
from pathlib import Path
from hashlib import sha256
import subprocess,sys
root=Path(sys.argv[1])
base='8957b18520d15dac503e0e4a5ce4b2455135d601'
inbox='79e9311874e3ea91e45b990629965534d41bb5fa'
resilience='07b2b7c762b129c5b6ea978299ba69e528e3189e'
def git(*args):return subprocess.check_output(['git','-C',str(root),*args])
def changed(commit):return set(git('diff','--name-only',base,commit).decode().splitlines())
def tree(commit):
    result={}
    for line in git('ls-tree','-rz',commit).split(b'\0'):
        if line:
            value,name=line.split(b'\t',1);result[name.decode()]=value
    return result
paths={'alembic/versions/d8f4c2a91e67_private_native_inbox.py','database.py','docs/NATIVE-INBOX-CONTRACT.md',
    'main.py','models/__init__.py','models/native_inbox.py','routes/native_inbox.py','schemas/native_inbox.py',
    'services/native_inbox.py','tests/test_database_connection_options.py','tests/test_database_pool_recovery.py',
    'tests/test_native_inbox.py','utils/database_connection.py'}
left,right=changed(inbox),changed(resilience)
assert len(left)==9 and len(right)==4 and not left&right and left|right==paths==changed('17943ba1714673c51c43cdbb800106f91612f527')
expected=tree(base);a,b=tree(inbox),tree(resilience)
for path in left:expected[path]=a[path]
for path in right:expected[path]=b[path]
assert len(expected)==365 and tree('17943ba1714673c51c43cdbb800106f91612f527')==expected
profile_paths={'alembic/versions/e8b5a3d1f907_private_profile_visuals.py','models/property_network.py','routes/property_network.py','schemas/property_network.py','services/property_network.py','tests/test_profile_visual_inputs.py','tests/test_property_network.py'}
assert set(git('diff','--name-only','17943ba1714673c51c43cdbb800106f91612f527','a75f9c26f3bceb14afcb6e1150e1b2a3d50fbada').decode().splitlines())==profile_paths
current=tree('a75f9c26f3bceb14afcb6e1150e1b2a3d50fbada')
for path in profile_paths:expected[path]=current[path]
assert len(expected)==367 and current==expected
# Current Group opt-in source changes no schema or original profile projection.
group_pins={'routes/social_groups.py': '3f10962199d3a6366e65d12d15e82986efb783e699c835f73fcaae8f1b66758d', 'services/social_groups.py': 'd36e27ed1ec0ca1ca9324d8a99ae8ebcf0d665d6c33a8cb81eb525eb400f4465', 'tests/test_social_groups.py': '8560c595f52fc914a9f8f3d00c1323c87f9d2daf273a9ad1f0945ed94a04a858'}
assert git('show','-s','--format=%P','6ef69b249c573eedaea962dad530eaed0a612b24').decode().strip()=='a75f9c26f3bceb14afcb6e1150e1b2a3d50fbada'
assert set(git('diff','--name-only','a75f9c26f3bceb14afcb6e1150e1b2a3d50fbada','6ef69b249c573eedaea962dad530eaed0a612b24').decode().splitlines())==set(group_pins)
current=tree('6ef69b249c573eedaea962dad530eaed0a612b24')
for name,digest in group_pins.items():
    assert sha256(git('show','6ef69b249c573eedaea962dad530eaed0a612b24:'+name)).hexdigest()==digest
    expected[name]=current[name]
assert len(expected)==367 and current==expected
# Only the reviewed signup-history source overlays the proven accepted parent.
history_pins={'routes/lead_delivery.py': '4eb3f0069ea618202f08ae2e737fe7d2041ebf06305ad6e861b52f0a7256c25a', 'tests/test_customer_delivery.py': 'a7412acd4c9a2941e2ce0027d09095492e8cf408f6b95d9c0432b2bd335e87c6'}
history_parent=tree('6ef69b249c573eedaea962dad530eaed0a612b24');history_current=tree('c4de3e2e25fd3699ccb776c740d6413b402ed317')
assert len(history_parent)==367 and len(history_current)==367
assert {k for k in set(history_parent)|set(history_current) if history_parent.get(k)!=history_current.get(k)}==set(history_pins)
assert all(history_current[k]==v for k,v in history_parent.items() if k not in history_pins)
for name,digest in history_pins.items():assert sha256(git('show','c4de3e2e25fd3699ccb776c740d6413b402ed317:'+name)).hexdigest()==digest

# Exact one-keyword test-only child; every app/schema/dependency blob is retained.
assert git('show','-s','--format=%P','HEAD').decode().strip()=='c4de3e2e25fd3699ccb776c740d6413b402ed317'
assert git('rev-parse','HEAD^{tree}').decode().strip()=='3a30b7e8c5b53827b97374c18806a7cb03ef9c5c'
fixed=tree('HEAD');prior=tree('c4de3e2e25fd3699ccb776c740d6413b402ed317')
assert len(fixed)==len(prior)==367 and fixed.keys()==prior.keys()
assert {name for name in prior if fixed[name]!=prior[name]}=={'tests/test_customer_delivery.py'}
assert all(fixed[name]==value for name,value in prior.items() if name!='tests/test_customer_delivery.py')
body=git('show','HEAD:tests/test_customer_delivery.py')
assert sha256(body).hexdigest()=='5827f87dc3901fd51e69f652caeef31f5b983a4c69ced74ae59d393d3ef1934a'
addition=b', display_name=f"Synthetic {role} reader"'
assert body.count(addition)==1 and body.replace(addition,b'',1)==git('show','c4de3e2e25fd3699ccb776c740d6413b402ed317:tests/test_customer_delivery.py')

PY_BACKEND_COMPOSITION
python3 -I -B - "$frontend" <<'PY_FRONTEND_COMPOSITION'
from pathlib import Path
from hashlib import sha256
import subprocess,sys
root=Path(sys.argv[1])
def git(*args):return subprocess.check_output(['git','-C',str(root),*args])
def tree(commit):
    return dict((line.split(b'\t',1)[1].decode(),line.split(b'\t',1)[0]) for line in git('ls-tree','-rz',commit).split(b'\0') if line)
ancestor='6ca7880c5b154a9c370ea47bffd00d4d89838c4f'
left='bb4a9c626fb96f93ae6d6bc55b5a2a67f85411ba'
right='44a3fdae157039f42fad562bde281f7205ca19f3'
a,l,r=map(tree,(ancestor,left,right))
profile_paths={'apps/dashboard/src/locale/en/property_network.json', 'apps/dashboard/src/components/network/MemberCommunity.tsx', 'apps/dashboard/src/components/network/ProfileVisuals.tsx', 'apps/dashboard/src/locale/es/property_network.json', 'apps/dashboard/src/api/propertyNetwork.ts', 'tests/property-network.test.cjs', 'apps/dashboard/src/components/network/ProfileCustomization.tsx', 'apps/dashboard/src/pages/shared/PropertyNetwork.tsx', 'apps/dashboard/src/components/network/ProfileAuthor.tsx', 'apps/dashboard/src/components/network/NetworkShared.tsx', 'apps/dashboard/src/components/network/ProfileWork.tsx', 'tests/property-network-ui.test.cjs'}
css_paths={'apps/dashboard/src/pages/shared/ResearchTools.tsx','apps/landing/src/views/Calculator.tsx'}
assert set(git('diff','--name-only',ancestor,right).decode().splitlines())==profile_paths and len(profile_paths)==12
assert set(git('diff','--name-only',ancestor,left).decode().splitlines())==css_paths and not profile_paths&css_paths
expected=dict(a)
for name in profile_paths:expected[name]=r[name]
for name in css_paths:expected[name]=l[name]
combined='5ecb7c6e16268f1e8edd1b54d70f11e605e57a02'
assert len(a)==616 and len(l)==616 and len(r)==620 and tree(combined)==expected
assert set(git('diff','--name-only',left,combined).decode().splitlines())==profile_paths
assert len(git('diff','--name-only','6015987e33375ed012996dddb27ca092532d793c',combined).decode().splitlines())==28
# Extend the preserved exact original merge with only the two reviewed test adapters.
fix1='09bfd9f54ec8bca8a1d0488a571d8dbac0df2743'
fix2='3b7d8190951fcdca574d7af98bb1bbf62c97e764'
assert git('show','-s','--format=%P',fix1).decode().strip()==combined
assert git('show','-s','--format=%P',fix2).decode().strip()==fix1
assert git('rev-parse',fix2+'^{tree}').decode().strip()=='c18447b2c1c11d5761e8c22831625a3dd5e3e7ec'
fix_pins={'tests/realtor-sale-catalog.test.cjs':'b2a45690dd716d6ba0bb6b1126ca4e20ff32a48a73938d52bbf4db262d3d33d7',
          'tests/service-operations.test.cjs':'4a42d9a0a5d84b2d59c0802d7891b72e2c71b58d76d45fa5864cbad41cd1aa0f'}
assert set(git('diff','--name-only',combined,fix1).decode().splitlines())==set(fix_pins)
assert set(git('diff','--name-only',fix1,fix2).decode().splitlines())=={'tests/realtor-sale-catalog.test.cjs'}
assert set(git('diff','--name-only',combined,fix2).decode().splitlines())==set(fix_pins)
current=tree(fix2)
for name,digest in fix_pins.items():
    oid=git('rev-parse',fix2+':'+name).decode().strip()
    assert sha256(git('cat-file','blob',oid)).hexdigest()==digest
    expected[name]=current[name]
assert len(expected)==620 and current==expected
# Exact reviewed sibling-key namespaces and one actual-parent regression.
fix3='bb79bcefb016309f77deea7695bdf65d3bcf1c8b'
assert git('show','-s','--format=%P',fix3).decode().strip()==fix2
profile_pins={'apps/dashboard/src/components/network/MemberCommunity.tsx':'42827187beaa5019539a6a2426bf4abbb9b84971350ebb53d345b283ac7dd77c',
              'tests/property-network-ui.test.cjs':'62dc5a256533675f503eebf85a66a4fbe22a5d82431fa75c40b1ad4810074a49'}
assert set(git('diff','--name-only',fix2,fix3).decode().splitlines())==set(profile_pins)
current=tree(fix3)
for name,digest in profile_pins.items():
    assert sha256(git('show',fix3+':'+name)).hexdigest()==digest
    expected[name]=current[name]
assert len(expected)==620 and current==expected
# Exact reviewed five Group-workspace sibling key namespaces + one parent regression.
fix4='ee32af9f5c0db6f79412c7c089e7f9f7143f42d3'
assert git('show','-s','--format=%P',fix4).decode().strip()==fix3
child_pins={'apps/dashboard/src/components/network/GroupWorkspace.tsx':'3020da0bbc78a35e920e3625bbf9e41adc0f0434deaf97cb048212439e2a235c','tests/property-network-ui.test.cjs':'10e0580f8268432947ba567ab612dcc0da57046c9bf7d3a903db16b0624e3f14'}
assert set(git('diff','--name-only',fix3,fix4).decode().splitlines())==set(child_pins)
current=tree(fix4)
for name,digest in child_pins.items():
    assert sha256(git('show',fix4+':'+name)).hexdigest()==digest
    expected[name]=current[name]
assert current==expected and len(expected)==620
# Final reviewed overlap merge: nine exclusive parent blobs and one exact UI union.
group='0bc3fc968199490344c79c9cbed44afb0875eb18'
assert git('show','-s','--format=%P',group).decode().strip()=='ddd1190b7c8dcd6cc84d275ec63efeeab4bb8828'
assert git('show','-s','--format=%P','ddd1190b7c8dcd6cc84d275ec63efeeab4bb8828').decode().strip()==combined
assert git('rev-parse',group+'^{tree}').decode().strip()=='d1cdfec6cfe69aa02c6a5cbb760796375518cb70'
assert git('show','-s','--format=%P','a8069c847a75823fc5a62101895a80d8fc1f8b1d').decode().strip()==fix4+' '+group
exclusive={'apps/dashboard/src/api/socialGroups.ts': ('0bc3fc968199490344c79c9cbed44afb0875eb18', '1f2c4513f5a10009366b3bda6d3f012790c5b688f8b2260f0e02529cb1957d01'), 'apps/dashboard/src/components/network/GroupFeed.tsx': ('0bc3fc968199490344c79c9cbed44afb0875eb18', '31d06471196994fe11c2fc7f6299b2f8825be228da6d8635429e441415ce6d10'), 'apps/dashboard/src/components/network/GroupWorkspace.tsx': ('ee32af9f5c0db6f79412c7c089e7f9f7143f42d3', '3020da0bbc78a35e920e3625bbf9e41adc0f0434deaf97cb048212439e2a235c'), 'apps/dashboard/src/components/network/MemberCommunity.tsx': ('ee32af9f5c0db6f79412c7c089e7f9f7143f42d3', '42827187beaa5019539a6a2426bf4abbb9b84971350ebb53d345b283ac7dd77c'), 'apps/dashboard/src/locale/en/property_network.json': ('0bc3fc968199490344c79c9cbed44afb0875eb18', '21295196c6cc4529c92975768c21ba7c75324c70c7b68aaf6d969b4d0cad64e1'), 'apps/dashboard/src/locale/es/property_network.json': ('0bc3fc968199490344c79c9cbed44afb0875eb18', '0b6792d51c989a58f215a107a0e0a819d6b0a9ee6cf814c25368c9b846a3ea50'), 'tests/realtor-sale-catalog.test.cjs': ('ee32af9f5c0db6f79412c7c089e7f9f7143f42d3', 'b2a45690dd716d6ba0bb6b1126ca4e20ff32a48a73938d52bbf4db262d3d33d7'), 'tests/service-operations.test.cjs': ('ee32af9f5c0db6f79412c7c089e7f9f7143f42d3', '4a42d9a0a5d84b2d59c0802d7891b72e2c71b58d76d45fa5864cbad41cd1aa0f'), 'tests/social-groups.test.cjs': ('0bc3fc968199490344c79c9cbed44afb0875eb18', '2dd092bccf9c50cdf09ae5a5b9d6dfe1f7b6d6ceb50cdb3177b25cc5b1d7a803')}
final=tree('a8069c847a75823fc5a62101895a80d8fc1f8b1d');group_tree=tree(group);common_tree=tree(combined)
union=set(git('diff','--name-only',combined,fix4).decode().splitlines())|set(git('diff','--name-only',combined,group).decode().splitlines())
assert len(union)==10 and set(git('diff','--name-only',combined,'a8069c847a75823fc5a62101895a80d8fc1f8b1d').decode().splitlines())==union
assert len(exclusive)==9 and set(exclusive)==union-{'tests/property-network-ui.test.cjs'}
expected=dict(common_tree)
for name,(source,digest) in exclusive.items():
    source_tree=tree(source)
    assert final[name]==source_tree[name] and sha256(git('show',source+':'+name)).hexdigest()==digest
    expected[name]=source_tree[name]
ui='tests/property-network-ui.test.cjs'
ui_bytes=git('show','a8069c847a75823fc5a62101895a80d8fc1f8b1d:'+ui)
assert sha256(ui_bytes).hexdigest()=='c198eacca873af98873775c768cb609b43471dc65c358480f0d85a3441db38e3'
marker=b"test('actual group post and comment keep group aliases when secondary chosen pictures are withdrawn'"
def author_case(data):
    start=data.index(marker);end=data.index(b'\ntest(',start+len(marker))+1
    return data[start:end]
case=author_case(git('show',group+':'+ui))
assert ui_bytes.count(case)==1 and ui_bytes.replace(case,b'',1)==git('show',fix4+':'+ui)
expected[ui]=final[ui]
assert len(expected)==620 and final==expected and all(final[name]==value for name,value in common_tree.items() if name not in union)
# Only the reviewed signup-history source overlays the proven accepted parent.
history_pins={'apps/dashboard/src/api/communications.ts': '82f38392b480ee92fe0baeca2a27b2b9f4e779e4f452533a66c63431627b51fe', 'apps/dashboard/src/components/admin/SignupCommunicationHistory.tsx': '2302401d3d21661641dff010ba6a81a413b736e56de35d0f025fec6c473b1bd4', 'apps/dashboard/src/components/admin/UserDetailPanel.tsx': '7d8c5b9ae5569b1be473adb73ee6039cb6a79cc78014250331b89946ac6913b7', 'apps/dashboard/src/locale/en/communications.json': 'b1d38157e414e2463c2b5c3f69e270470376492a3834254ff695098e0897c97f', 'apps/dashboard/src/locale/es/communications.json': 'babc8466d2a2c6d7f3b8fbd76aa5c1ce12c0a59fea7041e1c517027ed4c0c556', 'tests/signup-history.test.cjs': 'dffb6e496292626799f029b9e4e549349e51c346a59334bc88ddf79d5055156d'}
history_parent=tree('a8069c847a75823fc5a62101895a80d8fc1f8b1d');history_current=tree('de4d2b21f8e08720211407eb1449d6f85cfa9b34')
assert len(history_parent)==620 and len(history_current)==622
assert {k for k in set(history_parent)|set(history_current) if history_parent.get(k)!=history_current.get(k)}==set(history_pins)
assert all(history_current[k]==v for k,v in history_parent.items() if k not in history_pins)
for name,digest in history_pins.items():assert sha256(git('show','de4d2b21f8e08720211407eb1449d6f85cfa9b34:'+name)).hexdigest()==digest
assert git('show','-s','--format=%P','de4d2b21f8e08720211407eb1449d6f85cfa9b34').decode().strip()=='95077fb8e5cb784ed47be6a78d9632e62a0b9bf4'
assert git('show','-s','--format=%P','95077fb8e5cb784ed47be6a78d9632e62a0b9bf4').decode().strip()=='a8069c847a75823fc5a62101895a80d8fc1f8b1d'
intermediate=tree('95077fb8e5cb784ed47be6a78d9632e62a0b9bf4')
child_paths={'apps/dashboard/src/components/admin/SignupCommunicationHistory.tsx','apps/dashboard/src/locale/en/communications.json','apps/dashboard/src/locale/es/communications.json','tests/signup-history.test.cjs'}
assert len(intermediate)==622 and {k for k in intermediate if intermediate[k]!=history_current[k]}==child_paths

# Exact responsive toolbar-only child; all other621 source tuples are retained.
assert git('show','-s','--format=%P','HEAD').decode().strip()=='de4d2b21f8e08720211407eb1449d6f85cfa9b34'
assert git('rev-parse','HEAD^{tree}').decode().strip()=='34ea2cfc7810d13c7c40c29ac5a26ed032f2456a'
prior=tree('de4d2b21f8e08720211407eb1449d6f85cfa9b34');toolbar=tree('HEAD');name='apps/dashboard/src/pages/admin/Users.tsx'
assert len(prior)==len(toolbar)==622 and prior.keys()==toolbar.keys()
assert {path for path in prior if prior[path]!=toolbar[path]}=={name}
assert all(toolbar[path]==value for path,value in prior.items() if path!=name)
body=git('show','HEAD:'+name)
assert sha256(body).hexdigest()=='b75e7d70349ca7998ee95f08aae8c9c4197550f749181f28a0c83f7c505d048b'
reversals=[
 (b'flex w-full min-w-0 flex-col items-stretch gap-2 sm:w-auto sm:flex-row sm:items-center',b'flex items-center gap-2'),
 (b'flex w-full min-w-0 items-center gap-2 px-3 py-1.5 rounded-lg border border-line bg-white sm:w-55',b'flex items-center gap-2 px-3 py-1.5 rounded-lg border border-line bg-white w-55'),
 (b'text-xs border-0 outline-none bg-transparent text-ink placeholder:text-dim flex-1 min-w-0',b'text-xs border-0 outline-none bg-transparent text-ink placeholder:text-dim flex-1')]
for current,old in reversals:
    assert body.count(current)==1
    body=body.replace(current,old,1)
assert body==git('show','de4d2b21f8e08720211407eb1449d6f85cfa9b34:'+name)

PY_FRONTEND_COMPOSITION
printf 'SIGNUP_HISTORY_BUILD_SCOPE current_tests=555 current_files=66 current_standalone_typecheck=required fresh_dashboard_build=required fresh_landing_build=required native162=retained_unchanged_sources profile24_32=retained_unchanged_components group12_16=retained_unchanged_components native21=retained_actual_pass\n'
python3 -I -B - "$baseline/requirements.txt" "$backend/requirements.txt" <<'PY_REQUIREMENTS'
from pathlib import Path
import sys
assert Path(sys.argv[2]).read_bytes()==Path(sys.argv[1]).read_bytes()+b'openpyxl==3.1.5\ndefusedxml==0.7.1\net-xmlfile==2.0.0\n'
PY_REQUIREMENTS
[[ -d ${RUNNER_TEMP:-} && $(realpath "$RUNNER_TEMP") == "$RUNNER_TEMP" ]] || die HOST_SCRATCH_REQUIRED
nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
[[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || die EXACT_NONCE_REQUIRED
state=$(mktemp -d "$RUNNER_TEMP/ildr-integrated.XXXXXXXX")
label='org.ilovedrrealty.signup.email.history.acceptance'
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
assert len(list(Path(sys.argv[1]).glob('*.test.cjs')))==66
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

native=0
bounded 360s docker run "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1536 \
  sh -c 'node --test --test-reporter=tap tests/*.test.cjs' >"$state/contracts.log" 2>&1 || native=$?
python3 -I -B - "$state/contracts.log" "$native" <<'PY_CONTRACTS' || die ACTUAL_555_CONTRACTS_REQUIRED
from pathlib import Path
import re,sys
raw=Path(sys.argv[1]).read_bytes()
assert len(raw)<=4*1024*1024
counts={key.decode():int(value) for key,value in re.findall(rb'(?m)^# (tests|pass|fail|cancelled|skipped|todo) ([0-9]+)\s*$',raw)}
print('SIGNUP_HISTORY_FRONTEND_CONTRACTS native='+sys.argv[2]+' '+str(counts))
if sys.argv[2]!='0':
    for name,line,col in re.findall(rb"location: '/work/(tests/[A-Za-z0-9_.-]+\.cjs):([0-9]+):([0-9]+)'",raw)[:12]:
        print('SIGNUP_HISTORY_CONTRACT_LOCATION '+name.decode()+':'+line.decode()+':'+col.decode())
assert sys.argv[2]=='0' and counts=={'tests':555,'pass':555,'fail':0,'cancelled':0,'skipped':0,'todo':0}
PY_CONTRACTS
verify_source || die CONTRACTS_ALTERED_FRONTEND_SOURCE
native=0
bounded 360s docker run "${prepare[@]}" --network none \
  --mount "type=bind,src=$state/frontend-work,dst=/work" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp CI=true NODE_OPTIONS=--max-old-space-size=1536 \
  sh -c 'npm run typecheck --workspace=dashboard' >"$state/strict.log" 2>&1 || native=$?
python3 -I -B - "$state/strict.log" "$native" <<'PY_STRICT' || die ACTUAL_STRICT_DASHBOARD_REQUIRED
from pathlib import Path
import re,sys
raw=Path(sys.argv[1]).read_bytes()
assert len(raw)<=1048576
for file,line,col,code in re.findall(rb'(?m)^((?:src|\.\./\.\./packages)/[A-Za-z0-9_./-]+\.(?:ts|tsx))\(([0-9]{1,5}),([0-9]{1,5})\): error (TS[0-9]{4}):',raw)[:12]:
    print('SIGNUP_HISTORY_STRICT_DIAGNOSTIC '+file.decode()+':'+line.decode()+':'+col.decode()+' '+code.decode())
print('SIGNUP_HISTORY_STRICT_DASHBOARD native='+sys.argv[2])
assert sys.argv[2]=='0'
PY_STRICT
verify_source || die STRICT_ALTERED_FRONTEND_SOURCE

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
print("SIGNUP_HISTORY_DASHBOARD native=0 build=success standalone_typecheck=separate_current_phase")
PY
verify_source || die DASHBOARD_ALTERED_SOURCE

tools="$ops/ci/signup-email-history-browser"
diagnostic_tools="$tools"
driver_tools="$tools"
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
cp "$driver_tools/run-browser.mjs" "$state/browser-run/run-browser.mjs"
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
    (browser/f'{language}.json').write_text(json.dumps({'admin':copy('admin'),'nav':copy('nav'),'communications':copy('communications'),'auth':json.loads((workspace/f'apps/landing/src/locales/{language}/auth.json').read_bytes())},ensure_ascii=False),encoding='utf-8')
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
# Actual same-source native21 model-schema/route proof; overall prior browser run failed.
printf 'SIGNUP_HISTORY_RETAINED_NATIVE tested_commit=26ca867a1008868f7dc5f322d28dc289a7763980 tested_tree=3a30b7e8c5b53827b97374c18806a7cb03ef9c5c tests=21 errors=0 failures=0 skipped=0 providers=0 current_native_tests=false run=45914574770 job=140230615311 primary_sha256=4246446edd27d15f895a679e9f5eb93a4b71e9f1d9268b4661ff0ca50b21e4d8 independent_phase_receipt_sha256=167b65b4918f3c091396b85155cf3fabab1ea00928a52c3f9403b48c7267d754\n'

dashboard=$(bounded 25s docker create "${runtime[@]}" --memory 192m --memory-swap 192m \
  --tmpfs /tmp:rw,size=32m --mount "type=bind,src=$state/frontend-work/apps/dashboard/dist,dst=/dashboard-dist,readonly" \
  --mount "type=bind,src=$dashboard_server,dst=/serve-dashboard.mjs,readonly" "$node_image" \
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp GITHUB_ACTIONS=true GITHUB_REPOSITORY=ilovedrrealty/realty-api \
  node /serve-dashboard.mjs) || die DASHBOARD_SERVER_CREATE_FAILED
bounded 20s docker start "$dashboard" >/dev/null || die STATIC_SERVER_START_FAILED

phases=(signup_email_history)
drivers=(run-browser.mjs)
for index in "${!phases[@]}"; do
  phase="${phases[$index]}"
  phase_nonce=$(python3 -I -B -c 'from uuid import uuid4; print(uuid4().hex)')
  [[ "$phase_nonce" =~ ^[a-f0-9]{32}$ ]] || die PHASE_NONCE_REQUIRED
  db="ildr_browser_ci_$phase_nonce"
  marker="ildr-signup-history-browser-ci:$phase_nonce"
  bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "CREATE DATABASE $db" -c "COMMENT ON DATABASE $db IS '$marker'" >/dev/null || die OWNED_PHASE_DATABASE_REQUIRED
  mkdir "$state/fixtures/$phase"
  api=$(bounded 25s docker create "${runtime[@]}" --memory 2g --memory-swap 2g --tmpfs /tmp:rw,exec,size=128m \
    --mount "type=bind,src=$backend,dst=/application,readonly" --mount "type=bind,src=$previous,dst=/previous,readonly" --mount "type=bind,src=$reference,dst=/baseline106,readonly" --mount "type=bind,src=$ops/ci,dst=/ci-tools,readonly" \
    --mount "type=bind,src=$state/fixtures/$phase,dst=/browser-fixtures" "$app_image" \
    env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp PYTHONDONTWRITEBYTECODE=1 \
    GITHUB_ACTIONS=true GITHUB_REPOSITORY=ilovedrrealty/realty-api ILDR_BROWSER_CI_NONCE="$phase_nonce" \
    python -I -B /ci-tools/signup-email-history-browser/serve_fixture.py browser) || die API_CREATE_FAILED
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
print('SIGNUP_HISTORY_LANDING_BUILD native='+sys.argv[3]+' required_exports='+str(sum(path.is_file() and not path.is_symlink() for path in exports)))
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
  api_running=$(bounded 5s docker inspect --format '{{.State.Running}}' "$api")
  api_oom=$(bounded 5s docker inspect --format '{{.State.OOMKilled}}' "$api")
  pg_running=$(bounded 5s docker inspect --format '{{.State.Running}}' "$pg")
  pg_oom=$(bounded 5s docker inspect --format '{{.State.OOMKilled}}' "$pg")
  [[ "$api_native" =~ ^[0-9]{1,3}$ && "$api_running" =~ ^(true|false)$ && "$api_oom" =~ ^(true|false)$ &&
     "$pg_running" =~ ^(true|false)$ && "$pg_oom" =~ ^(true|false)$ ]] || die FINITE_PHASE_PROCESS_OBSERVATIONS_REQUIRED
  printf 'SIGNUP_HISTORY_PHASE_PROCESS api_exit=%s api_running=%s api_oom=%s postgres_running=%s postgres_oom=%s browser_post_oom=unobserved_rm\n' \
    "$api_native" "$api_running" "$api_oom" "$pg_running" "$pg_oom"
  bounded 10s docker logs "$api" >"$state/api-$phase.log" 2>&1 || die API_LOG_REQUIRED
  browser_ok=0; api_ok=0
  python3 -I -B "$diagnostic_tools/check-result.py" browser "$state/browser-$phase.log" "$browser_native" || browser_ok=$?
  python3 -I -B "$diagnostic_tools/check-result.py" api "$state/api-$phase.log" "$api_native" || api_ok=$?
  [[ "$api_running" == false && "$pg_running" == true ]] || die STOPPED_API_AND_RUNNING_DATABASE_REQUIRED
  [[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres \
    -v ON_ERROR_STOP=1 -c "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname='$db'") == "$marker" ]] || die OWNED_DATABASE_DROP_GUARD
  bounded 10s docker exec "$pg" psql -X -h 127.0.0.1 -p 55449 -U postgres -d postgres -v ON_ERROR_STOP=1 \
    -c "DROP DATABASE $db" >/dev/null || die OWNED_DATABASE_DROP_REQUIRED
  [[ $(bounded 6s docker exec "$pg" psql -XAt -h 127.0.0.1 -p 55449 -U postgres -d postgres \
    -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_database WHERE datname='$db'") == 0 ]] || die OWNED_DATABASE_ABSENCE_REQUIRED
  bounded 15s docker rm -v "$api" >/dev/null || die PHASE_API_REMOVAL_REQUIRED
  [[ "$browser_ok" == 0 && "$api_ok" == 0 && "$api_oom" == false && "$pg_oom" == false &&
     ! -e "$state/fixtures/$phase/fixtures.json" && ! -L "$state/fixtures/$phase/fixtures.json" ]] || die EXACT_BROWSER_AND_AUTHORITATIVE_FACTS_REQUIRED
  verify_source || die BROWSER_ALTERED_FRONTEND_SOURCE
  [[ -z $(git -C "$backend" status --porcelain=v1 --untracked-files=all) ]] || die BROWSER_ALTERED_BACKEND_SOURCE
done
printf 'SIGNUP_HISTORY_HOSTED_RESULT operations=%s frontend=%s backend=%s retained_native162_run=45652164070 current_native_tests=false retained_native_tests=21 frontend_tests=555 standalone_strict=current dashboard=current landing=current browser=exact_checker_pass authoritative_fixture=exact_checker_pass live_provider_calls=0 run=%s attempt=1\n' \
  "$GITHUB_SHA" "$frontend_commit" "$backend_commit" "$GITHUB_RUN_ID"

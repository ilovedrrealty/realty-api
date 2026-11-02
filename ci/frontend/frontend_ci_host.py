"""Renew the reviewed frontend relay with59 exact source overlays; no frontend push."""
import hashlib
from datetime import datetime
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time

BACKEND_BASE = "926e2f47b56ed66e33057bfc66d4d6f7bfa10793"
BACKEND_TREE = "77f0b27bd0d3812559e3b59eed9409bef12438c5"
FE_BASE = "73d771940e73d42ed044cea77a5258a93a445b05"
FE_TREE = "f718c91136d2726f8f8966c367f3c20584081a0a"
IMAGE = "node:24.22.1-bookworm-slim@sha256:be8204d4bd327363357f532187b752ff62759c31bedf98e9e18fc08c9cbd605c"
LABEL = "org.ilovedrrealty.frontend.run"
BRANCH = "codex-ci-services-frontend-20261102"
PRIOR_MANIFEST_SHA256 = "ca6957ff3ac6587b3f0462ecb9f81d56c4c68b948c148b1943572244d6f3bd79"
SOURCE_HEAD = "d967a3714b40b6e98ad2654c3ce865ad5d152a20"
SOURCE_TREE = "ef36dc3083673e3daad8f73401ea9d00a87c32b3"
SOURCE_PARENT = "d711a13706b1410a1e8a8c415713642443a27c75"
SOURCE_PARENT_TREE = "90e8d74bab96d915de828a9b9c160df2ef2ce21d"
SOURCE_EVIDENCE = {
    "ROOT-INTEGRATED-SOURCE.json": "42c4a2060a16c2b1709a3eeccbc4448fca5a45803ef1da5eb474c1449687d5ef",
    "independent-integrated-photo-source-review.json": "2762a84bc96a0ef640eb428dd44e448ea9b7deb3e80fcc3fabf0f8c2db218a6a",
    "FRONTEND-SERVICES-AUTHOR-SOURCE-V3.json": "333b8bf50bb43225d96c48bacc4a009f25c92e8ef2c69f62690e4598026038ec",
    "independent-frontend-services-v3-source-review.json": "b3e04ed24ecb79f1d6b73e9269b128c077d259362e3e859c98361177c37e7f56",
    "independent-native434-git-body-audit.json": "055b1fe22eeda2125cdc98b02c33d9e7592944ba42329571c84bbdd2cfe040db",
    "independent-actual434-commit-review.json": "3f57217d72058451561645f28f0cfca8ae3098df088f32662cb19c9834ad379d",
    "local-app434-commit.json": "c791b010fa6fde544b956de919a8621004a3b41b7a1ab710608474fbf5f18a52",
    "BACKEND-FRONTEND-CONTRACT-COMPARISON-V3.json": "56fa8241f89b642804fa5589df517813b6e0d269b64e0ecaa5ede57ef15dcd30",
}
SOURCE_ADDITIONS = {
    "apps/dashboard/src/api/serviceWorkflow.ts",
    "apps/dashboard/src/components/network/ServicePreferences.tsx",
    "apps/dashboard/src/components/network/ServiceProposalFields.tsx",
    "apps/dashboard/src/components/network/ServiceRequestCreate.tsx",
    "apps/dashboard/src/components/network/ServiceRequestDetail.tsx",
    "apps/dashboard/src/components/network/ServiceWorkflow.tsx",
    "apps/dashboard/src/components/network/ServiceWorkflowShared.tsx",
    "tests/public-image-url.test.cjs", "tests/service-workflow.test.cjs",
}
SOURCE_DELTA = {
    *SOURCE_ADDITIONS, "apps/dashboard/src/pages/shared/PropertyNetwork.tsx",
    "apps/dashboard/src/locale/en/property_network.json", "apps/dashboard/src/locale/es/property_network.json",
    "apps/landing/src/api/imgUrl.ts", "apps/landing/src/views/SpanishLandingPage.tsx",
    "tests/property-network-ui.test.cjs",
}
RENEWED_OPS = {".github/workflows/frontend-validation.yml", "ci/frontend/frontend_ci_host.py",
               "ci/frontend/run_frontend_ci.cjs", "ci/frontend/frontend-overlay.json"}
OPS = {".github/workflows/frontend-validation.yml", *(
    "ci/frontend/" + name for name in ("frontend_ci_host.py", "run_frontend_ci.cjs",
    "landing_fixture.cjs", "landing_fixture.test.cjs", "frontend-overlay.json"))}
MAX_LOG = 16 * 1024 * 1024

def need(ok, code):
    if not ok:
        raise RuntimeError(code)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def relative(name):
    need(isinstance(name, str) and name.isascii() and "\\" not in name
         and not any(ord(c) < 32 for c in name), "RELATIVE_PATH_REQUIRED")
    p = PurePosixPath(name)
    need(not p.is_absolute() and all(x not in ("", ".", "..") for x in name.split("/")), "RELATIVE_PATH_REQUIRED")
    return p

def regular(root, name):
    current = root
    for part in relative(name).parts:
        current /= part
        need(not current.is_symlink(), "SYMLINK_REFUSED")
    need(current.is_file(), "REGULAR_FILE_REQUIRED")
    return current

def available():
    return int(re.search(r"^MemAvailable:\s+(\d+) kB$", Path("/proc/meminfo").read_text(), re.M)[1]) / 1024

def native(args, cwd=None, data=None, env=None, seconds=30, limit=MAX_LOG):
    result = subprocess.run(args, cwd=cwd, input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, timeout=seconds, check=False)
    need(len(result.stdout) <= limit and len(result.stderr) <= limit, "NATIVE_OUTPUT_BOUND")
    need(result.returncode == 0, "NATIVE_NONZERO_" + str(result.returncode))
    return result.stdout

def git(repo, *args, data=None, env=None, limit=MAX_LOG):
    local_env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}
    if env is not None:
        local_env.update(env)
    return native(["git", "-c", "core.autocrlf=false", "-c", "core.fsmonitor=false",
                   "-c", "core.attributesFile=/dev/null", *args], cwd=repo, data=data, env=local_env, limit=limit)

def tree(repo, revision):
    result = {}
    for record in git(repo, "ls-tree", "-r", "-z", revision).split(b"\0"):
        if not record:
            continue
        header, name = record.split(b"\t", 1)
        mode, kind, oid = header.decode("ascii").split(" ")
        name = name.decode("utf8")
        relative(name)
        need(kind == "blob" and mode in ("100644", "100755") and name not in result, "REGULAR_TREE_REQUIRED")
        result[name] = {"mode": mode, "git_blob_oid": oid}
    return result

def verify_bodies(repo, revision, expected):
    actual = tree(repo, revision)
    need(set(actual) == set(expected), "EXACT_FRONTEND_TREE_PATHS_REQUIRED")
    names = sorted(actual)
    bound = sum(value["bytes"] for value in expected.values()) + 100 * len(names)
    need(bound < 16 * 1024 * 1024, "FRONTEND_SOURCE_BOUND")
    output = git(repo, "cat-file", "--batch", data=("\n".join(actual[n]["git_blob_oid"] for n in names) + "\n").encode(), limit=bound)
    cursor = 0
    for name in names:
        end = output.index(b"\n", cursor)
        oid, kind, size = output[cursor:end].decode("ascii").split(" ")
        size = int(size); cursor = end + 1
        body = output[cursor:cursor + size]; cursor += size
        need(cursor < len(output) and output[cursor:cursor+1] == b"\n", "BLOB_DELIMITER_REQUIRED")
        cursor += 1
        e = expected[name]
        need(kind == "blob" and actual[name] == {"mode": e["mode"], "git_blob_oid": e["git_blob_oid"]}
             and oid == e["git_blob_oid"] == blob(body) and size == e["bytes"]
             and sha(body) == e["sha256"] and (b"\0" in body) == e["binary_nul_detected"], "ACTUAL_FRONTEND_BLOB_MISMATCH")
    need(cursor == len(output), "TRAILING_GIT_BATCH_OUTPUT")

def reconstruct(backend, frontend, m, scratch):
    need(git(frontend, "rev-parse", "HEAD").strip().decode() == FE_BASE
         and git(frontend, "rev-parse", "HEAD^{tree}").strip().decode() == FE_TREE, "EXACT_PUBLIC_FRONTEND_BASE_REQUIRED")
    need(git(frontend, "status", "--porcelain=v1", "--untracked-files=all") == b"", "CLEAN_EPHEMERAL_FRONTEND_BASE_REQUIRED")
    need(tree(frontend, "HEAD") == m["base_git_blobs"] and len(m["base_git_blobs"]) == 397, "BASE397_MAP_REQUIRED")
    need(m["format"] == 2 and m["frontend_base_commit"] == FE_BASE and m["frontend_base_tree"] == FE_TREE
         and re.fullmatch("[a-f0-9]{40}", m["frontend_candidate_tree"])
         and m["frontend_candidate_tree"] == SOURCE_TREE
         and len(m["application_files"]) == 434 and len(m["overlays"]) == 59, "FROZEN_FRONTEND_CLOSURE_REQUIRED")
    need(set(m["application_files"]) == set(m["base_git_blobs"]) | set(m["overlays"])
         and len(set(m["base_git_blobs"]) & set(m["overlays"])) == 22
         and len(m["public_base_ancestor_commits"]) == 8
         and len(m["expected_public_base_changes"]) == 59
         and set(m["expected_public_base_changes"]) == {
             ("M\t" if name in m["base_git_blobs"] else "A\t") + name for name in m["overlays"]
         }, "EXACT59_PUBLIC_BASE_CLOSURE_REQUIRED")
    index = scratch / "frontend-index"
    env = {**os.environ, "GIT_INDEX_FILE": str(index), "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}
    git(frontend, "read-tree", FE_BASE, env=env)
    for name, e in m["overlays"].items():
        relative(name)
        need(e["payload_path"] == "ci/frontend/overlay/" + name, "EXACT_PAYLOAD_PATH_REQUIRED")
        payload = regular(backend, e["payload_path"]).read_bytes()
        need(len(payload) == e["bytes"] and sha(payload) == e["sha256"] and blob(payload) == e["git_blob_oid"], "OVERLAY_BODY_REQUIRED")
        need(e["mode"] in ("100644", "100755"), "OVERLAY_REGULAR_MODE_REQUIRED")
        target = frontend.joinpath(*relative(name).parts)
        current = frontend
        for part in relative(name).parts[:-1]:
            current /= part
            need(not current.is_symlink(), "OVERLAY_PARENT_SYMLINK_REFUSED")
            current.mkdir(exist_ok=True)
        need(not target.is_symlink(), "OVERLAY_TARGET_SYMLINK_REFUSED")
        if target.exists():
            need(target.is_file() and name in m["base_git_blobs"], "OVERLAY_EXISTING_TARGET_REFUSED")
        target.write_bytes(payload); target.chmod(0o755 if e["mode"] == "100755" else 0o644)
        oid = git(frontend, "hash-object", "-w", "--stdin", data=payload).strip().decode()
        need(oid == e["git_blob_oid"], "STORED_OVERLAY_OID_REQUIRED")
        git(frontend, "update-index", "--add", "--cacheinfo", e["mode"], oid, name, env=env)
    candidate = git(frontend, "write-tree", env=env).strip().decode()
    need(candidate == m["frontend_candidate_tree"], "ACTUAL_RECONSTRUCTED_TREE_MISMATCH")
    actual_changes = git(frontend, "diff", "--name-status", FE_BASE, candidate).decode().splitlines()
    need(len(actual_changes) == 59 and set(actual_changes) == set(m["expected_public_base_changes"]),
         "ACTUAL_PUBLIC_BASE59_DIFF_REQUIRED")
    verify_bodies(frontend, candidate, m["application_files"])
    need(git(frontend, "rev-parse", "HEAD").strip().decode() == FE_BASE, "FRONTEND_HEAD_MUST_REMAIN_BASE")
    return candidate


def validate_renewal(backend, m):
    prior_raw = git(backend, "show", BACKEND_BASE + ":ci/frontend/frontend-overlay.json")
    need(sha(prior_raw) == PRIOR_MANIFEST_SHA256, "GENUINE_ACCEPTED_RELAY_MANIFEST_REQUIRED")
    prior = json.loads(prior_raw)
    need(m["source_worktree_head"] == SOURCE_HEAD and m["source_worktree_base_tree"] == SOURCE_TREE
         and m["source_worktree_parent"] == m["accepted_frontend_commit"] == SOURCE_PARENT
         and m["source_worktree_parent_tree"] == m["accepted_frontend_tree"] == SOURCE_PARENT_TREE
         and prior["frontend_candidate_tree"] == SOURCE_PARENT_TREE
         and m["source_worktree_branch"] == "codex-private-service-workflow-ui-20261102"
         and m["relay_parent_commit"] == BACKEND_BASE and m["relay_parent_tree"] == BACKEND_TREE
         and m["relay_parent_paths"] == 256 and m["relay_candidate_paths"] == 267
         and set(m["source_delta_files"]) == SOURCE_DELTA
         and m["source_worktree_porcelain"] == [], "ACTUAL_FROZEN_SOURCE434_CONTEXT_REQUIRED")
    evidence = m["current_source_evidence_sha256"]
    need(evidence == SOURCE_EVIDENCE
         and m["base_git_blobs"] == prior["base_git_blobs"]
         and m["public_base_ancestor_commits"] == prior["public_base_ancestor_commits"],
         "CURRENT_INDEPENDENT_SOURCE_AND_PUBLIC_BASE_REQUIRED")
    added = set(m["application_files"]) - set(prior["application_files"])
    need(added == SOURCE_ADDITIONS
         and set(prior["application_files"]) <= set(m["application_files"])
         and {name for name in m["application_files"] if m["application_files"][name] != prior["application_files"].get(name)} == SOURCE_DELTA,
         "ONLY_CURRENT15_FRONTEND_BODY_CHANGES_ALLOWED")
    for name, item in m["source_delta_files"].items():
        need({key: item[key] for key in ("mode", "git_blob_oid", "sha256", "bytes", "binary_nul_detected")}
             == m["application_files"][name], "CURRENT15_NATIVE_BODY_MAP_REQUIRED")
    need(set(m["overlays"]) == set(prior["overlays"]) | SOURCE_DELTA
         and all(all(m["overlays"][name][key] == prior["overlays"][name][key]
                     for key in ("payload_path", "mode", "git_blob_oid", "sha256", "bytes"))
                 for name in set(prior["overlays"]) - SOURCE_DELTA), "UNCHANGED44_ACCEPTED_PAYLOADS_REQUIRED")
    changes = {"M\t" + name for name in RENEWED_OPS} | {
        ("M\t" if name in prior["overlays"] else "A\t") + m["overlays"][name]["payload_path"]
        for name in SOURCE_DELTA
    }
    need(len(changes) == 19 and m["expected_backend_changes"] == sorted(changes)
         and set(git(backend, "diff", "--name-status", BACKEND_BASE, "HEAD").decode().splitlines()) == changes,
         "ONLY8_MODIFIED11_NEW_RELAY_PATHS_ALLOWED")
    before, after = tree(backend, BACKEND_BASE), tree(backend, "HEAD")
    changed_names = {line[2:] for line in changes}
    need(len(before) == 256 and len(after) == 267
         and set(after) == set(before) | {m["overlays"][name]["payload_path"] for name in SOURCE_DELTA - set(prior["overlays"])}
         and all(after[name] == item for name, item in before.items() if name not in changed_names),
         "UNCHANGED248_BASE_GIT_OBJECTS_REQUIRED")

def main():
    need(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true", "GITHUB_LINUX_ONLY")
    need(os.environ.get("GITHUB_REPOSITORY") == "ilovedrrealty/realty-api"
         and os.environ.get("GITHUB_REF") == "refs/heads/" + BRANCH
         and os.environ.get("GITHUB_RUN_ATTEMPT") == "1", "EXACT_CI_EVENT_REQUIRED")
    commit = os.environ.get("GITHUB_SHA", ""); run = os.environ.get("GITHUB_RUN_ID", "")
    need(re.fullmatch("[a-f0-9]{40}", commit) and re.fullmatch("[0-9]+", run), "EXACT_CI_IDENTIFIERS_REQUIRED")
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve()
    backend = workspace / "backend-ci"; frontend = workspace / "frontend-base"
    need(backend.is_dir() and frontend.is_dir() and not backend.is_symlink()
         and not frontend.is_symlink(), "FIXED_EPHEMERAL_CHECKOUT_ROOTS_REQUIRED")
    need(Path(__file__).resolve() == backend / "ci/frontend/frontend_ci_host.py", "FIXED_ENTRY_PATH_REQUIRED")
    need(available() >= 10240, "START_MEMORY_BELOW_10240_MIB")
    need(shutil.disk_usage(workspace).free >= 8 * 1024 ** 3, "START_DISK_BELOW_8_GIB")
    need(git(backend, "rev-parse", "HEAD").strip().decode() == commit
         and git(backend, "rev-parse", "HEAD^").strip().decode() == BACKEND_BASE
         and git(backend, "rev-parse", "HEAD^{tree}").strip().decode(), "EXACT_BACKEND_CI_PARENT_REQUIRED")
    need(git(backend, "rev-parse", BACKEND_BASE + "^{tree}").strip().decode() == BACKEND_TREE, "EXACT_BACKEND_BASE_TREE_REQUIRED")
    need(git(backend, "status", "--porcelain=v1", "--untracked-files=all") == b"", "CLEAN_BACKEND_CI_SOURCE_REQUIRED")
    manifest_bytes = regular(backend, "ci/frontend/frontend-overlay.json").read_bytes()
    need(sha(manifest_bytes) == os.environ.get("OVERLAY_MANIFEST_SHA256"), "EXACT_MANIFEST_PIN_REQUIRED")
    m = json.loads(manifest_bytes)
    need(len(OPS | {e["payload_path"] for e in m["overlays"].values()}) == 65, "EXACT65_OPERATION_AND_PAYLOAD_CLOSURE_REQUIRED")
    validate_renewal(backend, m)
    key = run + "-1-" + commit[:12]; volume = "ildr-frontend-" + key
    report = {"schema_version": 1, "status": "FAIL", "backend_ci_commit": commit, "run_id": run, "attempt": 1,
              "frontend_base_commit": FE_BASE, "frontend_source_tree": None, "containers": [],
              "owned_cleanup_verified": False, "real_api_provider_or_production_actions": False,
              "live_publication_or_release_acceptance": False}
    ids = []; volume_created = False; capture_uncertain = False
    temp_root = Path(os.environ["RUNNER_TEMP"]).resolve()
    need(temp_root.is_dir() and not temp_root.is_symlink(), "FIXED_RUNNER_TEMP_REQUIRED")
    with tempfile.TemporaryDirectory(prefix="ildr-frontend-" + key + "-", dir=temp_root) as tmp:
        scratch = Path(tmp)
        docker_env = {"PATH": os.environ["PATH"], "HOME": str(scratch), "DOCKER_CONFIG": str(scratch / "docker-config")}
        (scratch / "docker-config").mkdir()
        def docker(*args, seconds=30):
            return native(["docker", *args], env=docker_env, seconds=seconds)
        def inspect_container(cid):
            obj = json.loads(docker("inspect", "--type", "container", cid))[0]
            need(obj["Id"] == cid and obj["Config"]["Labels"].get(LABEL) == key, "OWNED_CONTAINER_ID_REQUIRED")
            return obj
        def absent(kind, name):
            if kind == "volume":
                names = docker("volume", "ls", "--quiet").decode().splitlines()
                need(name not in names, "POSITIVE_VOLUME_ABSENCE_REQUIRED")
            else:
                need(kind == "container", "FIXED_ABSENCE_KIND_REQUIRED")
                rows = docker("container", "ls", "--all", "--no-trunc", "--format", "{{.ID}} {{.Names}}").decode().splitlines()
                for row in rows:
                    cid, names = row.split(" ", 1)
                    need(re.fullmatch("[a-f0-9]{64}", cid) and names, "REGULAR_CONTAINER_INVENTORY_REQUIRED")
                    need(name != cid and name not in names.split(","), "POSITIVE_CONTAINER_ABSENCE_REQUIRED")
        def run_container(mode, network, deadline):
            nonlocal capture_uncertain
            need(available() >= 10240, "PHASE_START_MEMORY_BELOW_10240_MIB")
            absent("container", "ildr-fe-" + key + "-" + mode)
            try:
                raw = docker("create", "--name", "ildr-fe-" + key + "-" + mode, "--platform", "linux/amd64",
                "--label", LABEL + "=" + key, "--network", network, "--read-only", "--init",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256", "--cpus", "2",
                "--memory", "8g", "--memory-swap", "8g", "--tmpfs", "/tmp:rw,nosuid,nodev,size=536870912",
                "--mount", "type=bind,src=" + str(frontend) + ",dst=/source,readonly",
                "--mount", "type=bind,src=" + str(backend / "ci/frontend") + ",dst=/ci,readonly",
                "--mount", "type=volume,src=" + volume + ",dst=/work", "--workdir", "/work",
                "--env", "FE_CI_COMMIT=" + commit, "--env", "FE_CI_RUN_ID=" + run,
                    IMAGE, "node", "--max-old-space-size=256", "/ci/run_frontend_ci.cjs", mode).strip().decode()
            except Exception:
                capture_uncertain = True
                raise
            if not re.fullmatch("[a-f0-9]{64}", raw):
                capture_uncertain = True; raise RuntimeError("CONTAINER_CAPTURE_UNCERTAIN")
            ids.append(raw); obj = inspect_container(raw)
            need(obj["HostConfig"]["NetworkMode"] == network and obj["HostConfig"]["Memory"] == 8589934592
                 and obj["HostConfig"]["ReadonlyRootfs"] is True, "FIXED_CONTAINER_BOUNDARY_REQUIRED")
            process = subprocess.Popen(["docker", "start", "--attach", raw], env=docker_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            selector = selectors.DefaultSelector(); selector.register(process.stdout, selectors.EVENT_READ); selector.register(process.stderr, selectors.EVENT_READ)
            streams = {process.stdout: bytearray(), process.stderr: bytearray()}; began = time.monotonic(); stop_reason = None; stopped_at = None
            try:
                while selector.get_map():
                    if stop_reason is None and (time.monotonic() - began > deadline or available() < 320
                                                or shutil.disk_usage(workspace).free < 1024 ** 3):
                        stop_reason = "DEADLINE_MEMORY_OR_DISK_RESERVE"; stopped_at = time.monotonic()
                        inspect_container(raw); docker("kill", raw)
                    if stopped_at is not None and time.monotonic() - stopped_at > 5:
                        process.kill()
                        break
                    for entry, _ in selector.select(.25):
                        chunk = os.read(entry.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(entry.fileobj); continue
                        remaining = MAX_LOG - len(streams[entry.fileobj])
                        streams[entry.fileobj].extend(chunk[:max(0, remaining)])
                        if len(chunk) > remaining and stop_reason is None:
                            stop_reason = "CONTAINER_LOG_BOUND"; stopped_at = time.monotonic()
                            inspect_container(raw); docker("kill", raw)
                process.wait(timeout=20)
            finally:
                selector.close()
                if process.poll() is None:
                    try:
                        if inspect_container(raw)["State"]["Running"]:
                            docker("kill", raw)
                    finally:
                        process.kill(); process.wait(timeout=10)
            stdout = bytes(streams[process.stdout]); stderr = bytes(streams[process.stderr])
            print("FRONTEND_CONTAINER_STDOUT_BEGIN mode=" + mode, flush=True); sys.stdout.buffer.write(stdout); sys.stdout.flush()
            print("\nFRONTEND_CONTAINER_STDERR mode=" + mode, flush=True); sys.stdout.buffer.write(stderr); sys.stdout.flush()
            print("\nFRONTEND_CONTAINER_STDOUT_END mode=" + mode, flush=True)
            obj = inspect_container(raw); state = obj["State"]
            record = {"mode": mode, "container_id": raw, "client_exit": process.returncode,
                      "container_exit": state["ExitCode"], "running": state["Running"], "oom_killed": state["OOMKilled"],
                      "stop_reason": stop_reason, "stdout_sha256": sha(stdout), "stdout_bytes": len(stdout),
                      "stderr_sha256": sha(stderr), "stderr_bytes": len(stderr)}
            report["containers"].append(record)
            need(process.returncode == state["ExitCode"] == 0 and not state["Running"] and not state["OOMKilled"] and stop_reason is None, "CONTAINER_NATIVE_FAILURE")
            lines = [line[len(b"FRONTEND_CI_RESULT "):] for line in stdout.splitlines() if line.startswith(b"FRONTEND_CI_RESULT ")]
            need(len(lines) == 1 and len(lines[0]) <= 131072, "UNIQUE_TYPED_INNER_RESULT_REQUIRED")
            inner = json.loads(lines[0])
            need(inner["mode"] == mode and inner["ci_commit"] == commit and inner["run_id"] == run
                 and inner["source_tree"] == m["frontend_candidate_tree"] and inner["source_unchanged"] is True
                 and inner["strict_configs_unchanged"] is True
                 and inner["status"] == ("PASS_LOCKED_INSTALL_NO_LIFECYCLE" if mode == "install" else "PASS_FULL_CHECKS_SYNTHETIC_LANDING_INPUT_NOT_RELEASE"), "INNER_ACCEPTANCE_REQUIRED")
            record["inner_result"] = inner
        try:
            report["frontend_source_tree"] = reconstruct(backend, frontend, m, scratch)
            docker("pull", "--platform", "linux/amd64", IMAGE, seconds=180)
            image = json.loads(docker("image", "inspect", IMAGE))[0]
            need(image["Os"] == "linux" and image["Architecture"] == "amd64"
                 and any(value.endswith("@" + IMAGE.split("@", 1)[1]) for value in image["RepoDigests"]), "PINNED_IMAGE_REQUIRED")
            report["image_id"] = image["Id"]; report["image_repo_digests"] = image["RepoDigests"]
            absent("volume", volume)
            try:
                create_started = int(time.time())
                captured_volume = docker("volume", "create", "--label", LABEL + "=" + key, volume).strip().decode()
                need(captured_volume == volume, "OWNED_VOLUME_CAPTURE_REQUIRED")
                created_info = json.loads(docker("volume", "inspect", volume))[0]
                created_at = datetime.fromisoformat(created_info["CreatedAt"].replace("Z", "+00:00")).timestamp()
                need(created_info["Name"] == volume and created_info["Labels"].get(LABEL) == key
                     and create_started <= created_at <= time.time() + 2, "FRESH_OWNED_VOLUME_REQUIRED_BEFORE_MOUNT")
            except Exception:
                capture_uncertain = True
                raise
            volume_created = True
            run_container("install", "bridge", 420)
            run_container("validate", "none", 1200)
            verify_bodies(frontend, m["frontend_candidate_tree"], m["application_files"])
            need(git(backend, "status", "--porcelain=v1", "--untracked-files=all") == b"", "BACKEND_SOURCE_CHANGED")
            report["status"] = "PASS_FULL_STRICT_FRONTEND_CHECKS_SYNTHETIC_LANDING_NOT_PUBLICATION"
        except Exception as error:
            report["failure_code"] = str(error) if re.fullmatch("[A-Z0-9_]+", str(error)) else "UNCLASSIFIED_HOST_FAILURE"
        finally:
            try:
                for cid in reversed(ids):
                    inspect_container(cid); docker("rm", "--force", "--volumes", cid); absent("container", cid)
                if volume_created:
                    info = json.loads(docker("volume", "inspect", volume))[0]
                    need(info["Name"] == volume and info["Labels"].get(LABEL) == key, "OWNED_VOLUME_REQUIRED")
                    docker("volume", "rm", volume); absent("volume", volume)
                need(docker("ps", "-aq", "--no-trunc", "--filter", "label=" + LABEL + "=" + key).strip() == b"", "OWNED_CONTAINER_RESIDUAL")
                need(docker("volume", "ls", "-q", "--filter", "label=" + LABEL + "=" + key).strip() == b"", "OWNED_VOLUME_RESIDUAL")
                docker("info", "--format", "{{.ServerVersion}}")
                need(not capture_uncertain, "UNCERTAIN_CAPTURE_DISQUALIFIES_CLEANUP")
                report["owned_cleanup_verified"] = True
            except Exception:
                report["status"] = "FAIL"; report["cleanup_failure"] = True
        body = (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()
        marker = "commit=" + commit + " run=" + run + " attempt=1 sha256=" + sha(body) + " bytes=" + str(len(body))
        print("FRONTEND_CI_HOST_RESULT_BEGIN " + marker, flush=True); sys.stdout.buffer.write(body); sys.stdout.flush()
        print("FRONTEND_CI_HOST_RESULT_END " + marker, flush=True)
        if report["owned_cleanup_verified"]:
            print("FRONTEND_CI_OWNED_CLEANUP_VERIFIED commit=" + commit + " run=" + run + " attempt=1", flush=True)
        return 0 if report["status"].startswith("PASS_") and report["owned_cleanup_verified"] else 1

if __name__ == "__main__":
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: (_ for _ in ()).throw(InterruptedError("CI_INTERRUPTED")))
    raise SystemExit(main())

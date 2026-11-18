"""Reconstruct the reviewed narrow FE and its two exact responsive/navigation successors."""

from hashlib import sha1, sha256
from pathlib import Path
import subprocess
import sys

BASE = "b5df6a9321365e827c01df556c09b8808151e59a"
BASE_TREE = "27f08141cd78943e4c32fe4212d871e5153022ae"
RECIPE = "8091437cd3cc4c1f308ca6c6c79fb9d479eac63f"
RECIPE_TREE = "caea995775d9f508d0ca1960fb8d66ecb5ab21ab"
COMMIT = "480c3bd53bcfd16d95e9e4d974c97429e00a0e68"
TREE = "6749f0833ce655fa64918213792d28046f046b9d"
LAYERS = (
    ("f01-f03-frontend", "373b27bcae0f101e91cea4656bbef179d42fa09d18d1c4462711d20cc1d98bbe",
     "479d36b81860692fdcac904860981a2621b460280731ce68318fec08f326b434",
     "ebfdc8c93d3db51b58564420c6ee65502cf9b72a", "c3ed974dd9924968b90f284159d57a86d76f8b70"),
    ("f01-f03-customer-step", "0d8a9c03e1ba1811bdc36621cb33bfb90a2debf6cbf2c646579599e9b69ec71a",
     "26eff6da47bf78e2bc6108cfd57bb0249a0d555a1e2be78ec818215305cbe5fc",
     "b881b9f25eb50e5b61b262a9c3f64757841f4352", "669c5b5e26c161e5146ea52921c9b2f7b9dfa3ff"),
    ("f01-f03-settings-mobile", "29386027b94f6c74a079a0fd53097968b6ca8979e544337beffd033c727fe54d",
     "cd16b4204e04122b0e9da5c3b600fb7f841b7ab4a32c041fd5419243af4d06c3",
     "977ca90b30c5cb1897eb186fec8a51c0711883f7", "2e0f96561a531eb883b88112cad8b4b809cb3c26"),
    ("delivery-mobile", "46a0f0939d20391409508f2c6f04919eafd35406734fe06457ed2835310cea39",
     "dc3ee2e4e752f7420b22969f8fa53582bd4db335e34a2369726ed96f84eda3e8",
     "fee6704c79e542207eb16787504e9c37fcc6b429", "476cf8080f71d213c81af899cb85d039a5bd422f"),
    ("default-renting", "fe06a70128916f49c32896fbe79cda31097ff3c3cec95618d854bca635ea7cf0",
     "d63f896807447ffe6e6ef2dd65d513fda7ed368232cbe937cd979479c5fb7521", COMMIT, TREE),
)


def git(root, *arguments, data=None):
    return subprocess.check_output(["git", "-C", str(root), *arguments], input=data, timeout=30)


def main():
    base, recipe, objects, copy = map(Path, sys.argv[1:])
    assert git(base, "rev-parse", "HEAD", "HEAD^{tree}").decode().splitlines() == [BASE, BASE_TREE]
    assert git(recipe, "rev-parse", "HEAD", "HEAD^{tree}").decode().splitlines() == [RECIPE, RECIPE_TREE]
    assert not objects.exists() and copy.is_dir() and not any(copy.iterdir())
    subprocess.run(["git", "init", "--quiet", "--object-format=sha1", str(objects)], check=True, timeout=10)
    (objects / ".git/objects/info/alternates").write_text(str(base / ".git/objects") + "\n")
    git(objects, "read-tree", BASE)
    previous = BASE
    for name, patch_hash, commit_hash, commit, tree in LAYERS:
        directory = Path(__file__).resolve().parent if name in {"delivery-mobile", "default-renting"} else recipe / "ci/cumulative-landing"
        patch = directory / f"{name}.patch"
        payload = directory / f"{name}.commit"
        assert sha256(patch.read_bytes()).hexdigest() == patch_hash
        assert sha256(payload.read_bytes()).hexdigest() == commit_hash
        git(objects, "apply", "--cached", "--check", "--whitespace=error-all", str(patch))
        git(objects, "apply", "--cached", "--whitespace=error-all", str(patch))
        assert git(objects, "write-tree").decode().strip() == tree
        assert git(objects, "hash-object", "-t", "commit", "-w", str(payload)).decode().strip() == commit
        assert git(objects, "rev-parse", commit + "^").decode().strip() == previous
        previous = commit
    entries = git(objects, "ls-tree", "-rz", COMMIT).split(b"\0")
    assert entries[-1] == b"" and len(entries) - 1 == 391
    for entry in entries[:-1]:
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        name = raw_name.decode()
        assert kind == b"blob" and mode in (b"100644", b"100755") and ".." not in Path(name).parts
        value = git(objects, "cat-file", "blob", oid.decode())
        assert sha1(b"blob " + str(len(value)).encode() + b"\0" + value).hexdigest().encode() == oid
        target = copy / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    git(objects, "diff", "--exit-code", BASE, COMMIT, "--",
        "apps/dashboard/src/components/admin/LeadDetailPanel.tsx",
        "apps/dashboard/src/components/shared/ShareContactDialog.tsx", "apps/dashboard/src/lib/contactShare.ts",
        "apps/dashboard/src/api/tradeContacts.ts", "apps/dashboard/src/pages/admin/TradeContacts.tsx")
    print(f"COMMUNICATIONS_BROWSER_RECONSTRUCTION frontend={COMMIT} tree={TREE} paths=391")


if __name__ == "__main__":
    main()

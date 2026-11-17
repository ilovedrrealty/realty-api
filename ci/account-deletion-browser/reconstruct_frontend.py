"""Replay the already-reviewed exact frontend history into owned hosted scratch."""

import hashlib
from pathlib import Path
import subprocess
import sys


BASE = "99bc8d4c0ee4f3f0b72cf174530971f6a35f0ab3"
LIVE = "b5df6a9321365e827c01df556c09b8808151e59a"
SOURCE = "69d64e6bb7ba4d748267d22b7e215bd53089f325"
TREE = "2570e8ccc497bd03fbee402897d67cbd9ad9a294"
PHASES = (
    ("integrated-trade-frontend", "12f5b00fc5aa4040cb91c8ecc483812999c0e33e",
     "9f1817d1f744d3c57c365f18435c79051219f0df",
     "bab3d68fa5740ef914deb0b3d623feb7a9641aa160f0c502daf1078f23fcf7ae",
     "9f955c1533cbe0fde9965c7c6122f089b0bdfbd546ed2cb53557fd86d3fe1636"),
    ("newsletter-frontend", "5994fbe6d1c09b3f74000ca20261778f0c46887e",
     "d7d803a5b53f70e077ea4995ac1f1ba288a8d7da",
     "df8f19de80b143d3176409d05f5402964fe7e710e5686accc525dfe530f19e6c",
     "a2d1178b3cd61d0e00a6172395cd9330ddac7decc2a3a46016d5666a13bfb040"),
    ("newsletter-token-format", "4d0a99e0bef524fe00eab5f2c0916d91768a4efb",
     "ca59517c57b8e06ffe5fd2840340ee739e29937b",
     "ef4f871d8719f7e2a45b7ca1ea4eb4065e52eb7b71b89ea5a75e6345827a5009",
     "154685af21a73f4885ae20d266a4e67397f8124b58e9367cf8ede1327afa5404"),
    ("auth-fallback-fixture", "90b238ea2e81e2ec279cbc18546125478bffceb1",
     "0da98d76419ff0501b87b112d975037496b9583f",
     "5b723caae48f8867102646f262a6540a116ef89dca4ed8a81a0298df1ee33c20",
     "59ea851b47d3f6f012cd91109246c6a8acc65fd2bfc89dd03c71d6370da3f71a"),
    ("account-deletion-frontend", SOURCE, TREE,
     "677f123321b22aece9f7fd5b396c3d4c76457723d588190ed5c5f6b911d093b8",
     "8bde73aa77efe883779189a40540272660b778478b07681e0704e45141132c6f"),
)


def main():
    assert sys.platform == "linux" and len(sys.argv) == 6
    ops, base, live, objects, output = map(Path, sys.argv[1:])
    for path in (ops, base, live, output):
        assert path.is_absolute() and path.resolve(strict=True) == path
    assert not objects.exists() and not any(output.iterdir())

    def git(path, *arguments, data=None):
        return subprocess.check_output(["git", "-C", str(path), *arguments], input=data)

    assert git(base, "rev-parse", "HEAD").decode().strip() == BASE
    assert git(live, "rev-parse", "HEAD").decode().strip() == LIVE
    subprocess.run(["git", "init", "--quiet", str(objects)], check=True)
    (objects / ".git/objects/info/alternates").write_text(
        str(base / ".git/objects") + "\n" + str(live / ".git/objects") + "\n",
        encoding="ascii",
    )
    git(objects, "read-tree", BASE)
    previous = BASE
    for name, commit, tree, patch_sha, payload_sha in PHASES:
        patch = (ops / "ci/cumulative-landing" / (name + ".patch")).read_bytes()
        payload = (ops / "ci/cumulative-landing" / (name + ".commit")).read_bytes()
        assert hashlib.sha256(patch).hexdigest() == patch_sha
        assert hashlib.sha256(payload).hexdigest() == payload_sha
        git(objects, "apply", "--cached", "--check", "--unidiff-zero", "--whitespace=error-all", "-", data=patch)
        git(objects, "apply", "--cached", "--unidiff-zero", "--whitespace=error-all", "-", data=patch)
        assert git(objects, "write-tree").decode().strip() == tree
        assert git(objects, "hash-object", "-t", "commit", "-w", "--stdin", data=payload).decode().strip() == commit
        if name == "integrated-trade-frontend":
            assert git(objects, "rev-parse", commit + "^1", commit + "^2").decode().splitlines() == [LIVE, BASE]
        else:
            assert git(objects, "rev-parse", commit + "^").decode().strip() == previous
        previous = commit

    assert len(git(objects, "ls-tree", "-rz", SOURCE).split(b"\0")) - 1 == 575
    git(objects, "diff", "--exit-code", PHASES[2][1], SOURCE, "--", "apps/landing")
    git(objects, "update-ref", "HEAD", SOURCE)
    archive = subprocess.Popen(["git", "-C", str(objects), "archive", "--format=tar", SOURCE], stdout=subprocess.PIPE)
    unpack = subprocess.run(["tar", "-xf", "-", "-C", str(output)], stdin=archive.stdout, check=False)
    archive.stdout.close()
    assert archive.wait(timeout=15) == unpack.returncode == 0
    print(f"F09_BROWSER_FRONTEND_RECONSTRUCTED source={SOURCE} tree={TREE} paths=575", flush=True)


if __name__ == "__main__":
    main()

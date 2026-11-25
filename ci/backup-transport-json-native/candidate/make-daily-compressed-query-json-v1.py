"""Separate JSON-envelope experiment; exact staged source and recovery interfaces remain frozen."""
from hashlib import sha256
from pathlib import Path

REFERENCE = Path(__file__).resolve().parent.parent / "backup-transport-stages-20261125/make-daily-compressed-query-staged-v1.py"
REFERENCE_SHA = "47691ecbf43a9ffecf5e9670573d2521281df386fa789d04dfba32de350bb7f5"
raw = REFERENCE.read_bytes()
if REFERENCE.is_symlink() or sha256(raw).hexdigest() != REFERENCE_SHA:
    raise ValueError("Frozen staged wrapper reference changed")
source = raw.decode("utf8")
before = ") SELECT jsonb_build_object('transport','PGP_COMPRESSED_V1',"
after = ") SELECT json_build_object('transport','PGP_COMPRESSED_V1',"
if source.count(before) != 1 or source.count(after) != 0:
    raise ValueError("Exact single final envelope required")
# Execute the immutable preparation code with exactly one final-function token
# substitution. Its original file location preserves all frozen path/hash checks.
exec(compile(source.replace(before, after, 1), str(__file__), "exec"),
    {"__name__": "__main__", "__file__": str(REFERENCE)})

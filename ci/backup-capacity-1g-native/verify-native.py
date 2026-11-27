"""Execute the exact frozen full13ff pipeline; no capture logic is replaced."""
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import sys

ROOT = Path("/new-tools")
SOURCE = ROOT / "verify-native.py"
SHA = "a835e9b41edf7e29822ecca4bb67254b7cbf11701960bb702f1efd3b5995b78f"
PHASES = {"compact", "guards", "stress"}

if __name__ == "__main__":
    phase = sys.argv[1] if len(sys.argv) == 2 and sys.argv[1] in PHASES else "invalid"
    try:
        assert phase in PHASES and ROOT.resolve(strict=True) == ROOT
        assert SOURCE.is_file() and not SOURCE.is_symlink() and os.statvfs(SOURCE).f_flag & os.ST_RDONLY
        assert sha256(SOURCE.read_bytes()).hexdigest() == SHA
    except Exception:
        print("BACKUP_TRANSPORT_JSON_NATIVE_RESULT " + json.dumps({"status": "FAIL", "phase": phase,
            "stage": "capacity_source", "error_type": "CapacityRefused", "reason": "exact_readonly_json_worker_required",
            "sqlstate": "unavailable"}, sort_keys=True, separators=(",", ":")), flush=True)
        raise SystemExit(1) from None
    runpy.run_path(str(SOURCE), run_name="__main__")

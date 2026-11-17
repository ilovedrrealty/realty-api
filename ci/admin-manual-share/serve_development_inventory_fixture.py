"""Confine development mutations to one owned synthetic sale listing."""

import json
import re
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_editorial_fixture as editorial


fixture = editorial.fixture
base_migrate_and_seed = fixture.migrate_and_seed
synthetic_listing = None


def migrate_and_bind():
    global synthetic_listing
    data = base_migrate_and_seed()
    candidate = data["foreignListingId"]
    fixture.require(str(UUID(candidate, version=4)) == candidate, "Owned synthetic sale listing required")
    synthetic_listing = candidate
    return data


def confined_development_api(api):
    fixture.require(synthetic_listing is not None, "Owned synthetic sale listing required")
    baseline = editorial.confined_editorial_api(api)
    prefix = f"/admin/developments/{synthetic_listing}"
    mutations = {
        ("PUT", f"{prefix}/draft"),
        *(("POST", f"{prefix}/{operation}") for operation in ("review", "publish", "withdraw")),
    }

    async def serve(scope, receive, send):
        if scope["type"] == "http" and (scope["method"], scope["path"]) in mutations:
            return await api(scope, receive, send)
        return await baseline(scope, receive, send)

    return serve


if __name__ == "__main__":
    try:
        fixture.APP_COMMIT = editorial.pin("ILDR_DEVELOPMENT_APP_COMMIT", 40)
        fixture.APP_TREE = editorial.pin("ILDR_DEVELOPMENT_APP_TREE", 40)
        fixture.MIGRATION_HEAD = editorial.pin("ILDR_DEVELOPMENT_MIGRATION", 12)
        fixture.migrate_and_seed = migrate_and_bind
        fixture.confined_api = confined_development_api
        fixture.run()
    except Exception as error:
        kind = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind):
            kind = "UnhandledError"
        print("BROWSER_API_FIXTURE_FAILURE " + json.dumps({
            "stage": fixture.stage, "error_type": kind, "fixture_line": None,
        }), flush=True)
        raise SystemExit(1) from None

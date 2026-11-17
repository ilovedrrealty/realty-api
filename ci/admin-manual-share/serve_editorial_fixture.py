"""Run the exact editorial API with mutations confined to synthetic editorial routes."""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_admin_share_fixture as fixture


base_confined_api = fixture.confined_api
UUID = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
MUTATIONS = {
    "POST": re.compile(rf"/admin/editorial(?:/{UUID}/(?:review|publish|unpublish|restore))?"),
    "PUT": re.compile(rf"/admin/editorial/{UUID}/draft"),
}


def confined_editorial_api(api):
    baseline = base_confined_api(api)

    async def serve(scope, receive, send):
        if scope["type"] == "http":
            allowed = MUTATIONS.get(scope["method"])
            if allowed is not None and allowed.fullmatch(scope["path"]):
                return await api(scope, receive, send)
        return await baseline(scope, receive, send)

    return serve


def pin(name, size):
    value = os.environ.get(name, "")
    fixture.require(re.fullmatch(rf"[a-f0-9]{{{size}}}", value), "Exact editorial application pin required")
    return value


if __name__ == "__main__":
    try:
        fixture.APP_COMMIT = pin("ILDR_EDITORIAL_APP_COMMIT", 40)
        fixture.APP_TREE = pin("ILDR_EDITORIAL_APP_TREE", 40)
        fixture.MIGRATION_HEAD = pin("ILDR_EDITORIAL_MIGRATION", 12)
        fixture.confined_api = confined_editorial_api
        fixture.run()
    except Exception as error:
        kind = type(error).__name__
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", kind):
            kind = "UnhandledError"
        print("BROWSER_API_FIXTURE_FAILURE " + json.dumps({
            "stage": fixture.stage, "error_type": kind, "fixture_line": None,
        }), flush=True)
        raise SystemExit(1) from None

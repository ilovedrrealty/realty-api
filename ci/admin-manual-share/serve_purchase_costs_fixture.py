"""Seed one synthetic legacy worksheet and restrict writes to the reviewed browser journey."""

import json
import os
import re
import sys
from pathlib import Path
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve_admin_share_fixture as fixture


WORKSPACES = '/research-workspaces'
WORKSHEET_PATH = re.compile(WORKSPACES + r'/([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})')
WORKSHEET_TITLE = 'Synthetic purchase cost review'
LEGACY_TITLE = 'Synthetic legacy review'
seed_base = fixture.migrate_and_seed
confine_base = fixture.confined_api


def seed_purchase_costs():
    from database import SessionLocal
    from models.research_workspace import ResearchWorkspace
    from models.user import User

    base = seed_base()
    fixture.advance('seed_purchase_costs')
    accounts = {key: base['accounts'][key] for key in ('realtor', 'admin')}
    legacy_id = uuid4()
    with SessionLocal.begin() as db:
        realtor = db.query(User).filter_by(email=accounts['realtor']['email'], role='realtor').one()
        db.add(ResearchWorkspace(id=legacy_id, user_id=realtor.id, title=LEGACY_TITLE, data={
            'title': LEGACY_TITLE, 'reference': '', 'notes': 'Synthetic legacy due diligence notes',
            'checks': {}, 'item_notes': {}, 'links': [],
        }))
    return {'accounts': accounts, 'legacy_id': str(legacy_id)}


def is_owned_fixture_update(match):
    from database import SessionLocal
    from models.research_workspace import ResearchWorkspace
    from models.user import User

    with SessionLocal() as db:
        return db.query(ResearchWorkspace).join(User, User.id == ResearchWorkspace.user_id).filter(
            ResearchWorkspace.id == UUID(match.group(1)), ResearchWorkspace.title == WORKSHEET_TITLE,
            User.email == 'browser-realtor@example.com', User.role == 'realtor',
        ).first() is not None


def confined_purchase_costs(api):
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    baseline = confine_base(api)

    async def serve(scope, receive, send):
        if scope['type'] != 'http':
            return await baseline(scope, receive, send)
        method, path = scope['method'], scope['path']
        match = WORKSHEET_PATH.fullmatch(path)
        if not (method == 'POST' and path == WORKSPACES or method == 'PUT' and match):
            return await baseline(scope, receive, send)
        raw = await Request(scope, receive).body()
        try:
            body = json.loads(raw) if len(raw) <= 4096 else None
        except (ValueError, UnicodeError):
            body = None
        if (not isinstance(body, dict) or body.get('title') != WORKSHEET_TITLE
                or method == 'PUT' and not is_owned_fixture_update(match)):
            refusal = JSONResponse({'detail': 'Synthetic worksheet write refused'}, status_code=405,
                                   headers={'Cache-Control': 'no-store'})
            return await refusal(scope, receive, send)
        responded = False

        async def replay():
            nonlocal responded
            if responded:
                return await receive()
            responded = True
            return {'type': 'http.request', 'body': raw, 'more_body': False}

        return await api(scope, replay, send)

    return serve


def pin(name, digits):
    value = os.environ.get(name, '')
    fixture.require(re.fullmatch(rf'[a-f0-9]{{{digits}}}', value), 'Exact worksheet application pin required')
    return value


if __name__ == '__main__':
    try:
        fixture.APP_COMMIT = pin('ILDR_PURCHASE_COSTS_APP_COMMIT', 40)
        fixture.APP_TREE = pin('ILDR_PURCHASE_COSTS_APP_TREE', 40)
        fixture.MIGRATION_HEAD = pin('ILDR_PURCHASE_COSTS_MIGRATION', 12)
        fixture.migrate_and_seed = seed_purchase_costs
        fixture.confined_api = confined_purchase_costs
        fixture.run()
    except Exception as error:
        kind = type(error).__name__
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,62}', kind):
            kind = 'UnhandledError'
        print('BROWSER_API_FIXTURE_FAILURE ' + json.dumps({
            'stage': fixture.stage, 'error_type': kind, 'fixture_line': None,
        }), flush=True)
        raise SystemExit(1) from None

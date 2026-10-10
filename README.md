# I Love DR Realty — API

FastAPI + SQLAlchemy + Alembic backend for the I Love DR Realty platform (frontends live in the sibling repo **`realty`**). Deployed on Railway (`api.ilovedrrealty.com`) with Postgres and Storage on Supabase.

## Layout

| Path | Purpose |
|------|---------|
| `main.py` | App, CORS, security headers, rate limiter, routers, daily payout cron, `/health` |
| `config.py` | All settings (pydantic-settings) — read env vars only through here |
| `routes/` | One router per resource: auth, listings, leads, inquiries, bookings, admin, saved_homes, upgrade_requests, stats, chat, webhooks |
| `models/` `schemas/` | SQLAlchemy tables / Pydantic request-response shapes |
| `services/paypal.py` | PayPal orders, capture, Payouts, webhook verification |
| `utils/` `dependencies/` | Email (Resend), GoHighLevel sync, storage, share images; auth dependencies |
| `alembic/versions/` | Migrations (single head — check with `alembic heads`) |
| `bootstrap_db.py` | Creates core tables on a completely empty database (no migration does) |
| `seed.py` | Dev-only demo data. **Never run against production** (creates an admin with a known password) |

## Run locally

```bash
python -m venv .venv && . .venv/Scripts/activate      # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env            # set DATABASE_URL (empty local Postgres) and JWT_SECRET
python bootstrap_db.py && alembic upgrade head
python seed.py                  # optional, dev only
uvicorn main:app --reload --port 8000     # docs at /docs, health at /health
```

With `RESEND_API_KEY` empty no email is sent; the signup code is printed in the console.

## Deploy

Railway builds the `Dockerfile` (`railway.toml`). The start command is `python bootstrap_db.py && alembic upgrade head && uvicorn main:app ...`, so **migrations run automatically on every deploy** against whatever `DATABASE_URL` points at. Keep migrations additive and re-runnable. Variables are listed in `.env.example`.

## Things to know

- Auth is a 24 h JWT in the `ildr_token` cookie (shared across subdomains via `COOKIE_DOMAIN`), or a Bearer header.
- The payout cron runs inside the web process at 00:01 UTC; run a single instance only.
- No tests, CI or error monitoring exist yet.
- PayPal and GoHighLevel webhooks are rejected unless `PAYPAL_WEBHOOK_ID` / `GHL_WEBHOOK_SECRET` are set.
- Branches: work on `dev`, fast-forward `main`; confirm in Railway which branch deploys.

## Documentation

Endpoint reference, data models, env vars, architecture, runbooks and known issues are in the `realty` repo under `docs/` (clone it next to this one): start with `HANDOVER.md`, then `API.md`, `DATA_MODELS.md`, `ENV.md`.

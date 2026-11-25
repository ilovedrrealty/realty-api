"""Driver-specific liveness bounds without rewriting connection identity or TLS."""
from urllib.parse import urlsplit


def engine_options(database_url: str) -> dict:
    options = {"pool_pre_ping": True}
    # SQLAlchemy's default PostgreSQL driver here is psycopg2. Do not pass libpq
    # arguments to SQLite or another explicitly selected PostgreSQL driver.
    if urlsplit(database_url).scheme in {"postgresql", "postgresql+psycopg2"}:
        options["connect_args"] = {
            "connect_timeout": 10, "keepalives": 1, "keepalives_idle": 30,
            "keepalives_interval": 10, "keepalives_count": 3,
            "tcp_user_timeout": 60000,
        }
    return options

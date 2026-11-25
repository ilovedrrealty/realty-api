"""One owned loopback startup-handshake stall, never a worker or real server."""
import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError


def run():
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    assert re.fullmatch(r"[a-f0-9]{32}", nonce)
    assert sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
    assert os.environ.get("GITHUB_REPOSITORY") == "ilovedrrealty/realty-api"
    assert {p.name for p in Path("/sys/class/net").iterdir()} == {"lo"}
    application = Path("/application")
    assert application.resolve(strict=True) == application and os.statvfs(application).f_flag & os.ST_RDONLY
    sys.path.insert(0, str(application))
    from utils.database_connection import engine_options
    stopped = threading.Event()
    facts = {"accepted": 0, "startup_seen": False, "server_error": False}
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.2)
    port = listener.getsockname()[1]

    def stall():
        connection = None
        try:
            while not stopped.is_set():
                try:
                    connection, address = listener.accept()
                    break
                except socket.timeout:
                    continue
            if connection is not None:
                assert address[0] == "127.0.0.1"
                facts["accepted"] += 1
                connection.settimeout(0.2)
                startup = bytearray()
                while not stopped.is_set():
                    try:
                        value = connection.recv(8192)
                        if not value:
                            break
                        startup.extend(value)
                        facts["startup_seen"] = len(startup) >= 8 and startup[4:8] == b"\x00\x03\x00\x00"
                        assert len(startup) <= 8192
                    except socket.timeout:
                        continue
        except Exception:
            facts["server_error"] = True
        finally:
            if connection is not None:
                connection.close()

    thread = threading.Thread(target=stall, name="owned-startup-stall", daemon=False)
    thread.start()
    engine = None
    elapsed = None
    failed = False
    try:
        # One numeric IPv4 target, no DNS/TLS negotiation or failover target.
        # Real constructor options must override the deliberately unbounded URL.
        url = f"postgresql+psycopg2://postgres@127.0.0.1:{port}/owned?sslmode=disable&connect_timeout=0"
        options = engine_options(url)
        assert options["pool_pre_ping"] is True and options["connect_args"]["connect_timeout"] == 10
        options["connect_args"]["application_name"] = "resilience_stall_" + nonce
        engine = create_engine(url, **options)
        started = time.monotonic()
        try:
            with engine.connect():
                raise AssertionError("A silent socket cannot complete PostgreSQL startup")
        except OperationalError:
            failed = True
        elapsed = int((time.monotonic() - started) * 1000)
    finally:
        stopped.set()
        listener.close()
        thread.join(timeout=3)
        if engine is not None:
            engine.dispose()
    assert failed and type(elapsed) is int and 8000 <= elapsed <= 15000
    assert facts == {"accepted": 1, "startup_seen": True, "server_error": False} and not thread.is_alive()
    return {"status": "PASS", "case": "owned_startup_handshake_timeout", "elapsed_ms": elapsed,
            "connect_timeout_seconds": 10, "accepted_connections": 1, "startup_seen": True,
            "server_thread_joined": True, "application_queries": 0, "external_provider_attempts": 0}


if __name__ == "__main__":
    try:
        print("RESILIENCE_BLACKHOLE_RESULT " + json.dumps(run(), sort_keys=True), flush=True)
    except Exception:
        print("RESILIENCE_BLACKHOLE_FAILURE owned_startup_stall_failed", flush=True)
        raise SystemExit(1) from None

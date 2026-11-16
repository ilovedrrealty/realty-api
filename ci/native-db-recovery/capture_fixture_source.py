import argparse
from contextlib import closing
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import types
import uuid
import warnings


TABLES = tuple('activity_log alembic_version anonymous_leads bookings bulk_import_jobs deal_requests '
               'inquiries leads listing_edits listing_events listings pending_users '
               'professional_profile_revisions professional_profiles research_workspaces saved_homes '
               'settings upgrade_requests users website_settings website_settings_revisions'.split())


UNSUPPORTED = ('materialized_views', 'foreign_tables', 'partitioned_or_partition_tables')


MAX_DUMP = 16 * 1024 * 1024


MAX_TOC = 1024 * 1024


MAX_RECORDS_PER_TABLE = 1_000_000


PROFILE_COUNTS = """
SELECT count(*), count(*) FILTER (WHERE (
  user_id IS NULL AND review_status='draft' AND listing_status='hidden'
  AND published_data IS NULL AND published_at IS NULL
  AND draft_data->'public_contact_consent'='false'::jsonb
) IS NOT TRUE)
FROM public.professional_profiles
"""


class Stop(ValueError):
    """A safe reason code, never a subprocess, database, or credential string."""


def require(condition, code):
    if not condition:
        raise Stop(code)


def pg_environment(options, inherited):
    # Minimal inherited variables to run the pinned Windows executable. No inherited PG/service settings.
    env = {key: value for key, value in inherited.items()
           if key.upper() in {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE'}}
    env.update(OSTYPE='msys', LC_ALL='C', PGHOST=options['host'], PGPORT=str(options['port']),
               PGDATABASE=options['dbname'], PGUSER=options['user'], PGSSLMODE='verify-full', PGGSSENCMODE='disable',
               PGSSLROOTCERT=options['sslrootcert'], PGCONNECT_TIMEOUT=str(options['connect_timeout']),
               PGOPTIONS=options['options'])
    return env


def private_process(command, *, env, limit, timeout, payload=None, cwd=None, allow_unused_input=False):
    """Exchange bytes only through private local pipes and discard stderr at the OS boundary."""
    detached = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    data = bytearray()
    failure = []
    with subprocess.Popen(command, cwd=cwd, env=env, close_fds=True, bufsize=0,
                          stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE if limit else subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, **detached) as child:
        def read_pipe():
            try:
                with child.stdout as stream:
                    while chunk := stream.read(65536):
                        if len(data) + len(chunk) > limit:
                            failure.append('CHILD_OUTPUT_LIMIT')
                            child.kill()
                            return
                        data.extend(chunk)
            except OSError:
                failure.append('CHILD_READ_FAILED')

        def write_pipe():
            try:
                with child.stdin as stream, memoryview(payload) as view:
                    offset = 0
                    while offset < len(view):
                        written = stream.write(view[offset:offset + 65536])
                        if not written:
                            failure.append('CHILD_INPUT_FAILED')
                            return
                        offset += written
            except BrokenPipeError:
                if not allow_unused_input:
                    failure.append('CHILD_INPUT_FAILED')
            except OSError:
                failure.append('CHILD_INPUT_FAILED')

        threads = []
        try:
            for enabled, function in ((limit, read_pipe), (payload is not None, write_pipe)):
                if enabled:
                    thread = threading.Thread(target=function, daemon=True)
                    thread.start()
                    threads.append(thread)
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Stop('CHILD_TIMEOUT') from None
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)
            require(not any(thread.is_alive() for thread in threads), 'CHILD_PIPE_DID_NOT_CLOSE')
        require(not failure, failure[0] if failure else 'CHILD_PIPE_FAILED')
        require(child.returncode == 0, 'CHILD_FAILED')
    return data


def checked_catalog(catalog):
    require(catalog['project_ref'] == PROJECT and catalog['definition_sha256'] == CATALOG_SHA
            and catalog['alembic_heads'] == [HEAD] and catalog['table_names'] == list(TABLES), 'PRODUCTION_SCHEMA_DRIFT')
    require(all(catalog[key] == [] for key in UNSUPPORTED), 'UNSUPPORTED_PUBLIC_OBJECTS')


def snapshot_plan(cur, sql, catalog_source):
    cur.execute("SELECT current_setting('transaction_read_only'), current_setting('transaction_isolation'), "
                "current_setting('row_security'), current_setting('server_version_num')::int, current_database()")
    readonly, isolation, rls, server, database = cur.fetchone()
    require((readonly, isolation, rls, database) == ('on', 'repeatable read', 'off', 'postgres')
            and server // 10000 == 17, 'DATABASE_SESSION_INVARIANT')
    cur.execute('SELECT pg_catalog.pg_export_snapshot(), transaction_timestamp()')
    snapshot, captured_at = cur.fetchone()
    require(re.fullmatch(r'[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+', snapshot), 'SNAPSHOT_ID_INVALID')
    # Fetch metadata only. The server keeps catalog definitions and computes the original frozen digest.
    fields = ('project_ref', 'definition_sha256', 'alembic_heads', 'table_names', *UNSUPPORTED)
    projection = sql.SQL(', ').join(sql.SQL('{}::text, catalog->{}').format(sql.Literal(field), sql.Literal(field))
                                     for field in fields)
    cur.execute(sql.SQL('SELECT jsonb_build_object({}) FROM ({}) AS frozen(catalog)').format(
        projection, sql.SQL(catalog_source)))
    checked_catalog(cur.fetchone()[0])
    counts = {}
    for table in TABLES:
        cur.execute(sql.SQL('SELECT count(*) FROM public.{}').format(sql.Identifier(table)))
        counts[table] = cur.fetchone()[0]
        require(0 <= counts[table] <= MAX_RECORDS_PER_TABLE, 'PUBLIC_ROW_LIMIT')
    cur.execute(PROFILE_COUNTS)
    profiles, profiles_outside_scope = cur.fetchone()
    require(profiles == counts['professional_profiles'] and profiles_outside_scope == 0, 'PROFILE_PRIVACY_DRIFT')
    return snapshot, {
        'captured_at_utc': captured_at.astimezone(timezone.utc).isoformat(),
        'server_version_num': server, 'alembic_head': HEAD, 'public_catalog_definition_sha256': CATALOG_SHA,
        'public_table_record_counts': counts, 'strictly_private_profile_count': profiles,
        'same_read_only_snapshot_for_public_rows': True, 'verified_tls': True,
    }


def validate_toc(toc, tables):
    entries = []
    for line in toc.splitlines():
        if re.match(rb'^\d+; \d+ \d+ TABLE DATA ', line):
            match = re.fullmatch(rb'\d+; \d+ \d+ TABLE DATA public ([a-z_][a-z0-9_]*) .+', line)
            require(match, 'UNEXPECTED_ARCHIVE_TABLE_SCOPE')
            entries.append(match[1].decode('ascii'))
    require(sorted(entries) == sorted(tables), 'ARCHIVE_TABLE_DATA_COVERAGE')


def pg_dump_credentials(password):
    require(password and not any(ord(char) < 32 or ord(char) == 127 for char in password), 'PASSWORD_INPUT_INVALID')
    payload = bytearray(password, 'utf-8')
    if len(payload) > 512:
        payload[:] = b'\x00' * len(payload)
        raise Stop('PASSWORD_INPUT_INVALID')
    payload.append(10)
    return payload


def capture(host, user, catalog_source):
    require(not any(key.upper().startswith('PG') for key in os.environ), 'AMBIENT_POSTGRES_SETTINGS_PRESENT')
    sys.path.insert(0, str(DEP))
    import psycopg2
    from psycopg2 import sql

    connection = dict(host=host, port=5432, dbname='postgres', user=user, sslmode='verify-full', gssencmode='disable',
                      sslrootcert=str(CERT), connect_timeout=20,
                      options='-c default_transaction_read_only=on -c row_security=off '
                              '-c statement_timeout=120000 -c lock_timeout=15000 '
                              '-c idle_in_transaction_session_timeout=420000')
    env = pg_environment(connection, os.environ)
    require(sys.stdin.isatty() and sys.stdout.isatty(), 'OWNER_WINDOWS_TERMINAL_REQUIRED')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        password = getpass.getpass('Supabase database password (hidden). Press Ctrl+C to cancel: ')
    payload = pg_dump_credentials(password)
    try:
        try:
            conn = psycopg2.connect(**connection, password=password)
        except psycopg2.Error:
            raise Stop('DATABASE_CONNECT_FAILED') from None
        finally:
            password = None
        with closing(conn), conn, conn.cursor() as cursor:
            require(conn.info.ssl_in_use, 'TLS_SESSION_REQUIRED')
            conn.set_session(isolation_level='REPEATABLE READ', readonly=True, autocommit=False)
            snapshot, report = snapshot_plan(cursor, sql, catalog_source)
            print('Reading the verified application database snapshot. The archive stays in memory until encrypted.', flush=True)
            dump = private_process([str(PG_DUMP), '--password', '--format=custom', '--schema=public',
                                    '--strict-names', '--lock-wait-timeout=15000', f'--snapshot={snapshot}'],
                                   env=env, payload=payload, limit=MAX_DUMP, timeout=300, cwd=BIN)
    finally:
        payload[:] = b'\x00' * len(payload)
    require(dump.startswith(b'PGDMP'), 'PG_CUSTOM_ARCHIVE_REQUIRED')
    # No database connection. The first pass checks table coverage. The second decompresses all archive data.
    toc = private_process([str(PG_RESTORE), '--list'], env=env, payload=dump,
                          limit=MAX_TOC, timeout=60, cwd=BIN, allow_unused_input=True)
    validate_toc(toc, TABLES)
    private_process([str(PG_RESTORE), '--file=-'], env=env, payload=dump, limit=0, timeout=90, cwd=BIN)
    return dump, report

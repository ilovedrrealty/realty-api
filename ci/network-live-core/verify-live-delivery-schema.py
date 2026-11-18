"""Compare fresh/live-upgrade catalogs and exercise a compatible old application.

Hosted-only, marked disposable PostgreSQL; no production connection or provider
credentials are accepted. Contact-bearing snapshots stay in process memory.
"""

from contextlib import closing
from hashlib import sha1, sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


APPLICATION = Path("/application")
LIVE = Path("/live")
ROLLBACK = Path("/rollback")
APP_COMMIT = "01695530df670a94ac28f8b3bf36cc34b5b7a942"
APP_TREE = "e88ea0c57f9fac39545322532a5992d5a2927676"
LIVE_COMMIT = "7f63fec927f75af3f901f74297973253ad5c743b"
LIVE_TREE = "1a87c3d4c96e2df0c956e18f0f26c982e7ef3289"
HEAD = "b19d8e4c720a"
LIVE_HEAD = "d721f0b6a84c"
LIVE_TABLES = ('activity_log',
 'alembic_version',
 'anonymous_leads',
 'bookings',
 'bulk_import_jobs',
 'deal_requests',
 'inquiries',
 'lead_delivery_jobs',
 'lead_delivery_worker_state',
 'leads',
 'listing_edits',
 'listing_events',
 'listings',
 'pending_users',
 'professional_profile_revisions',
 'professional_profiles',
 'research_workspaces',
 'saved_homes',
 'settings',
 'trade_contact_sources',
 'trade_contacts',
 'upgrade_requests',
 'users',
 'website_settings',
 'website_settings_revisions')
EXPECTED_TABLES = ('activity_log',
 'alembic_version',
 'anonymous_leads',
 'bookings',
 'bulk_import_jobs',
 'client_shortlist_grants',
 'client_shortlist_items',
 'client_shortlists',
 'deal_requests',
 'development_inventories',
 'development_inventory_events',
 'development_inventory_revisions',
 'editorial_events',
 'editorial_pages',
 'editorial_revisions',
 'inquiries',
 'lead_delivery_jobs',
 'lead_delivery_worker_state',
 'leads',
 'listing_arrival_guides',
 'listing_daily_views',
 'listing_edits',
 'listing_events',
 'listing_floor_plans',
 'listing_location_review_revisions',
 'listing_location_reviews',
 'listing_performance_entries',
 'listing_view_coverage',
 'listings',
 'network_audit',
 'network_blocks',
 'network_due_work_actions',
 'network_due_work_dispositions',
 'network_due_work_preferences',
 'network_history_share_actions',
 'network_history_shares',
 'network_maintenance_actions',
 'network_maintenance_occurrences',
 'network_maintenance_preferences',
 'network_maintenance_schedules',
 'network_media',
 'network_milestones',
 'network_post_comments',
 'network_posts',
 'network_project_comments',
 'network_project_members',
 'network_project_updates',
 'network_projects',
 'network_properties',
 'network_property_custody_actions',
 'network_property_custody_transfers',
 'network_service_actions',
 'network_service_conversation_messages',
 'network_service_favorites',
 'network_service_negotiation_messages',
 'network_service_preferences',
 'network_service_proposals',
 'network_service_recipients',
 'network_service_referral_events',
 'network_service_referrals',
 'network_service_requests',
 'network_service_reviews',
 'network_service_work',
 'network_service_work_actions',
 'pending_users',
 'professional_profile_revisions',
 'professional_profiles',
 'property_management_records',
 'property_management_revisions',
 'property_viewings',
 'research_workspaces',
 'saved_homes',
 'seller_valuation_comparables',
 'seller_valuation_follow_ups',
 'seller_valuation_intakes',
 'seller_valuation_workspaces',
 'settings',
 'social_network_profiles',
 'trade_contact_sources',
 'trade_contacts',
 'transaction_document_versions',
 'transaction_documents',
 'transaction_room_events',
 'transaction_room_grants',
 'transaction_room_milestones',
 'transaction_room_tasks',
 'transaction_rooms',
 'upgrade_requests',
 'users',
 'viewing_itineraries',
 'website_settings',
 'website_settings_revisions')
NEW_TABLES = ('client_shortlist_grants',
 'client_shortlist_items',
 'client_shortlists',
 'development_inventories',
 'development_inventory_events',
 'development_inventory_revisions',
 'editorial_events',
 'editorial_pages',
 'editorial_revisions',
 'listing_arrival_guides',
 'listing_daily_views',
 'listing_floor_plans',
 'listing_location_review_revisions',
 'listing_location_reviews',
 'listing_performance_entries',
 'listing_view_coverage',
 'network_audit',
 'network_blocks',
 'network_due_work_actions',
 'network_due_work_dispositions',
 'network_due_work_preferences',
 'network_history_share_actions',
 'network_history_shares',
 'network_maintenance_actions',
 'network_maintenance_occurrences',
 'network_maintenance_preferences',
 'network_maintenance_schedules',
 'network_media',
 'network_milestones',
 'network_post_comments',
 'network_posts',
 'network_project_comments',
 'network_project_members',
 'network_project_updates',
 'network_projects',
 'network_properties',
 'network_property_custody_actions',
 'network_property_custody_transfers',
 'network_service_actions',
 'network_service_conversation_messages',
 'network_service_favorites',
 'network_service_negotiation_messages',
 'network_service_preferences',
 'network_service_proposals',
 'network_service_recipients',
 'network_service_referral_events',
 'network_service_referrals',
 'network_service_requests',
 'network_service_reviews',
 'network_service_work',
 'network_service_work_actions',
 'property_management_records',
 'property_management_revisions',
 'property_viewings',
 'seller_valuation_comparables',
 'seller_valuation_follow_ups',
 'seller_valuation_intakes',
 'seller_valuation_workspaces',
 'social_network_profiles',
 'transaction_document_versions',
 'transaction_documents',
 'transaction_room_events',
 'transaction_room_grants',
 'transaction_room_milestones',
 'transaction_room_tasks',
 'transaction_rooms',
 'viewing_itineraries')
ADDED_COLUMNS = (('listing_edits', 'room_photo_tour_base_gallery_sha256', 'text', False, None),
 ('listings', 'room_photo_tour_snapshot', 'jsonb', False, None))
ADDED_TRIGGERS = {('leads', 'transaction_room_lead_changed'),
 ('listings', 'history_share_scope_changed'),
 ('listings', 'listing_arrival_guide_owner_changed'),
 ('listings', 'property_viewing_listing_changed'),
 ('users', 'client_shortlist_assignment_changed'),
 ('users', 'history_share_scope_changed')}
ROLLBACK_RESULTS = {'seed_live': {'status': 'PASS',
               'stage': 'seed_live',
               'historical_users': 4,
               'historical_leads': 3,
               'historical_inquiries': 1,
               'historical_bookings': 1,
               'configured_team_recipients': 4,
               'retained_accepted_jobs': 9,
               'worker_rows': 1,
               'owner_trade_contacts': 82,
               'trade_contacts': 85,
               'trade_sources': 86,
               'private_profiles': 46,
               'cms_version': 7},
 'seed_candidate': {'status': 'PASS',
                    'stage': 'seed_candidate',
                    'phone_only_inquiries': 1,
                    'email_only_inquiries': 1,
                    'consented_property_inquiries': 1,
                    'verified_signups': 1,
                    'linked_bookings': 1,
                    'delivery_jobs': 39,
                    'customer_email_jobs': 3,
                    'network_records': 8},
 'verify_old': {'status': 'PASS',
                'stage': 'verify_old',
                'role_read_checks': 13,
                'old_client_intakes': 1,
                'fake_owner_notices': 1,
                'head_preserved': True,
                'delivery_jobs_unchanged': True,
                'no_historical_replay': True}}

SOURCE_REVIEWED_SERVER_TABLES = ('listing_location_review_revisions',
 'listing_location_reviews',
 'network_audit',
 'network_blocks',
 'network_media',
 'network_milestones',
 'network_post_comments',
 'network_posts',
 'network_project_comments',
 'network_project_members',
 'network_project_updates',
 'network_projects',
 'network_properties',
 'network_service_actions',
 'network_service_preferences',
 'network_service_proposals',
 'network_service_recipients',
 'network_service_requests',
 'network_service_work',
 'network_service_work_actions',
 'property_management_records',
 'property_management_revisions',
 'social_network_profiles')
SOURCE_REVIEWED_SERVER_PRIVILEGES = ('DELETE', 'INSERT', 'MAINTAIN', 'REFERENCES', 'SELECT', 'TRIGGER', 'TRUNCATE', 'UPDATE')

stage = "isolation"


class RehearsalFailure(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise RehearsalFailure(reason)


def advance(name):
    global stage
    stage = name


def git(root, *arguments):
    return subprocess.check_output(
        ["git", "--no-optional-locks", "-c", f"safe.directory={root}", "-C", str(root), *arguments],
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1",
             "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"},
        stderr=subprocess.DEVNULL, timeout=15,
    )


def verify_source(root, commit, tree):
    require(root.resolve(strict=True) == root and os.statvfs(root).f_flag & os.ST_RDONLY,
            "read_only_source_required")
    require(git(root, "rev-parse", "HEAD", "HEAD^{tree}").decode().splitlines() == [commit, tree]
            and not git(root, "status", "--porcelain=v1", "--untracked-files=all")
            and not (root / ".env").exists() and not (root / ".env").is_symlink(),
            "exact_clean_source_required")


def verify_rollback_archive():
    require(ROLLBACK.resolve(strict=True) == ROLLBACK and os.statvfs(ROLLBACK).f_flag & os.ST_RDONLY,
            "read_only_rollback_required")
    names = set()
    for entry in git(LIVE, "ls-tree", "-rz", "HEAD").split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        name = raw_name.decode("utf-8")
        require(mode in (b"100644", b"100755") and kind == b"blob", "rollback_plain_files_required")
        path = ROLLBACK / name
        require(path.is_file() and not path.is_symlink(), "rollback_source_file_required")
        data = path.read_bytes()
        actual = sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest().encode()
        require(actual == object_id, "rollback_live_blob_mismatch")
        names.add(name)
    additions = git(APPLICATION, "diff", "--name-only", "--diff-filter=A", LIVE_COMMIT, APP_COMMIT,
                    "--", "alembic/versions").decode().splitlines()
    require(len(additions) == 32, "rollback_revision_count_required")
    for name in additions:
        require(name.startswith("alembic/versions/") and name.endswith(".py")
                and (ROLLBACK / name).read_bytes() == git(APPLICATION, "cat-file", "blob", APP_COMMIT + ":" + name),
                "rollback_current_revision_required")
        names.add(name)
    actual = set()
    for path in ROLLBACK.rglob("*"):
        require(not path.is_symlink(), "rollback_symlink_refused")
        if path.is_file():
            actual.add(path.relative_to(ROLLBACK).as_posix())
    require(actual == names and not (ROLLBACK / ".env").exists(), "rollback_extra_file_refused")


def environment(database):
    values = {
        "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1",
        "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "ilovedrrealty/realty-api",
        "ILDR_BROWSER_CI_NONCE": os.environ["ILDR_BROWSER_CI_NONCE"],
        "DATABASE_URL": f"postgresql+psycopg2://postgres@127.0.0.1:55449/{database}",
        "JWT_SECRET": "synthetic-live-communications-only", "ENVIRONMENT": "development",
        "COOKIE_DOMAIN": "", "ALLOWED_ORIGINS": "http://127.0.0.1:8917,http://127.0.0.1:4175",
        "LANDING_URL": "http://127.0.0.1:8917", "RESEND_API_KEY": "", "EMAIL_FROM": "rehearsal@example.com",
        "NOTIFY_EMAIL": "", "GHL_ENABLED": "false", "GHL_API_KEY": "", "GHL_LOCATION_ID": "",
        "GHL_WEBHOOK_SECRET": "", "LEAD_DELIVERY_ENABLED": "false", "CUSTOMER_MESSAGES_ENABLED": "false",
        "PROPERTY_INQUIRY_REQUIRE_CONSENT": "false", "SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": "",
        "PAYPAL_CLIENT_ID": "", "PAYPAL_CLIENT_SECRET": "", "PAYPAL_WEBHOOK_ID": "",
    }
    return values


def native(root, database, *arguments, allow_failure=False):
    # Isolated Python intentionally omits the script directory from sys.path.
    # Restore only this already-verified, read-only application root for the
    # bootstrap entrypoint's local imports. Alembic uses prepend_sys_path=.
    if arguments == ("bootstrap_db.py",):
        arguments = (
            "-c",
            "import runpy,sys; root=sys.argv[1]; sys.path.insert(0,root); "
            "runpy.run_path(root+'/bootstrap_db.py',run_name='__main__')",
            str(root),
        )
    result = subprocess.run(
        [sys.executable, "-I", "-B", *arguments], cwd=root, env=environment(database),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=150,
    )
    require(len(result.stdout) <= 4 * 1024 * 1024, "bounded_native_output_required")
    require(allow_failure or result.returncode == 0, "native_phase_failed")
    return result


def rollback_mode(database, mode):
    roots = {"seed_live": LIVE, "seed_candidate": APPLICATION, "verify_old": ROLLBACK}
    process = native(roots[mode], database, "/tools/verify-live-delivery-rollback.py", mode, allow_failure=True)
    prefix = b"LIVE_DELIVERY_ROLLBACK_STAGE "
    rows = [json.loads(line[len(prefix):]) for line in process.stdout.splitlines() if line.startswith(prefix)]
    require(len(rows) == 1 and isinstance(rows[0], dict) and rows[0].get("stage") == mode,
            "exact_rollback_stage_required")
    row = rows[0]
    if row.get("status") == "FAIL":
        reason = row.get("reason", "failed")
        require(isinstance(reason, str) and re.fullmatch(r"[a-z_]{1,50}", reason), "safe_rollback_failure_required")
        raise RehearsalFailure("rollback_" + mode + "_" + reason)
    require(row.get("status") == "PASS" and process.returncode == 0,
            "successful_rollback_stage_required")
    require(all(re.fullmatch(r"[a-z_]{1,50}", key) for key in row), "safe_rollback_fields_required")
    require(all(type(value) in (bool, int) and 0 <= value <= 1000
                for key, value in row.items() if key not in {"status", "stage"}),
            "aggregate_rollback_values_required")
    require(row == ROLLBACK_RESULTS[mode], "exact_rollback_aggregate_required")
    return rows[0]


def connection(database):
    import psycopg2

    return psycopg2.connect(host="127.0.0.1", port=55449, dbname=database, user="postgres", password="",
                            connect_timeout=4, options="-cstatement_timeout=10000")


def create_database(name, nonce, purpose):
    from psycopg2 import sql

    with closing(connection("postgres")) as admin:
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute("SELECT current_setting('server_version_num')::int, host(inet_server_addr()), inet_server_port()")
            require(cursor.fetchone() == (170006, "127.0.0.1", 55449), "exact_loopback_postgres_required")
            cursor.execute("SELECT count(*) FROM pg_database WHERE datname=%s", (name,))
            require(cursor.fetchone() == (0,), "fresh_owned_database_required")
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            cursor.execute(sql.SQL("COMMENT ON DATABASE {} IS %s").format(sql.Identifier(name)),
                           (f"ildr-live-communications-ci:{nonce}:{purpose}",))
    with closing(connection(name)) as db, db.cursor() as cursor:
        cursor.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role")
        db.commit()


def query(database, statement, params=()):
    with closing(connection(database)) as db, db.cursor() as cursor:
        cursor.execute(statement, params)
        return cursor.fetchall()


def table_names(database):
    return tuple(row[0] for row in query(database, """
        SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename
    """))


def catalog(database, tables):
    require(tables and all(re.fullmatch(r"[a-z_]+", name) for name in tables), "catalog_table_allowlist_required")
    selected = list(tables)
    definitions = {
        "columns": """SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
            pg_get_expr(d.adbin,d.adrelid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname,a.attname""",
        "constraints": """SELECT c.relname,k.conname,k.contype,pg_get_constraintdef(k.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_constraint k ON k.conrelid=c.oid
            WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname,k.conname""",
        "indexes": """SELECT t.relname,i.relname,x.indisprimary,x.indisunique,pg_get_indexdef(i.oid)
            FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace JOIN pg_index x ON x.indrelid=t.oid
            JOIN pg_class i ON i.oid=x.indexrelid WHERE n.nspname='public' AND t.relname=ANY(%s)
            ORDER BY t.relname,i.relname""",
        "rls": """SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname""",
        "acl": """SELECT c.relname,coalesce(r.rolname,'PUBLIC'),a.privilege_type,a.is_grantable
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
            LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE n.nspname='public' AND c.relname=ANY(%s)
            ORDER BY c.relname,coalesce(r.rolname,'PUBLIC'),a.privilege_type""",
        "policies": """SELECT c.relname,p.polname,pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid)
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_policy p ON p.polrelid=c.oid
            WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY c.relname,p.polname""",
    }
    definitions["triggers"] = """SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_trigger t ON t.tgrelid=c.oid WHERE n.nspname='public' AND c.relname=ANY(%s)
        AND NOT t.tgisinternal ORDER BY c.relname,t.tgname"""
    result = {key: query(database, sql, (selected,)) for key, sql in definitions.items()}
    result["functions"] = query(database, """SELECT p.proname,pg_get_function_identity_arguments(p.oid),
        pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prokind='f' ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)""")
    result["enums"] = query(database, """SELECT t.typname,e.enumlabel,e.enumsortorder::text FROM pg_type t
        JOIN pg_namespace n ON n.oid=t.typnamespace JOIN pg_enum e ON e.enumtypid=t.oid
        WHERE n.nspname='public' ORDER BY t.typname,e.enumsortorder""")
    return result


def snapshot(database, tables, columns=None, sequence_names=None):
    from psycopg2 import sql

    result = {}
    with closing(connection(database)) as db, db.cursor() as cursor:
        for table in tables:
            if table == "alembic_version":
                continue
            projection = sql.SQL("*") if columns is None else sql.SQL(",").join(sql.Identifier(name) for name in columns[table])
            cursor.execute(sql.SQL("SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb)::text "
                                   "FROM (SELECT {} FROM public.{}) t").format(projection, sql.Identifier(table)))
            result[table] = cursor.fetchone()[0]
        cursor.execute("SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename")
        sequences = cursor.fetchall()
        result["sequences"] = sequences if sequence_names is None else [row for row in sequences if row[0] in sequence_names]
    return result


def preserved_catalog(before, after):
    """Only the two reviewed nullable columns and six new old-table triggers."""
    old_columns = set(before["columns"])
    require(set(after["columns"]) - old_columns == set(ADDED_COLUMNS)
            and old_columns <= set(after["columns"]), "only_reviewed_columns_added_required")
    old_triggers = set(before["triggers"])
    extra = set(after["triggers"]) - old_triggers
    added_pairs = {(row[0], row[1]) for row in extra}
    old_preserved = old_triggers <= set(after["triggers"])
    if not old_preserved or added_pairs != ADDED_TRIGGERS:
        differences = {
            "missing_old": sorted((row[0], row[1]) for row in old_triggers - set(after["triggers"])),
            "missing_added": sorted(ADDED_TRIGGERS - added_pairs),
            "unexpected_added": sorted(added_pairs - ADDED_TRIGGERS),
        }
        require(all(len(pairs) <= 100 and all(table in LIVE_TABLES
                    and re.fullmatch(r"[a-z_]{1,63}", name) for table, name in pairs)
                    for pairs in differences.values()), "bounded_trigger_catalog_diagnostic_required")
        print("NETWORK_OLD_TRIGGER_DIFFERENCES " + json.dumps(differences, sort_keys=True,
              separators=(",", ":")), flush=True)
    require(old_preserved and added_pairs == ADDED_TRIGGERS,
            "only_reviewed_triggers_added_required")
    for key in ("constraints", "indexes", "rls", "acl", "policies", "enums"):
        require(after[key] == before[key], "retained_catalog_definition_changed")
    require(set(before["functions"]) <= set(after["functions"]), "retained_function_changed")


def verify_private(value):
    require(value["rls"] == [(name, True, False) for name in NEW_TABLES], "private_rls_required")
    require(not value["policies"] and not any(row[1] in {"PUBLIC", "anon", "authenticated"}
                                             for row in value["acl"]), "private_client_data_api_grants_required")
    # The six early revisions retain the existing trusted server defaults.
    # Require their exact non-grantable matrix; all other new tables deny it.
    expected_server = {(table, "service_role", privilege, False)
                       for table in SOURCE_REVIEWED_SERVER_TABLES
                       for privilege in SOURCE_REVIEWED_SERVER_PRIVILEGES}
    actual_server = {row for row in value["acl"] if row[1] == "service_role"}
    require(actual_server == expected_server, "source_reviewed_server_acl_required")


def retained_fixture(database):
    actual = query(database, """SELECT
        (SELECT count(*) FROM trade_contacts WHERE details->>'verification_status'='owner_source'),
        (SELECT count(*) FROM trade_contacts), (SELECT count(*) FROM trade_contact_sources),
        (SELECT count(*) FROM professional_profiles WHERE user_id IS NULL AND review_status='draft'
          AND listing_status='hidden' AND published_data IS NULL AND published_at IS NULL
          AND draft_data->>'public_contact_consent'='false'),
        (SELECT version FROM website_settings WHERE id=1),
        (SELECT count(*) FROM website_settings_revisions),
        (SELECT count(*) FROM lead_delivery_jobs WHERE lead_id='00000000-0000-0000-0000-00000000012f'
          AND status='accepted' AND attempt_count=1),
        (SELECT count(*) FROM lead_delivery_worker_state)""")
    expected = [(82, 85, 86, 46, 7, 8, 9, 1)]
    if actual != expected:
        names = ("owner_trade_contacts", "trade_contacts", "trade_sources", "private_profiles",
                 "cms_version", "cms_revisions", "retained_accepted_jobs", "worker_rows")
        require(len(actual) == 1 and len(actual[0]) == len(names)
                and all(type(value) is int and 0 <= value <= 1000 for value in actual[0]),
                "bounded_retained_fixture_counts_required")
        print("NETWORK_RETAINED_FIXTURE_COUNTS " + json.dumps(dict(zip(names, actual[0])),
              sort_keys=True, separators=(",", ":")), flush=True)
    require(actual == expected, "retained_current_fixture_required")


def identity():
    require(sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_REPOSITORY") == "ilovedrrealty/realty-api"
            and {path.name for path in Path("/sys/class/net").iterdir()} == {"lo"}, "hosted_loopback_required")
    nonce = os.environ.get("ILDR_BROWSER_CI_NONCE", "")
    require(re.fullmatch(r"[a-f0-9]{32}", nonce), "owned_nonce_required")
    verify_source(APPLICATION, APP_COMMIT, APP_TREE)
    verify_source(LIVE, LIVE_COMMIT, LIVE_TREE)
    verify_rollback_archive()
    return nonce


def run():
    nonce = identity()
    fresh = f"ildr_f01_fresh_{nonce}"
    upgrade = f"ildr_f01_upgrade_{nonce}"
    advance("fresh_database")
    create_database(fresh, nonce, "fresh")
    create_database(upgrade, nonce, "upgrade")
    advance("fresh_bootstrap")
    native(APPLICATION, fresh, "bootstrap_db.py")
    native(APPLICATION, fresh, "-m", "alembic", "upgrade", "head")
    require(table_names(fresh) == EXPECTED_TABLES, "exact_92_tables_required")
    require(query(fresh, "SELECT version_num FROM alembic_version") == [(HEAD,)], "fresh_head_required")
    require(query(fresh, "SELECT (SELECT count(*) FROM lead_delivery_jobs),(SELECT count(*) FROM lead_delivery_worker_state)") == [(0, 0)],
            "fresh_delivery_tables_empty_required")
    fresh_full_catalog = catalog(fresh, EXPECTED_TABLES)
    verify_private(catalog(fresh, NEW_TABLES))
    advance("live_bootstrap")
    native(LIVE, upgrade, "bootstrap_db.py")
    native(LIVE, upgrade, "-m", "alembic", "upgrade", "head")
    require(query(upgrade, "SELECT version_num FROM alembic_version") == [(LIVE_HEAD,)]
            and table_names(upgrade) == LIVE_TABLES, "exact_current_25_live_required")
    baseline = rollback_mode(upgrade, "seed_live")
    old_catalog = catalog(upgrade, LIVE_TABLES)
    old_columns = {table: [row[1] for row in old_catalog["columns"] if row[0] == table] for table in LIVE_TABLES}
    old_rows = snapshot(upgrade, LIVE_TABLES, old_columns)
    old_sequences = [row[0] for row in old_rows["sequences"]]
    retained_fixture(upgrade)
    advance("live_upgrade")
    native(APPLICATION, upgrade, "-m", "alembic", "upgrade", "head")
    require(table_names(upgrade) == EXPECTED_TABLES
            and query(upgrade, "SELECT version_num FROM alembic_version") == [(HEAD,)], "upgraded_head_required")
    require(snapshot(upgrade, LIVE_TABLES, old_columns, old_sequences) == old_rows,
            "all_old_column_rows_sequences_unchanged_required")
    preserved_catalog(old_catalog, catalog(upgrade, LIVE_TABLES))
    retained_fixture(upgrade)
    upgraded_full_catalog = catalog(upgrade, EXPECTED_TABLES)
    require(upgraded_full_catalog == fresh_full_catalog, "all_92_fresh_upgrade_catalogs_equal_required")
    verify_private(catalog(upgrade, NEW_TABLES))
    advance("candidate_data")
    seeded = rollback_mode(upgrade, "seed_candidate")
    current = snapshot(upgrade, EXPECTED_TABLES)
    retained_fixture(upgrade)
    advance("repeat_normal_startup")
    native(APPLICATION, upgrade, "bootstrap_db.py")
    native(APPLICATION, upgrade, "-m", "alembic", "upgrade", "head")
    require(snapshot(upgrade, EXPECTED_TABLES) == current
            and catalog(upgrade, EXPECTED_TABLES) == upgraded_full_catalog, "normal_repeat_preservation_required")
    advance("compatible_old_startup")
    native(ROLLBACK, upgrade, "bootstrap_db.py")
    native(ROLLBACK, upgrade, "-m", "alembic", "upgrade", "head")
    require(snapshot(upgrade, EXPECTED_TABLES) == current, "compatible_old_startup_no_mutation_required")
    advance("compatible_old_runtime")
    rolled_back = rollback_mode(upgrade, "verify_old")
    after = snapshot(upgrade, EXPECTED_TABLES)
    require(all(after[name] == current[name] for name in current if name != "leads"), "rollback_preserved_records_required")
    before_leads = json.loads(current["leads"])
    after_leads = json.loads(after["leads"])
    require(len(after_leads) == len(before_leads) + 1 and all(row in after_leads for row in before_leads),
            "one_old_client_intake_only_required")
    retained_fixture(upgrade)
    require(catalog(upgrade, EXPECTED_TABLES) == upgraded_full_catalog
            and query(upgrade, "SELECT version_num FROM alembic_version") == [(HEAD,)], "rollback_full_schema_preserved_required")
    return {"status": "PASS", "application_commit": APP_COMMIT, "application_tree": APP_TREE,
            "live_commit": LIVE_COMMIT, "alembic_head": HEAD, "table_count": 92,
            "fresh_upgrade_catalog_equal": True, "old_column_rows_sequences_unchanged": True,
            "reviewed_added_columns": 2, "reviewed_added_old_table_triggers": 6,
            "new_private_tables": len(NEW_TABLES), "public_member_denied_tables": len(NEW_TABLES),
            "source_reviewed_service_role_tables": len(SOURCE_REVIEWED_SERVER_TABLES),
            "source_reviewed_service_role_grants": 184, "repeat_normal_startup_preserved": True,
            "compatible_old_source_started": True, "compatible_old_runtime": rolled_back,
            "baseline": baseline, "new_code_data": seeded,
            "retained_accepted_jobs": 9, "retained_worker_rows": 1,
            "external_provider_requests": 0,
            "full_catalog_sha256": sha256(json.dumps(fresh_full_catalog, sort_keys=True).encode()).hexdigest()}


def archive_catalog_diagnostic(source, target, before, after):
    """Schema metadata only, emitted solely when exact restore equality fails."""
    render_sql = """SELECT current_setting('search_path'), current_setting('quote_all_identifiers'),
        current_setting('standard_conforming_strings'), current_setting('server_version_num')"""
    report = {"scope": "schema_metadata_only", "source_render_settings": query(source, render_sql)[0],
              "target_render_settings": query(target, render_sql)[0], "components": {}}
    for category in before:
        left, right = before[category], after[category]
        left_only, right_only = sorted(set(left) - set(right), key=repr), sorted(set(right) - set(left), key=repr)
        component = {"source_count": len(left), "target_count": len(right), "ordered_equal": left == right,
                     "set_equal": set(left) == set(right), "source_only_count": len(left_only),
                     "target_only_count": len(right_only), "source_only": [], "target_only": [],
                     "source_only_truncated": False, "target_only_truncated": False, "mismatches": [],
                     "mismatches_truncated": False}
        for name, rows in (("source_only", left_only), ("target_only", right_only)):
            if category in {"acl", "rls", "enums"}:
                component[name] = rows[:512]
            else:
                component[name] = [{"identity": list(row[:2]),
                    "tuple_sha256": sha256(json.dumps(row, separators=(",", ":")).encode()).hexdigest()}
                    for row in rows[:8]]
            component[name + "_truncated"] = len(rows) > len(component[name])
        if category not in {"acl", "rls", "enums"}:
            for index in range(max(len(left), len(right))):
                old = left[index] if index < len(left) else None
                new = right[index] if index < len(right) else None
                if old == new:
                    continue
                if len(component["mismatches"]) == 8:
                    component["mismatches_truncated"] = True
                    break
                old_text, new_text = json.dumps(old), json.dumps(new)
                offset = next((at for at, values in enumerate(zip(old_text, new_text)) if values[0] != values[1]),
                              min(len(old_text), len(new_text)))
                start = max(0, offset - 80)
                component["mismatches"].append({"row_index": index, "first_mismatch_character": offset,
                    "source_context": old_text[start:start + 256], "target_context": new_text[start:start + 256],
                    "source_length": len(old_text), "target_length": len(new_text)})
        report["components"][category] = component
    # Keep the complete safe tuples whenever they fit; report every truncation.
    while len(json.dumps(report, sort_keys=True).encode()) > 120000:
        for component in report["components"].values():
            for name in ("source_only", "target_only", "mismatches"):
                rows = component[name]
                if rows:
                    component[name] = rows[:len(rows) // 2]
                    component[name + "_truncated"] = True
    print("NETWORK_ARCHIVE_CATALOG_DIAGNOSTIC " + json.dumps(report, sort_keys=True,
          separators=(",", ":")), flush=True)


def restore():
    nonce = identity()
    source = f"ildr_f01_upgrade_{nonce}"
    target = f"ildr_f01_restore_{nonce}"
    for database, purpose in ((source, "upgrade"), (target, "restore")):
        require(query(database, """SELECT current_database(),current_setting('server_version_num')::int,
            shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()""") == [
                (database, 170006, f"ildr-live-communications-ci:{nonce}:{purpose}")], "owned_archive_database_required")
        require(table_names(database) == EXPECTED_TABLES
                and query(database, "SELECT version_num FROM alembic_version") == [(HEAD,)], "archive_full_head_required")
    source_catalog, target_catalog = catalog(source, EXPECTED_TABLES), catalog(target, EXPECTED_TABLES)
    if source_catalog != target_catalog:
        archive_catalog_diagnostic(source, target, source_catalog, target_catalog)
    require(source_catalog == target_catalog, "restored_full_catalog_required")
    require(snapshot(source, EXPECTED_TABLES) == snapshot(target, EXPECTED_TABLES), "restored_all_rows_sequences_required")
    retained_fixture(source)
    retained_fixture(target)
    require(query(source, "SELECT count(*),sum(octet_length(payload)) FROM listing_floor_plans")[0][0] == 2,
            "nonempty_binary_archive_fixture_required")
    return {"status": "PASS", "alembic_head": HEAD, "tables": 92, "all_rows_sequences_equal": True,
            "all_catalogs_equal": True, "floor_plan_binary_rows": 2, "retained_accepted_jobs": 9,
            "retained_private_profiles": 46, "retained_owner_trade_contacts": 82}


if __name__ == "__main__":
    try:
        result = restore() if sys.argv[1:] == ["restore"] else run()
    except Exception as error:
        result = {"status": "FAIL", "stage": stage, "error_type": type(error).__name__}
        if isinstance(error, RehearsalFailure):
            result["reason"] = str(error)
        print("LIVE_DELIVERY_SCHEMA_RESULT " + json.dumps(result, sort_keys=True), flush=True)
        raise SystemExit(1) from None
    print("LIVE_DELIVERY_SCHEMA_RESULT " + json.dumps(result, sort_keys=True), flush=True)

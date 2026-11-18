"""Accepted catalog/snapshot/deparser helpers; synthetic local databases only."""
from contextlib import closing
import re

OLD_TABLES = ('activity_log', 'alembic_version', 'anonymous_leads', 'bookings', 'bulk_import_jobs', 'client_shortlist_grants', 'client_shortlist_items', 'client_shortlists', 'deal_requests', 'development_inventories', 'development_inventory_events', 'development_inventory_revisions', 'editorial_events', 'editorial_pages', 'editorial_revisions', 'inquiries', 'lead_delivery_jobs', 'lead_delivery_worker_state', 'leads', 'listing_arrival_guides', 'listing_daily_views', 'listing_edits', 'listing_events', 'listing_floor_plans', 'listing_location_review_revisions', 'listing_location_reviews', 'listing_performance_entries', 'listing_view_coverage', 'listings', 'network_audit', 'network_blocks', 'network_due_work_actions', 'network_due_work_dispositions', 'network_due_work_preferences', 'network_history_share_actions', 'network_history_shares', 'network_maintenance_actions', 'network_maintenance_occurrences', 'network_maintenance_preferences', 'network_maintenance_schedules', 'network_media', 'network_milestones', 'network_post_comments', 'network_posts', 'network_project_comments', 'network_project_members', 'network_project_updates', 'network_projects', 'network_properties', 'network_property_custody_actions', 'network_property_custody_transfers', 'network_service_actions', 'network_service_conversation_messages', 'network_service_favorites', 'network_service_negotiation_messages', 'network_service_preferences', 'network_service_proposals', 'network_service_recipients', 'network_service_referral_events', 'network_service_referrals', 'network_service_requests', 'network_service_reviews', 'network_service_work', 'network_service_work_actions', 'pending_users', 'professional_profile_revisions', 'professional_profiles', 'property_management_records', 'property_management_revisions', 'property_viewings', 'research_workspaces', 'saved_homes', 'seller_valuation_comparables', 'seller_valuation_follow_ups', 'seller_valuation_intakes', 'seller_valuation_workspaces', 'settings', 'social_network_profiles', 'trade_contact_sources', 'trade_contacts', 'transaction_document_versions', 'transaction_documents', 'transaction_room_events', 'transaction_room_grants', 'transaction_room_milestones', 'transaction_room_tasks', 'transaction_rooms', 'upgrade_requests', 'users', 'viewing_itineraries', 'website_settings', 'website_settings_revisions')

class RehearsalFailure(RuntimeError):
    pass


def require(condition, reason):
    if not condition:
        raise RehearsalFailure(reason)


def connection(database):
    import psycopg2

    return psycopg2.connect(host="127.0.0.1", port=55449, dbname=database, user="postgres", password="",
                            connect_timeout=4, options="-cstatement_timeout=10000")


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


def verify_check_render_controls(database):
    """Exercise PostgreSQL's own Boolean deparser in an owned temporary table."""
    with closing(connection(database)) as db, db.cursor() as cursor:
        cursor.execute("""CREATE TEMP TABLE ildr_check_render_control (
            n INTEGER, a BOOLEAN, b BOOLEAN, c BOOLEAN,
            CONSTRAINT equivalent_between CHECK (n BETWEEN 1 AND 4 AND a),
            CONSTRAINT equivalent_expanded CHECK (n >= 1 AND n <= 4 AND a),
            CONSTRAINT different_bound CHECK (n >= 1 AND n <= 5 AND a),
            CONSTRAINT different_precedence_left CHECK ((a AND b) OR c),
            CONSTRAINT different_precedence_right CHECK (a AND (b OR c))
        ) ON COMMIT DROP""")
        cursor.execute("""SELECT conname,pg_get_constraintdef(oid),pg_get_constraintdef(oid,true)
            FROM pg_constraint WHERE conrelid='pg_temp.ildr_check_render_control'::regclass""")
        rows = {name: (raw, rendered) for name, raw, rendered in cursor.fetchall()}
        require(len(rows) == 5 and rows["equivalent_between"][0] != rows["equivalent_expanded"][0]
                and rows["equivalent_between"][1] == rows["equivalent_expanded"][1],
                "postgres_positive_check_render_control_required")
        require(rows["equivalent_expanded"][1] != rows["different_bound"][1]
                and rows["different_precedence_left"][1] != rows["different_precedence_right"][1],
                "postgres_negative_check_render_controls_required")


LEGACY_NEW_TABLES = ('client_shortlist_grants', 'client_shortlist_items', 'client_shortlists', 'development_inventories', 'development_inventory_events', 'development_inventory_revisions', 'editorial_events', 'editorial_pages', 'editorial_revisions', 'listing_arrival_guides', 'listing_daily_views', 'listing_floor_plans', 'listing_location_review_revisions', 'listing_location_reviews', 'listing_performance_entries', 'listing_view_coverage', 'network_audit', 'network_blocks', 'network_due_work_actions', 'network_due_work_dispositions', 'network_due_work_preferences', 'network_history_share_actions', 'network_history_shares', 'network_maintenance_actions', 'network_maintenance_occurrences', 'network_maintenance_preferences', 'network_maintenance_schedules', 'network_media', 'network_milestones', 'network_post_comments', 'network_posts', 'network_project_comments', 'network_project_members', 'network_project_updates', 'network_projects', 'network_properties', 'network_property_custody_actions', 'network_property_custody_transfers', 'network_service_actions', 'network_service_conversation_messages', 'network_service_favorites', 'network_service_negotiation_messages', 'network_service_preferences', 'network_service_proposals', 'network_service_recipients', 'network_service_referral_events', 'network_service_referrals', 'network_service_requests', 'network_service_reviews', 'network_service_work', 'network_service_work_actions', 'property_management_records', 'property_management_revisions', 'property_viewings', 'seller_valuation_comparables', 'seller_valuation_follow_ups', 'seller_valuation_intakes', 'seller_valuation_workspaces', 'social_network_profiles', 'transaction_document_versions', 'transaction_documents', 'transaction_room_events', 'transaction_room_grants', 'transaction_room_milestones', 'transaction_room_tasks', 'transaction_rooms', 'viewing_itineraries')

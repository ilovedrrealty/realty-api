"""Native private inbox only; legacy content, grants and provider settings unchanged."""
from alembic import op

revision = "d8f4c2a91e67"
down_revision = "c4e2a7319b60"
branch_labels = None
depends_on = None

TABLES = ("inbox_preferences", "inbox_blocks", "inbox_conversations", "inbox_commands",
          "inbox_entries", "inbox_read_states", "inbox_audit")
ACTIONS = "'conversation_opened','entry_created','read_marked','visibility_saved','block_saved','review_opened'"


def upgrade():
    statements = (
        """CREATE TABLE IF NOT EXISTS inbox_preferences (
            user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,
            visibility TEXT NOT NULL, version INTEGER NOT NULL,
            CONSTRAINT ck_inbox_preference CHECK(visibility IN ('private','members') AND version>=1)
        )""",
        """CREATE TABLE IF NOT EXISTS inbox_blocks (
            blocker_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            blocked_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(blocker_id,blocked_id),
            CONSTRAINT ck_inbox_no_self_block CHECK(blocker_id<>blocked_id)
        )""",
        """CREATE TABLE IF NOT EXISTS inbox_conversations (
            id UUID PRIMARY KEY, user_low_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            user_high_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            created_by_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            brokerage_subject_id UUID REFERENCES users(id) ON DELETE RESTRICT, policy_version TEXT NOT NULL,
            last_sequence BIGINT NOT NULL DEFAULT 0, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), last_entry_at TIMESTAMPTZ,
            CONSTRAINT uq_inbox_direct_pair UNIQUE(user_low_id,user_high_id),
            CONSTRAINT ck_inbox_conversation CHECK(user_low_id<user_high_id AND created_by_id IN (user_low_id,user_high_id)
                AND (brokerage_subject_id IS NULL OR brokerage_subject_id IN (user_low_id,user_high_id))
                AND policy_version='native-inbox-v1' AND last_sequence BETWEEN 0 AND 9007199254740991 AND ((last_sequence=0)=(last_entry_at IS NULL)))
        )""",
        f"""CREATE TABLE IF NOT EXISTS inbox_commands (
            actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT, submission_id UUID NOT NULL,
            action TEXT NOT NULL, payload_sha256 TEXT NOT NULL, target_id UUID NOT NULL,
            conversation_id UUID REFERENCES inbox_conversations(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(actor_id,submission_id),
            CONSTRAINT ck_inbox_command CHECK(action IN ({ACTIONS}) AND payload_sha256 ~ '^[0-9a-f]{{64}}$')
        )""",
        """CREATE TABLE IF NOT EXISTS inbox_entries (
            id UUID PRIMARY KEY, conversation_id UUID NOT NULL REFERENCES inbox_conversations(id) ON DELETE RESTRICT,
            sequence BIGINT NOT NULL, author_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            submission_id UUID NOT NULL, author_role TEXT NOT NULL, kind TEXT NOT NULL, body TEXT NOT NULL,
            occurred_at TIMESTAMPTZ, reported_direction TEXT, call_outcome TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_inbox_entry_command FOREIGN KEY(author_id,submission_id) REFERENCES inbox_commands(actor_id,submission_id) ON DELETE RESTRICT,
            CONSTRAINT uq_inbox_entry_sequence UNIQUE(conversation_id,sequence),
            CONSTRAINT uq_inbox_entry_submission UNIQUE(author_id,submission_id),
            CONSTRAINT ck_inbox_entry_actor CHECK(sequence BETWEEN 1 AND 9007199254740991 AND author_role IN ('buyer','owner','realtor','admin')),
            CONSTRAINT ck_inbox_entry_body CHECK(char_length(body) BETWEEN 1 AND 2000 AND body=btrim(body,' '||chr(9)||chr(10)||chr(13))
                AND translate(body,chr(9)||chr(10)||chr(13),'') COLLATE "C" !~ '[[:cntrl:]]'
                AND body !~ ('['||chr(128)||'-'||chr(159)||']')),
            CONSTRAINT ck_inbox_entry_kind CHECK((kind='message' AND occurred_at IS NULL AND reported_direction IS NULL AND call_outcome IS NULL)
                OR (kind='manual_whatsapp' AND occurred_at IS NOT NULL AND reported_direction IS NOT NULL AND reported_direction IN ('incoming','outgoing') AND call_outcome IS NULL)
                OR (kind='manual_call' AND occurred_at IS NOT NULL AND reported_direction IS NOT NULL AND reported_direction IN ('incoming','outgoing')
                    AND call_outcome IS NOT NULL AND call_outcome IN ('connected','voicemail','missed','no_answer','declined','unspecified'))),
            CONSTRAINT ck_inbox_entry_time CHECK(occurred_at IS NULL OR
                (occurred_at>='1970-01-01T00:00:00Z'::timestamptz AND occurred_at<=created_at+interval '5 minutes'))
        )""",
        """CREATE TABLE IF NOT EXISTS inbox_read_states (
            conversation_id UUID NOT NULL REFERENCES inbox_conversations(id) ON DELETE RESTRICT,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT, last_read_sequence BIGINT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(conversation_id,user_id),
            CONSTRAINT ck_inbox_read_sequence CHECK(last_read_sequence BETWEEN 0 AND 9007199254740991)
        )""",
        f"""CREATE TABLE IF NOT EXISTS inbox_audit (
            id UUID PRIMARY KEY, actor_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            submission_id UUID NOT NULL, actor_role TEXT NOT NULL, policy_version TEXT NOT NULL,
            action TEXT NOT NULL, target_id UUID NOT NULL,
            conversation_id UUID REFERENCES inbox_conversations(id) ON DELETE RESTRICT,
            source_kind TEXT, source_id UUID, recipient_id UUID, reason TEXT, expires_at TIMESTAMPTZ,
            result_flag BOOLEAN, result_visibility TEXT, result_sequence BIGINT,
            review_id UUID REFERENCES inbox_audit(id) ON DELETE RESTRICT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT fk_inbox_audit_command FOREIGN KEY(actor_id,submission_id) REFERENCES inbox_commands(actor_id,submission_id) ON DELETE RESTRICT,
            CONSTRAINT uq_inbox_audit_submission UNIQUE(actor_id,submission_id),
            CONSTRAINT ck_inbox_audit_actor CHECK(action IN ({ACTIONS}) AND actor_role IN ('buyer','owner','realtor','admin') AND policy_version='native-inbox-v1'),
            CONSTRAINT ck_inbox_audit_review CHECK((action='review_opened' AND actor_role='admin' AND source_kind IS NOT NULL
                AND source_kind IN ('native','service_award','service_negotiation') AND source_id IS NOT NULL AND reason IS NOT NULL
                AND reason IN ('oversight','support') AND expires_at IS NOT NULL AND expires_at>created_at
                AND ((source_kind='service_negotiation')=(recipient_id IS NOT NULL)))
                OR (action<>'review_opened' AND source_kind IS NULL AND source_id IS NULL AND recipient_id IS NULL AND reason IS NULL AND expires_at IS NULL)),
            CONSTRAINT ck_inbox_audit_result CHECK(((action='block_saved')=(result_flag IS NOT NULL))
                AND ((action='visibility_saved')=(result_visibility IS NOT NULL))
                AND (result_visibility IS NULL OR result_visibility IN ('private','members'))
                AND ((action IN ('entry_created','read_marked'))=(result_sequence IS NOT NULL))
                AND (result_sequence IS NULL OR result_sequence BETWEEN 0 AND 9007199254740991)),
            CONSTRAINT ck_inbox_audit_parent_review CHECK(review_id IS NULL OR (action='entry_created' AND actor_role='admin'))
        )""",
    )
    for statement in statements:
        op.execute(statement)
    op.execute("""DO $$ BEGIN IF NOT EXISTS(SELECT FROM pg_constraint WHERE conrelid='inbox_commands'::regclass AND conname='fk_inbox_command_audit') THEN
        ALTER TABLE inbox_commands ADD CONSTRAINT fk_inbox_command_audit FOREIGN KEY(actor_id,submission_id)
            REFERENCES inbox_audit(actor_id,submission_id) DEFERRABLE INITIALLY DEFERRED;
    END IF; END $$""")
    for name, table, columns in (
        ("ix_inbox_preference_discovery", "inbox_preferences", "visibility,user_id"),
        ("ix_inbox_block_reverse", "inbox_blocks", "blocked_id,blocker_id"),
        ("ix_inbox_conversation_low", "inbox_conversations", "user_low_id,last_entry_at,id"),
        ("ix_inbox_conversation_high", "inbox_conversations", "user_high_id,last_entry_at,id"),
        ("ix_inbox_conversation_creator", "inbox_conversations", "created_by_id"),
        ("ix_inbox_conversation_subject", "inbox_conversations", "brokerage_subject_id"),
        ("ix_inbox_command_conversation", "inbox_commands", "conversation_id,created_at"),
        ("ix_inbox_entry_author", "inbox_entries", "author_id,created_at,id"),
        ("ix_inbox_read_user", "inbox_read_states", "user_id,conversation_id"),
        ("ix_inbox_audit_actor", "inbox_audit", "actor_id,created_at,id"),
        ("ix_inbox_audit_conversation", "inbox_audit", "conversation_id,created_at,id"),
        ("ix_inbox_audit_review", "inbox_audit", "review_id"),
        ("ix_inbox_audit_source", "inbox_audit", "source_kind,source_id,recipient_id"),
    ):
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({columns})")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
        for role in ("anon", "authenticated", "service_role"):
            op.execute(f"""DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='{role}') THEN
                REVOKE ALL ON {table} FROM {role}; END IF; END $$""")
    op.execute("""CREATE OR REPLACE FUNCTION native_inbox_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE conversation inbox_conversations; command inbox_commands; entry inbox_entries; review inbox_audit;
    BEGIN
        IF TG_TABLE_NAME IN ('inbox_entries','inbox_commands','inbox_audit','inbox_conversations','inbox_preferences','inbox_read_states') AND TG_OP='DELETE' THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Inbox history and explicit privacy choices are retained';
        END IF;
        IF TG_TABLE_NAME IN ('inbox_entries','inbox_commands','inbox_audit','inbox_blocks') AND TG_OP='UPDATE' THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Inbox history and commands are immutable';
        ELSIF TG_TABLE_NAME='inbox_preferences' AND TG_OP='UPDATE' THEN
            IF NEW.user_id<>OLD.user_id OR NEW.version<>OLD.version+1 THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Inbox preference versions must advance';
            END IF;
        ELSIF TG_TABLE_NAME='inbox_conversations' AND TG_OP='UPDATE' THEN
            IF (to_jsonb(NEW)-'last_sequence'-'last_entry_at') IS DISTINCT FROM (to_jsonb(OLD)-'last_sequence'-'last_entry_at')
                OR NEW.last_sequence<>OLD.last_sequence+1 OR NEW.last_entry_at IS NULL THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Inbox participant identity is immutable';
            END IF;
        ELSIF TG_TABLE_NAME='inbox_entries' AND TG_OP='INSERT' THEN
            SELECT * INTO conversation FROM inbox_conversations WHERE id=NEW.conversation_id;
            SELECT * INTO command FROM inbox_commands WHERE actor_id=NEW.author_id AND submission_id=NEW.submission_id;
            IF conversation.id IS NULL OR NEW.sequence<>conversation.last_sequence OR command.actor_id IS NULL OR command.action<>'entry_created'
                OR command.target_id<>NEW.id OR command.conversation_id IS DISTINCT FROM NEW.conversation_id
                OR NOT EXISTS(SELECT FROM users u WHERE u.id=NEW.author_id AND u.role::text=NEW.author_role AND u.status='active' AND u.email_verified IS TRUE)
                OR (NEW.author_id NOT IN (conversation.user_low_id,conversation.user_high_id) AND NEW.author_role<>'admin') THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Inbox entry must match its actor command and conversation';
            END IF;
        ELSIF TG_TABLE_NAME='inbox_audit' AND TG_OP='INSERT' THEN
            SELECT * INTO command FROM inbox_commands WHERE actor_id=NEW.actor_id AND submission_id=NEW.submission_id;
            IF command.actor_id IS NULL OR command.action<>NEW.action OR command.target_id<>NEW.target_id
                OR command.conversation_id IS DISTINCT FROM NEW.conversation_id
                OR NOT EXISTS(SELECT FROM users u WHERE u.id=NEW.actor_id AND u.role::text=NEW.actor_role AND u.status='active' AND u.email_verified IS TRUE) THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Inbox audit must match its verified actor command';
            END IF;
            IF NEW.action='entry_created' THEN
                SELECT * INTO conversation FROM inbox_conversations WHERE id=NEW.conversation_id;
                SELECT * INTO entry FROM inbox_entries WHERE id=NEW.target_id;
                IF entry.id IS NULL OR entry.author_id<>NEW.actor_id OR entry.submission_id<>NEW.submission_id
                    OR entry.sequence<>NEW.result_sequence OR entry.conversation_id<>NEW.conversation_id
                    OR (NEW.actor_id NOT IN (conversation.user_low_id,conversation.user_high_id) AND NEW.review_id IS NULL) THEN
                    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Inbox entry audit must identify the recorded entry and review';
                END IF;
            END IF;
            IF NEW.review_id IS NOT NULL THEN
                SELECT * INTO review FROM inbox_audit WHERE id=NEW.review_id;
                IF review.id IS NULL OR review.action<>'review_opened' OR review.actor_id<>NEW.actor_id
                    OR review.source_kind<>'native' OR review.source_id<>NEW.conversation_id OR review.expires_at<=NEW.created_at THEN
                    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Admin intervention requires its current audited native review';
                END IF;
            END IF;
        ELSIF TG_TABLE_NAME='inbox_read_states' THEN
            SELECT * INTO conversation FROM inbox_conversations WHERE id=NEW.conversation_id;
            IF conversation.id IS NULL OR NEW.user_id NOT IN (conversation.user_low_id,conversation.user_high_id)
                OR NEW.last_read_sequence>conversation.last_sequence OR (TG_OP='UPDATE' AND
                (NEW.conversation_id<>OLD.conversation_id OR NEW.user_id<>OLD.user_id OR NEW.last_read_sequence<OLD.last_read_sequence)) THEN
                RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Inbox read cursor must be a monotonic participant cursor';
            END IF;
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END $$""")
    op.execute("REVOKE ALL ON FUNCTION native_inbox_guard() FROM PUBLIC")
    for role in ("anon", "authenticated", "service_role"):
        op.execute(f"""DO $$ BEGIN IF EXISTS(SELECT FROM pg_roles WHERE rolname='{role}') THEN
            REVOKE ALL ON FUNCTION native_inbox_guard() FROM {role}; END IF; END $$""")
    for table in TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS native_inbox_retention ON {table}")
        op.execute(f"CREATE TRIGGER native_inbox_retention BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION native_inbox_guard()")


def downgrade():
    op.execute("LOCK TABLE " + ",".join(TABLES) + " IN ACCESS EXCLUSIVE MODE")
    for table in TABLES:
        op.execute(f"""DO $$ BEGIN IF EXISTS(SELECT FROM {table}) THEN
            RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='Populated inbox records must be retained'; END IF; END $$""")
    op.execute("ALTER TABLE inbox_commands DROP CONSTRAINT fk_inbox_command_audit")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION native_inbox_guard()")

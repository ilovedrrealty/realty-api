from sqlalchemy import CheckConstraint, DateTime, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import mapped_column

from database import Base


class NewsletterSubscription(Base):
    __tablename__ = "newsletter_subscriptions"
    __table_args__ = (
        CheckConstraint("language IN ('en','es')", name="ck_newsletter_locale"),
        CheckConstraint("status IN ('pending','subscribed','unsubscribed')", name="ck_newsletter_status"),
    )

    id = mapped_column(UUID(as_uuid=True), primary_key=True)
    email = mapped_column(Text, nullable=False, unique=True)
    language = mapped_column(Text, nullable=False)
    status = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    consent_at = mapped_column(DateTime(timezone=True), nullable=False)
    confirmation_nonce = mapped_column(UUID(as_uuid=True))
    confirmation_payload = mapped_column(JSONB)
    confirmation_expires_at = mapped_column(DateTime(timezone=True))
    last_attempt_at = mapped_column(DateTime(timezone=True))
    provider_accepted_at = mapped_column(DateTime(timezone=True))
    subscribed_at = mapped_column(DateTime(timezone=True))
    unsubscribed_at = mapped_column(DateTime(timezone=True))

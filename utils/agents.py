"""Agent eligibility is independent of the user's administration privileges."""

from uuid import UUID
from fastapi import HTTPException

from models.user import User


def eligible_agent_query(db):
    return db.query(User).filter(
        User.role.in_(("admin", "realtor")),
        User.status == "active",
        User.email_verified.is_(True),
    )


def find_eligible_agent(db, agent_id):
    try:
        identifier = UUID(str(agent_id))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=422, detail="Invalid agent ID")
    return eligible_agent_query(db).filter(User.id == identifier).first()

"""Private saved providers. Current service opt-in controls what can be shown."""
from uuid import uuid4

from models.service_favorites import NetworkServiceFavorite as Favorite
from models.service_workflow import NetworkServicePreference as Preference
from models.user import User
from services import property_network as network, service_workflow as workflow


def _member(user):
    workflow.checked_user(user)
    if user.role == "admin":
        workflow.unavailable()
    return user


def _locked_member(db, user, ids=()):
    return _member(workflow.lock_users(db, _member(user), ids))


def _eligible_providers(db):
    return db.query(User.id.label("provider_id"), User.user_code, Preference.member_alias).select_from(User).join(
        Preference, Preference.user_id == User.id).filter(
        User.status == "active", User.email_verified.is_(True), User.role != "admin", User.user_code > 0,
        Preference.service_opt_in.is_(True),
    )


def _view(favorite_id, saved_at, code, alias):
    provider = None if code is None else {"user_code": code, "member_alias": alias}
    return {"id": str(favorite_id), "provider": provider, "saved_at": saved_at}


def list_favorites(db, user, page):
    actor = _locked_member(db, user)
    live = _eligible_providers(db).subquery()
    rows = db.query(Favorite.id, Favorite.saved_at, live.c.user_code, live.c.member_alias).select_from(Favorite).outerjoin(
        live, live.c.provider_id == Favorite.provider_id).filter(Favorite.actor_id == actor.id).order_by(
        Favorite.saved_at.desc(), Favorite.id.desc())
    return network.page(rows, page, lambda row: _view(*row))


def save(db, user, provider_user_code):
    user = _member(user)
    provider_id = db.query(User.id).filter(User.user_code == provider_user_code).scalar()
    if provider_id is None or provider_id == user.id:
        workflow.unavailable()

    actor = _locked_member(db, user, {provider_id})
    provider = _eligible_providers(db).filter(User.id == provider_id, User.user_code == provider_user_code).with_for_update(
        of=Preference).first()
    if provider is None:
        workflow.unavailable()

    row = db.query(Favorite).filter_by(actor_id=actor.id, provider_id=provider_id).first()
    if row is not None:
        return _view(row.id, row.saved_at, provider.user_code, provider.member_alias)

    row = Favorite(id=uuid4(), actor_id=actor.id, provider_id=provider_id)
    db.add(row)
    db.flush()
    result = _view(row.id, row.saved_at, provider.user_code, provider.member_alias)
    db.commit()
    return result


def remove(db, user, favorite_id):
    actor = _locked_member(db, user)
    row = db.query(Favorite).filter_by(id=favorite_id, actor_id=actor.id).first()
    if row is None:
        workflow.unavailable()
    db.delete(row)
    db.commit()

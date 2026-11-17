"""Read current details for a separate, explicitly submitted service request."""
from models.service_workflow import NetworkServicePreference
from models.user import User
from services import service_review, service_workflow as workflow
from utils.professional_options import CATEGORY_IDS, PROPERTY_CLASS_IDS


def context(db, user, request_id):
    _, _, request, source, winner = service_review.completed_award(db, user, request_id, owner_only=True)
    private = request.property_id is not None
    property_class = source.property_class if private else request.property_class
    if (request.category_id not in CATEGORY_IDS or property_class not in PROPERTY_CLASS_IDS
            or not workflow.eligible(db, winner, request)):
        workflow.unavailable()

    provider = db.get(User, winner.provider_id)
    preference = db.get(NetworkServicePreference, winner.provider_id)
    return {
        "request_id": str(request.id),
        "source": {"kind": "private" if private else "listing", "id": str(source.id),
                   "title": source.title, "property_class": property_class},
        "category_id": request.category_id,
        "provider": {"user_code": provider.user_code, "member_alias": preference.member_alias},
    }

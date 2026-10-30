"""Request constraints for authenticated JSON mutation endpoints."""
from fastapi import HTTPException, Request

from config import settings


def require_json_mutation(request: Request) -> None:
    """Block simple cross-site POSTs, including JSON without a media type.

    CORS alone does not stop a browser from sending a credentialed simple
    request. SameSite=None cookies require an explicit origin check as well.
    Bearer clients do not rely on ambient browser cookies.
    """
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(415, "Send this request as application/json")

    origin = request.headers.get("origin")
    authorization = request.headers.get("authorization", "")
    scheme, _, credential = authorization.partition(" ")
    uses_bearer = scheme.lower() == "bearer" and bool(credential.strip())
    uses_cookie = bool(request.cookies.get("ildr_token")) and not uses_bearer
    # Compare complete origins, never suffixes or substring matches. A normal
    # same-origin browser request is also allowed when served via one host.
    allowed = set(settings.origins_list)
    allowed.add(f"{request.url.scheme}://{request.url.netloc}")
    if (origin is not None and origin not in allowed) or (uses_cookie and origin is None):
        raise HTTPException(403, "Request origin is not allowed")

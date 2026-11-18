"""Owner-supplied Google destinations; no lookup, ratings or verification."""
import re
from urllib.parse import parse_qsl, unquote, urlsplit

PROFILE_URL_LIMIT = 2048
EMBED_URL_LIMIT = 4096
_CREDENTIAL_PARAMETERS = {"key", "api_key", "apikey", "client", "signature", "token", "access_token",
                          "callback", "url", "redirect", "redirect_uri", "continue", "return", "returnurl"}


def _parts(value, limit):
    if not isinstance(value, str):
        raise ValueError("Enter a Google Maps URL")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Control characters are not allowed in Google Maps URLs")
    value = value.strip()
    if not value:
        return value, None, []
    if (len(value) > limit or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
            or any(char in value for char in '<>"\\') or re.search(r"%(?![0-9a-fA-F]{2})", value)
            or re.search(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", value, re.I)):
        raise ValueError("Enter a complete Google Maps URL without HTML or credentials")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port is not None
                or parsed.fragment or not parsed.hostname
                or any(segment in {".", ".."} for segment in unquote(parsed.path, errors="strict").split("/"))):
            raise ValueError("Invalid Google Maps URL")
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        raise ValueError("Enter a valid HTTPS Google Maps URL") from None
    if (len(pairs) > 32 or len({key.lower() for key, _ in pairs}) != len(pairs)
            or any("%" in key or key.lower() in _CREDENTIAL_PARAMETERS for key, _ in pairs)):
        raise ValueError("Google Maps links must not include API keys or redirect parameters")
    return value, parsed, pairs


def google_profile_url(value):
    value, parsed, pairs = _parts(value, PROFILE_URL_LIMIT)
    if parsed is None:
        return value
    host, path = parsed.hostname, parsed.path
    query = dict(pairs)
    if host == "maps.app.goo.gl" and re.fullmatch(r"/[A-Za-z0-9_-]{4,180}/?", path):
        return value
    if host == "g.page" and re.fullmatch(r"/(?:r/)?[A-Za-z0-9_-]{2,180}/?", path):
        return value
    if host in {"www.google.com", "maps.google.com"}:
        if path.startswith("/maps/place/") and path[len("/maps/place/"):].strip("/"):
            return value
        if path.startswith("/maps/search/") and path[len("/maps/search/"):].strip("/"):
            return value
        if path in {"/maps/search", "/maps/search/"} and query.get("api") == "1" and query.get("query", "").strip():
            return value
        if path in {"", "/", "/maps", "/maps/"} and re.fullmatch(r"[0-9]{1,30}", query.get("cid", "")):
            return value
    raise ValueError("Use this business's Google Maps place, search or official share link")


def google_maps_embed_url(value):
    value, parsed, pairs = _parts(value, EMBED_URL_LIMIT)
    if parsed is None:
        return value
    if parsed.hostname != "www.google.com" or parsed.path != "/maps/embed":
        raise ValueError("Use the map URL from Google Maps Share, Embed a map")
    names = [key for key, _ in pairs]
    values = dict(pairs)
    if (set(names) - {"pb", "hl"} or names.count("pb") != 1 or names.count("hl") > 1
            or not values.get("pb", "").startswith("!")
            or "hl" in values and not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", values["hl"])):
        raise ValueError("Paste only the Google-generated maps/embed?pb= URL")
    return value

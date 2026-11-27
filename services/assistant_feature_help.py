"""Source-bound help only. A future server adapter must authenticate each caller.

This selector neither authenticates its ``server_actor`` nor authorizes a target
record. Never build that argument from model/user JSON or cache an earlier role.
There is intentionally no route, model call, record lookup or action here.
"""
import json
import re
from collections.abc import Mapping
from pathlib import Path

_ROLES = frozenset({"buyer", "owner", "realtor", "admin"})
_LOCALES = frozenset({"en", "es"})
_PATH = re.compile(r"/[a-z-]+(?:\?(?:workspace=admin|tab=(?:my-profile|groups|projects)))?\Z")


class HelpUnavailable(ValueError):
    """Finite public error code; never contains request or actor values."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _nonfinite(_value):
    raise ValueError("nonfinite")


def _load_manifest():
    value = json.loads(Path(__file__).with_suffix(".json").read_text(encoding="utf-8"),
                       object_pairs_hook=_object, parse_constant=_nonfinite)
    assert value["schema"] == 1 and value["guide_state"] == "unpublished_source"
    entries = value["entries"]
    assert isinstance(entries, list) and 1 <= len(entries) <= 20
    seen = set()
    for entry in entries:
        assert set(entry) == {"id", "audiences", "links", "text", "source_refs"}
        key = entry["id"]
        assert re.fullmatch(r"[a-z_]{1,40}", key) and key not in seen
        seen.add(key)
        audiences = entry["audiences"]
        assert isinstance(audiences, list) and audiences and len(set(audiences)) == len(audiences)
        assert set(audiences) <= (_ROLES | {"public"})
        assert "public" not in audiences or audiences == ["public"]
        assert set(entry["text"]) == _LOCALES
        assert entry["source_refs"] and set(entry["source_refs"]) <= set(value["source_pins"])
        for text in entry["text"].values():
            assert set(text) == {"title", "steps"} and 1 <= len(text["steps"]) <= 5
            assert isinstance(text["title"], str) and 1 <= len(text["title"]) <= 120
            assert all(isinstance(s, str) and 1 <= len(s) <= 600 for s in text["steps"])
        links = entry["links"]
        assert set(links) <= (_ROLES | {"default"}) and links
        assert "default" in links or set(links) == set(audiences)
        for group in links.values():
            assert isinstance(group, list) and 1 <= len(group) <= 3
            for link in group:
                assert set(link) == {"surface", "path", "label"}
                assert link["surface"] in {"landing", "dashboard"} and _PATH.fullmatch(link["path"])
                assert set(link["label"]) == _LOCALES
                assert all(isinstance(s, str) and 1 <= len(s) <= 120 for s in link["label"].values())
                assert (link["surface"] == "landing") == (audiences == ["public"])
    return value


_MANIFEST = _load_manifest()


def select_help(raw_request, *, server_actor=None):
    """Accept only locale/topic JSON; actor must come from fresh server auth.

    ``None`` selects anonymous help. An active verified server User object selects
    its current role's navigation only; resource-level controls still decide all
    actual reads/writes. Returned links are surface-relative, never API URLs.
    """
    try:
        if type(raw_request) not in (str, bytes) or len(raw_request) > 4096:
            raise ValueError("shape")
        if isinstance(raw_request, str) and len(raw_request.encode("utf-8")) > 4096:
            raise ValueError("size")
        request = json.loads(raw_request, object_pairs_hook=_object, parse_constant=_nonfinite)
        if type(request) is not dict or set(request) - {"locale", "topic"} or "locale" not in request:
            raise ValueError("keys")
        locale, topic = request["locale"], request.get("topic", "overview")
        if type(locale) is not str or locale not in _LOCALES or type(topic) is not str:
            raise ValueError("selection")
        if not re.fullmatch(r"[a-z_]{1,40}", topic):
            raise ValueError("topic")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HelpUnavailable("invalid_help_request") from None
    role = None
    if server_actor is not None:
        try:
            if isinstance(server_actor, (Mapping, str, bytes)):
                raise ValueError("actor")
            role = server_actor.role
            if type(role) is not str or role not in _ROLES:
                raise ValueError("role")
            if server_actor.status != "active" or server_actor.email_verified is not True:
                raise ValueError("state")
        except (AttributeError, TypeError, ValueError):
            raise HelpUnavailable("help_actor_unavailable") from None
    allowed = [entry for entry in _MANIFEST["entries"]
               if "public" in entry["audiences"] or role in entry["audiences"]]

    def project(entry, *, steps=False):
        output = {"topic": entry["id"], "title": entry["text"][locale]["title"],
                  "links": [{"surface": link["surface"], "path": link["path"],
                             "label": link["label"][locale]}
                            for link in entry["links"].get(role, entry["links"].get("default", []))]}
        if steps:
            output["steps"] = list(entry["text"][locale]["steps"])
        return output

    output = {"status": "help", "locale": locale, "guide_state": "unpublished_source"}
    if topic == "overview":
        output["topics"] = [project(entry) for entry in allowed]
    else:
        entry = next((entry for entry in allowed if entry["id"] == topic), None)
        if entry is None:
            raise HelpUnavailable("help_unavailable")
        output["guide"] = project(entry, steps=True)
    # One fixed bounded response; there is no model-generated URL or record data.
    assert len(json.dumps(output, ensure_ascii=False).encode("utf-8")) <= 32768
    return output

"""Extend the accepted communications rehearsal with exact customer link checks."""

from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys


APPLICATION_COMMIT = "08c78f58cf4052a267480a8c27a406c7f8afaf2c"
APPLICATION_TREE = "ad70125fd25bb57e8a830ad3ae3dd25e3f259ed4"
MIGRATION = "f4b27d6e913c"
PRIOR_HARNESS = Path("/prior/test_cumulative_communications_acceptance.py")
PRIOR_SHA256 = "31d9792c4fc11f924af0b0ab2db3247f77eb083c1274fab94fb99c75197db60b"
FIXTURE = Path("/prior/serve_cumulative_communications_fixture.py")
FIXTURE_SHA256 = "fd7079170b9307ef4d523a3692b83bba2490bf16b7eb26036e4eb718a00edba7"
WHATSAPP_URL = "https://wa.me/18096108094"
LABELS = {"en": "Message our team on WhatsApp", "es": "Escríbenos por WhatsApp"}


def load_pinned(name, path, expected):
    if (not path.is_file() or path.is_symlink() or not os.statvfs(path).f_flag & os.ST_RDONLY
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
        raise RuntimeError("immutable_rehearsal_source_required")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def app_isolation(prior):
    prior.require(sys.platform == "linux" and sys.version_info[:2] == (3, 11) and
                  {interface.name for interface in Path("/sys/class/net").iterdir()} == {"lo"},
                  "hosted_loopback_namespace_required")
    fixture = load_pinned("accepted_customer_fixture", FIXTURE, FIXTURE_SHA256)
    prior.require((fixture.APP_COMMIT, fixture.APP_TREE, fixture.MIGRATION_HEAD) == (
        "b38d477c0e2e50f87f1f2e4cc1ceb3c707af92c5", "a53b3f829f70717069f5c637c826fb8f92c02f7c", MIGRATION),
        "accepted_fixture_identity_changed")
    fixture.APP_COMMIT, fixture.APP_TREE = APPLICATION_COMMIT, APPLICATION_TREE
    application = fixture.verified_checkout()
    prior.require(application == Path("/application") and os.statvfs(application).f_flag & os.ST_RDONLY,
                  "immutable_application_mount_required")
    prior.confirm_disposable_database()
    fixture.configure_application(application, prior.DATABASE_URL)
    os.environ.update(RESEND_API_KEY="synthetic-placeholder-only", EMAIL_FROM=prior.SENDER,
                      LEAD_DELIVERY_ENABLED="true", CUSTOMER_MESSAGES_ENABLED="true",
                      PROPERTY_INQUIRY_REQUIRE_CONSENT="true", GHL_WEBHOOK_SECRET="", LOGO_URL="")
    return fixture


def verify_links(prior):
    old_customer = prior.customer_payload_matches
    old_owner = prior.owner_payload_matches
    customer, owner = Counter(), Counter()

    def customer_match(body, event):
        if not old_customer(body, event):
            return False
        locale, _, _, path = event.customer_copy
        primary_url = f"{prior.CUSTOMER_ORIGIN}{path}"
        text, html = body["text"], body["html"]
        label = LABELS[locale]
        prior.require(re.findall(r"https://\S+", text) == [primary_url, WHATSAPP_URL]
                      and re.findall(r'href="([^"]+)"', html) == [primary_url, WHATSAPP_URL]
                      and f"\n\n{label}: {WHATSAPP_URL}\n\n" in text
                      and f'<a href="{WHATSAPP_URL}">{label}</a>' in html,
                      "customer_followup_url_or_locale_changed")
        customer[locale] += 1
        return True

    def owner_match(body, event, origin):
        if not old_owner(body, event, origin):
            return False
        prior.require(WHATSAPP_URL not in body.get("text", "") and WHATSAPP_URL not in body.get("html", ""),
                      "customer_followup_leaked_into_team_notice")
        owner[event.kind] += 1
        return True

    prior.verified_isolation = lambda: app_isolation(prior)
    prior.customer_payload_matches = customer_match
    prior.owner_payload_matches = owner_match
    result = prior.run()
    prior.require(dict(customer) == {"en": 2, "es": 3} and sum(owner.values()) == 32,
                  "customer_or_team_acceptance_count_changed")
    result.update(customer_link_acceptances=dict(customer), team_notices_checked=sum(owner.values()),
                  exact_public_whatsapp_url=True, frontend_built=False)
    return result


def main():
    prior = None
    try:
        prior = load_pinned("accepted_communications_test", PRIOR_HARNESS, PRIOR_SHA256)
        result = verify_links(prior)
    except Exception as error:
        result = {"status": "FAIL", "stage": prior.stage if prior is not None else "rehearsal_source",
                  "exception_type": type(error).__name__}
        if prior is not None and isinstance(error, prior.RehearsalFailure):
            result["reason"] = str(error)
        print("CUSTOMER_FOLLOWUP_RESULT " + json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
        return 1
    print("CUSTOMER_FOLLOWUP_RESULT " + json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

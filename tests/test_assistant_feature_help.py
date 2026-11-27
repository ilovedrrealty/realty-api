"""Pure stdlib help selection; no application/config/database imports."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

_SPEC = importlib.util.spec_from_file_location(
    "owned_feature_help", Path(__file__).resolve().parents[1] / "services/assistant_feature_help.py")
help_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(help_module)


class FeatureHelpTests(unittest.TestCase):
    def actor(self, role="admin", **changes):
        return SimpleNamespace(**({"role": role, "status": "active", "email_verified": True} | changes))

    def select(self, topic="overview", *, actor=None, locale="en"):
        return help_module.select_help(json.dumps({"locale": locale, "topic": topic}), server_actor=actor)

    def test_anonymous_help_never_exposes_authenticated_directory_or_staff_topics(self):
        result = self.select()
        self.assertEqual(len(result["topics"]), 5)
        self.assertTrue(all(link["surface"] == "landing" for topic in result["topics"] for link in topic["links"]))
        for topic in ("professionals_search", "lead_assignment", "contact_import", "unknown_topic"):
            with self.assertRaisesRegex(help_module.HelpUnavailable, "^help_unavailable$"):
                self.select(topic)

    def test_current_roles_select_navigation_without_granting_target_access(self):
        for role in ("buyer", "owner", "realtor", "admin"):
            result = self.select(actor=self.actor(role))
            ids = {item["topic"] for item in result["topics"]}
            self.assertIn("professionals_search", ids)
            self.assertIn("property_work", ids)
            self.assertEqual("manual_follow_up" in ids, role in {"admin", "realtor"})
            self.assertEqual("lead_assignment" in ids, role == "admin")
            self.assertEqual("contact_import" in ids, role == "admin")
        admin = self.select("manual_follow_up", actor=self.actor())["guide"]["links"][0]
        realtor = self.select("manual_follow_up", actor=self.actor("realtor"))["guide"]["links"][0]
        self.assertEqual(admin["path"], "/leads?workspace=admin")
        self.assertEqual(realtor["path"], "/leads")
        self.assertIn("current access permits", self.select("property_work", actor=self.actor("buyer"))["guide"]["steps"][-1])

    def test_model_json_cannot_supply_role_actor_workspace_or_arbitrary_navigation(self):
        bad = ["[]", "null", '{"locale":"en","locale":"es"}', '{"locale":"en","topic":NaN}',
               '{"locale":"fr"}', '{"locale":true}', '{"topic":"buying"}', '{"locale":"en","topic":"/admin"}',
               "{" * 4100, b"\xff"]
        bad += [json.dumps({"locale": "en", key: value}) for key, value in
                (("role", "admin"), ("actor", {"role": "admin"}), ("workspace", "admin"), ("url", "https://example.com"))]
        for value in bad:
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(help_module.HelpUnavailable, "^invalid_help_request$"):
                    help_module.select_help(value, server_actor=self.actor())

    def test_actor_is_server_object_and_selector_reads_no_identity_or_contact_fields(self):
        for actor in ({"role": "admin"}, "admin", self.actor("Admin"), self.actor(status="suspended"),
                      self.actor(email_verified=False), self.actor(email_verified=1), object()):
            with self.assertRaisesRegex(help_module.HelpUnavailable, "^help_actor_unavailable$"):
                self.select(actor=actor)
        class RestrictedActor:
            role, status, email_verified = "admin", "active", True
            def __getattr__(self, _key):
                raise AssertionError("unnecessary identity read")
        self.assertEqual(len(self.select(actor=RestrictedActor())["topics"]), 13)

    def test_role_and_verification_changes_take_effect_on_next_selection(self):
        actor = self.actor()
        self.select("contact_import", actor=actor)
        actor.role = "buyer"
        with self.assertRaisesRegex(help_module.HelpUnavailable, "^help_unavailable$"):
            self.select("contact_import", actor=actor)
        actor.email_verified = False
        with self.assertRaisesRegex(help_module.HelpUnavailable, "^help_actor_unavailable$"):
            self.select("buying", actor=actor)

    def test_response_changes_cannot_rewrite_later_help(self):
        result = self.select("buying")
        original = result["guide"]["steps"][0]
        result["guide"]["steps"][0] = "changed"
        result["guide"]["links"][0]["path"] = "https://external.invalid"
        later = self.select("buying")
        self.assertEqual(later["guide"]["steps"][0], original)
        self.assertEqual(later["guide"]["links"][0]["path"], "/buying")

    def test_both_languages_use_fixed_surface_links_and_unpublished_source_state(self):
        for locale in ("en", "es"):
            overview = self.select(actor=self.actor(), locale=locale)
            self.assertEqual(overview["guide_state"], "unpublished_source")
            for item in overview["topics"]:
                guide = self.select(item["topic"], actor=self.actor(), locale=locale)["guide"]
                self.assertTrue(guide["steps"])
                for link in guide["links"]:
                    self.assertNotIn("/admin", link["path"])
                    self.assertNotIn("://", link["path"])
                    self.assertIn(link["surface"], {"landing", "dashboard"})
            self.assertLess(len(json.dumps(overview).encode()), 32768)
        self.assertNotEqual(self.select("buying")["guide"]["title"], self.select("buying", locale="es")["guide"]["title"])


if __name__ == "__main__":
    unittest.main()

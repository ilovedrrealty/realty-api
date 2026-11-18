"""Pure URL/schema cases; no database, provider, app configuration or credentials."""
import unittest
from pydantic import ValidationError
from schemas.professional_google import ProfessionalGoogleFields
from utils.professional_google import google_profile_url, google_maps_embed_url

PROFILE = "https://www.google.com/maps/place/Synthetic+business/"
EMBED = "https://www.google.com/maps/embed?pb=!1m2!1sSynthetic!2sBusiness"


class ProfessionalGoogleLinksTest(unittest.TestCase):
    def test_old_blank_payload_is_optional_and_has_no_publication_or_review_claim(self):
        result = ProfessionalGoogleFields.model_validate({}).model_dump()
        self.assertEqual(result, {"google_profile_url": "", "google_maps_embed_url": ""})
        self.assertNotIn("rating", result)
        self.assertNotIn("verified", result)

    def test_common_place_search_cid_and_official_share_links_preserve_the_supplied_destination(self):
        for value in (PROFILE, "https://www.google.com/maps/search/?api=1&query=Synthetic+business&query_place_id=SyntheticID",
                      "https://www.google.com/maps/search/Synthetic+business", "https://maps.google.com/?cid=123456",
                      "https://maps.app.goo.gl/Abcde12345?g_st=ic", "https://g.page/synthetic-business", "https://g.page/r/Synthetic_123",
                      "https://www.google.com/maps/place/John's+Repairs"):
            with self.subTest(value=value):
                self.assertEqual(google_profile_url(" " + value + " "), value)

    def test_profile_urls_reject_foreign_hosts_credentials_fragments_api_keys_and_nonbusiness_routes(self):
        for value in ("http://www.google.com/maps/place/A", "https://www.google.com.evil.example/maps/place/A",
                      "https://google.com@evil.example/maps/place/A", "https://user:secret@www.google.com/maps/place/A",
                      "https://www.google.com:443/maps/place/A", "https://www.google.com/maps/place/A#token=private",
                      "https://www.google.com/maps/place/A?%6bey=secret", "https://www.google.com/url?url=https://evil.example",
                      "https://www.google.com/maps/embed?pb=!1m2", "https://g.page/synthetic/review",
                      "https://www.google.com/maps/place/A%0A", "https://www.google.com/maps/place/../url",
                      "https://www.google.com/maps/place/%2e%2e/url", "https://www.google.com\\@evil.example/maps/place/A",
                      "https://www.google.com/maps/search/?api=1&api=0&query=Business"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                google_profile_url(value)

    def test_manual_embed_is_opaque_bounded_and_preserves_valid_google_output(self):
        self.assertEqual(google_maps_embed_url(EMBED), EMBED)
        self.assertEqual(google_maps_embed_url(EMBED + "&hl=es-419"), EMBED + "&hl=es-419")
        self.assertEqual(google_maps_embed_url(""), "")
        self.assertEqual(ProfessionalGoogleFields(google_profile_url=PROFILE, google_maps_embed_url=EMBED).google_maps_embed_url, EMBED)

    def test_embed_rejects_raw_html_v1_keys_redirects_duplicate_payload_and_arbitrary_frames(self):
        for value in ('<iframe src="' + EMBED + '"></iframe>', "https://evil.example/maps/embed?pb=!1m2",
                      "https://www.google.com/maps/embed/v1/place?key=secret&q=Business",
                      EMBED + "&key=secret", EMBED + "&pb=!other", EMBED + "&url=https://evil.example",
                      "https://www.google.com/maps/embed?pb=", "https://www.google.com/maps/embed?pb=not-google-payload",
                      EMBED + "#token=private", EMBED + "&hl=es&hl=en", EMBED + "&hl=%0A"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                google_maps_embed_url(value)

    def test_embed_needs_a_separate_profile_destination_and_extra_fields_are_forbidden(self):
        for fields in ({"google_maps_embed_url": EMBED}, {"rating": 5}, {"google_verified": True}, {"reviews": ["copied"]}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                ProfessionalGoogleFields.model_validate(fields)

    def test_size_and_control_limits_apply_to_both_fields(self):
        for parser, value in ((google_profile_url, PROFILE + "x" * 2048), (google_maps_embed_url, EMBED + "x" * 4096),
                              (google_profile_url, PROFILE + "\x00"), (google_maps_embed_url, EMBED + "\n" + "bad")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parser(value)


if __name__ == "__main__":
    unittest.main()

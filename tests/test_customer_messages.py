"""Pure copy/privacy tests; no configuration, database or provider imports."""
import unittest
from uuid import uuid4
from utils.customer_messages import customer_message, owner_sms


class CustomerMessageTests(unittest.TestCase):
    def test_bilingual_next_steps_for_every_event(self):
        for event in ("signup", "buyer_interest", "seller_interest", "rental_interest", "property_inquiry", "booking"):
            en, es = customer_message(event, "Synthetic", "en"), customer_message(event, "Synthetic", "es")
            self.assertNotEqual(en.subject, es.subject)
            for result in (en, es):
                self.assertIn("https://ilovedrrealty.com/", result.text)
                self.assertIn("STOP", result.sms)
                self.assertNotIn("Synthetic", result.sms)
                self.assertIn("I Love DR Realty", result.sms)

    def test_html_escapes_names_and_does_not_accept_a_url_as_property(self):
        result = customer_message("property_inquiry", '<img src=x onerror="alert(1)">', public_listing_id="https://evil.example/secret")
        self.assertNotIn('<img src=x', result.html)
        self.assertIn('&lt;img', result.html)
        self.assertNotIn("evil.example", result.html)
        self.assertIn("/search/", result.text)

    def test_only_explicit_public_context_links_a_listing(self):
        key = uuid4()
        self.assertIn(f"/listing/{key}/", customer_message("property_inquiry", public_listing_id=key).text)
        self.assertNotIn(str(key), customer_message("signup", public_listing_id=key).text)

    def test_booking_and_inquiry_do_not_claim_confirmation(self):
        self.assertIn("not a confirmed booking", customer_message("booking").text)
        self.assertIn("does not reserve", customer_message("property_inquiry").text)
        self.assertIn("no es una reserva confirmada", customer_message("booking", locale="es").text)

    def test_owner_texts_only_link_secure_tools_without_customer_data(self):
        for event in ("signup", "buyer_interest", "seller_interest", "rental_interest", "property_inquiry", "booking"):
            result = owner_sms(event)
            self.assertIn("https://app.ilovedrrealty.com/", result)
            self.assertNotIn("@", result)

    def test_unknown_events_rejected(self):
        for builder in (customer_message, owner_sms):
            with self.assertRaises(ValueError):
                builder("marketing_campaign")

    def test_non_whitespace_control_characters_removed_from_customer_name(self):
        result = customer_message("signup", "Synthetic\x00\x1b\x7f Name")
        for value in (result.text, result.html):
            self.assertIn("Synthetic Name", value)
            for control in ("\x00", "\x1b", "\x7f"):
                self.assertNotIn(control, value)

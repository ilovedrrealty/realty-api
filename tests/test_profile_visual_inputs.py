"""Pure profile-photo selection intent and backward-compatibility contracts."""
import unittest
from uuid import uuid4
from pydantic import ValidationError
from schemas.property_network import ProfileWrite


class ProfileVisualInputs(unittest.TestCase):
    def test_older_client_omission_is_distinct_from_explicit_removal(self):
        old = ProfileWrite(expected_version=2, display_name="Network alias")
        remove = ProfileWrite(expected_version=2, display_name="Network alias", avatar_media_id=None)
        self.assertNotIn("avatar_media_id", old.model_fields_set)
        self.assertIn("avatar_media_id", remove.model_fields_set)
        self.assertIsNone(remove.avatar_media_id)

    def test_deliberate_uuid_choices_roundtrip_without_account_or_url_fields(self):
        avatar, cover = uuid4(), uuid4()
        value = ProfileWrite(expected_version=0, display_name="Network alias", avatar_media_id=avatar, cover_media_id=cover)
        self.assertEqual((value.avatar_media_id, value.cover_media_id), (avatar, cover))
        self.assertNotIn("avatar_url", value.model_dump())
        self.assertNotIn("email", value.model_dump())

    def test_account_urls_raw_objects_and_unknown_fields_refuse(self):
        for field, value in (("avatar_media_id", "https://identity.example.invalid/photo"),
                             ("cover_media_id", {}), ("avatar_url", "https://identity.example.invalid/photo")):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ProfileWrite.model_validate({"expected_version": 1, "display_name": "Network alias", field: value})


if __name__ == "__main__":
    unittest.main()

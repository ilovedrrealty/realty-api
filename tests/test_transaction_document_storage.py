import hashlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from PIL import Image

from utils import transaction_document_storage as documents


PRIVATE_BUCKET = "isolated-closing-files"
IDS = tuple(UUID(int=value) for value in range(1, 4))


class MemoryStorage:
    def __init__(self):
        self.metadata = SimpleNamespace(
            id=PRIVATE_BUCKET,
            name=PRIVATE_BUCKET,
            public=False,
            file_size_limit=documents.MAX_DOCUMENT_BYTES,
            allowed_mime_types=list(documents.DOCUMENT_TYPES),
        )
        self.objects = {}
        self.uploads = []
        self.lose_response = False

    def get_bucket(self, bucket_id):
        if bucket_id != PRIVATE_BUCKET:
            raise AssertionError("Unexpected bucket")
        return self.metadata

    def from_(self, bucket_id):
        if bucket_id != PRIVATE_BUCKET:
            raise AssertionError("Unexpected bucket")
        return self

    def upload(self, key, data, options):
        self.uploads.append((key, options))
        if key in self.objects:
            raise ValueError("Already stored")
        self.objects[key] = data
        if self.lose_response:
            raise TimeoutError("Upload response was lost")

    def download(self, key):
        return self.objects[key]


class TransactionDocumentStorageTests(unittest.TestCase):
    def setUp(self):
        self.storage = MemoryStorage()
        self.pdf = self.make_file("PDF")
        self.content = documents.prepare_document(self.pdf, "application/pdf")
        account = SimpleNamespace(transaction_document_bucket=PRIVATE_BUCKET, storage_bucket="public-media")
        settings_patch = patch.object(documents, "settings", account)
        client_patch = patch.object(documents, "_get_client", return_value=SimpleNamespace(storage=self.storage))
        self.settings = settings_patch.start()
        self.client = client_patch.start()
        self.addCleanup(settings_patch.stop)
        self.addCleanup(client_patch.stop)

    @staticmethod
    def make_file(format_name):
        output = io.BytesIO()
        with Image.new("RGB", (12, 9), "white") as image:
            image.save(output, format=format_name)
        return output.getvalue()

    def test_supported_documents_keep_original_bytes(self):
        for mime, format_name in (("application/pdf", "PDF"), ("image/jpeg", "JPEG"), ("image/png", "PNG")):
            with self.subTest(mime=mime):
                data = self.make_file(format_name)
                content = documents.prepare_document(data, mime)
                self.assertEqual(content, documents.DocumentContent(mime, len(data), hashlib.sha256(data).hexdigest()))

    def test_declared_type_and_size_are_enforced(self):
        for payload, mime in (
            (self.pdf, "image/jpeg"),
            (self.pdf, "application/octet-stream"),
            (b"%PDF-1.4\ntruncated", "application/pdf"),
            (b"", "image/png"),
            (b"0" * (documents.MAX_DOCUMENT_BYTES + 1), "application/pdf"),
        ):
            with self.subTest(mime=mime, length=len(payload)):
                with self.assertRaises(documents.InvalidDocument):
                    documents.prepare_document(payload, mime)
        self.client.assert_not_called()

    def test_shared_or_unconfigured_bucket_never_reaches_storage(self):
        for configured in ("", "property-network-private", "listing-images", "public-media", "../../bucket"):
            with self.subTest(configured=configured):
                self.settings.transaction_document_bucket = configured
                with self.assertRaises(documents.DocumentStorageUnavailable):
                    documents.store_document(*IDS, self.pdf, self.content)
        self.client.assert_not_called()

    def test_bucket_metadata_must_enforce_privacy_type_and_size(self):
        for name, bad_value in (
            ("id", "another-bucket"),
            ("name", "another-bucket"),
            ("public", True),
            ("file_size_limit", None),
            ("file_size_limit", documents.MAX_DOCUMENT_BYTES + 1),
            ("allowed_mime_types", None),
            ("allowed_mime_types", ["image/webp"]),
        ):
            with self.subTest(name=name), patch.object(self.storage.metadata, name, bad_value):
                with self.assertRaises(documents.DocumentStorageUnavailable):
                    documents.store_document(*IDS, self.pdf, self.content)
        self.assertEqual(self.storage.uploads, [])

    def test_immutable_retry_confirms_lost_response_and_duplicate(self):
        self.storage.lose_response = True
        documents.store_document(*IDS, self.pdf, self.content)
        documents.store_document(*IDS, self.pdf, self.content)

        key = "/".join(("rooms", *(part.hex for part in IDS)))
        self.assertEqual(self.storage.objects, {key: self.pdf})
        self.assertEqual(documents.read_document(*IDS, self.content), self.pdf)
        self.assertEqual(
            self.storage.uploads,
            [(key, {"content-type": "application/pdf", "cache-control": "0", "x-upsert": "false"})] * 2,
        )

    def test_changed_payload_and_tampered_read_are_rejected(self):
        other_bytes = b"!" + self.pdf[1:]
        with self.assertRaises(documents.DocumentIntegrityError):
            documents.store_document(*IDS, other_bytes, self.content)
        self.assertEqual(self.storage.uploads, [])
        documents.store_document(*IDS, self.pdf, self.content)
        key = self.storage.uploads[0][0]
        self.storage.objects[key] = other_bytes
        with self.assertRaises(documents.DocumentIntegrityError):
            documents.read_document(*IDS, self.content)
        with self.assertRaises(documents.DocumentIntegrityError):
            documents.store_document(*IDS, self.pdf, self.content)
        self.assertEqual(self.storage.objects[key], other_bytes)

    def test_provider_errors_do_not_expose_upstream_text(self):
        with patch.object(self.storage, "upload", side_effect=TimeoutError("provider internals")):
            with self.assertRaisesRegex(documents.DocumentStorageUnavailable, "could not be read") as result:
                documents.store_document(*IDS, self.pdf, self.content)
        self.assertIsNone(result.exception.__cause__)
        self.assertNotIn("provider internals", str(result.exception))
        self.assertEqual(self.storage.objects, {})

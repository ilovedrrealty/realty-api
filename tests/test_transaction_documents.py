"""Exercise permissioned uploads and exact version access with a synthetic provider."""

import hashlib
import io
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from PIL import Image

import test_transaction_room as room_fixture
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db
from routes import transaction_documents as document_routes
from services import transaction_documents as document_service
from utils.transaction_document_storage import DocumentStorageUnavailable


MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/de248ab5c9f3_private_transaction_documents.py"


class TransactionDocumentTests(room_fixture.TransactionRoomFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        spec = spec_from_file_location(MIGRATION.stem, MIGRATION)
        migration = module_from_spec(spec)
        spec.loader.exec_module(migration)
        with cls.engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            assert all(connection.execute(text(
                "SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass(:table_name)"
            ), {"table_name": table}).scalar_one() for table in migration.TABLES)

        def local_db():
            with Session(cls.engine) as session:
                yield session

        app = FastAPI()
        app.include_router(room_fixture.routes.router)
        app.include_router(document_routes.router)
        app.dependency_overrides[get_db] = local_db
        cls.client = TestClient(app)
        cls.addClassCleanup(cls.client.close)

    def start_documents(self):
        self.objects = {}
        writing = patch.object(document_routes, "store_document", side_effect=self.synthetic_store)
        reading = patch.object(document_service, "read_document", side_effect=self.synthetic_read)
        self.writing, self.reading = writing.start(), reading.start()
        self.addCleanup(writing.stop)
        self.addCleanup(reading.stop)
        room = self.make_room()
        return f"/{room['id']}", f"/{room['id']}/documents"

    def synthetic_store(self, room_id, document_id, version_id, data, expected):
        self.assertEqual(hashlib.sha256(data).hexdigest(), expected.sha256)
        key = (room_id, document_id, version_id)
        self.assertEqual(self.objects.setdefault(key, data), data)

    def synthetic_read(self, room_id, document_id, version_id, expected):
        data = self.objects[(room_id, document_id, version_id)]
        self.assertEqual(hashlib.sha256(data).hexdigest(), expected.sha256)
        return data

    @staticmethod
    def file(color="white"):
        output = io.BytesIO()
        with Image.new("RGB", (8, 8), color) as image:
            image.save(output, format="PNG")
        return output.getvalue()

    def reservation(self, data, **fields):
        payload = dict(
            title="Synthetic closing document", file_name="Cierre verificable.png", request_id=str(uuid4()),
            byte_count=len(data), sha256=hashlib.sha256(data).hexdigest(), content_type="image/png",
        )
        return {**payload, **fields}

    def upload_path(self, path, receipt):
        return path + f"/{receipt['document_id']}/versions/{receipt['version_id']}"

    def upload(self, actor, version, data, status=200, **headers):
        return self.request(actor, "PUT", version + "/content", status, content=data,
                            headers={"Content-Type": "image/png", **headers})

    def review(self, room, version, **fields):
        current = self.request("realtor", "GET", room)
        payload = {"expected_version": current["version"], "status": "approved", **fields}
        return self.request("realtor", "POST", version + "/review", body=payload)

    def receive(self, actor, version, verified_data):
        response = self.client.get("/transactions" + version + "/download",
                                   headers={"Authorization": "Bearer " + self.tokens[actor]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Content-Disposition"], "attachment; filename*=UTF-8''Cierre%20verificable.png")
        self.assertNotIn("location", response.headers)
        self.assertEqual(response.content, verified_data)

    def test_documents_do_not_share_pending_or_private_versions_and_are_audited(self):
        room, documents = self.start_documents()
        data = self.file()
        payload = self.reservation(data)
        self.request("buyer", "POST", documents, 422, {**payload, "file_name": "Cierre.html"})
        reserved = self.request("buyer", "POST", documents, body=payload)
        version = self.upload_path(documents, reserved)
        self.assertEqual(reserved["status"], "reserved")
        self.assertEqual(self.request("buyer", "POST", documents, body=payload), reserved)
        self.request("buyer", "POST", documents, 409, {**payload, "file_name": "different.png"})
        self.assertEqual(self.request("admin", "GET", documents), {"items": [], "can_upload": True})
        self.request("other_buyer", "GET", documents, 404)
        self.request(None, "GET", documents, 401)
        self.upload("realtor", version, data, 404)
        self.upload("buyer", version, data, 403, Origin="https://other.example.invalid")
        self.upload("buyer", version, data + b"x", 413)
        self.upload("buyer", version, data[:-1], 422)
        self.writing.assert_not_called()

        submitted = self.upload("buyer", version, data)
        self.assertEqual(submitted["status"], "submitted")
        self.assertEqual(self.upload("buyer", version, data), submitted)
        self.assertEqual(self.writing.call_count, 1)
        self.request("buyer", "GET", version + "/download", 404)
        self.reading.assert_not_called()
        own = self.request("buyer", "GET", documents)["items"][0]["versions"][0]
        self.assertEqual((own["can_download"], own["can_review"], own["share_with_buyer"]), (False, False, None))
        self.assertFalse({"sha256", "request_id", "uploader_id", "object_key", "url"}.intersection(own))

        self.receive("realtor", version, data)
        self.review(room, version)
        self.request("buyer", "GET", version + "/download", 404)
        self.review(room, version, share_with_buyer=True)
        self.receive("buyer", version, data)
        self.review(room, version, status="rejected")
        self.request("buyer", "GET", version + "/download", 404)
        staff_history = self.request("admin", "GET", room + "/history")["items"]
        self.assertTrue({
            "document_version_submitted", "document_version_approved",
            "document_version_access_changed", "document_version_withdrawn",
        }.issubset({row["action"] for row in staff_history}))
        self.assertFalse(any(row["action"].startswith("document_")
                             for row in self.request("buyer", "GET", room + "/history")["items"]))

    def test_professional_share_is_to_one_current_grant_and_each_version_is_independent(self):
        room, documents = self.start_documents()
        detail = self.request("realtor", "GET", room)
        _, grant = self.invite(detail)
        self.request("professional", "GET", documents, 404)
        self.request("professional", "POST", f"/invitations/{grant}/accept", body={})

        data = self.file()
        receipt = self.request("realtor", "POST", documents, body=self.reservation(data))
        first = self.upload_path(documents, receipt)
        self.upload("realtor", first, data)
        self.review(room, first, professional_grant_id=grant)
        self.receive("professional", first, data)
        self.request("buyer", "GET", first + "/download", 404)
        self.request("professional", "POST", documents, 404,
                     self.reservation(data, document_id=receipt["document_id"]))

        changed = self.file("black")
        second_receipt = self.request("realtor", "POST", documents,
                                      body=self.reservation(changed, document_id=receipt["document_id"]))
        self.assertEqual(second_receipt["number"], 2)
        second = self.upload_path(documents, second_receipt)
        self.upload("realtor", second, changed)
        self.request("professional", "GET", second + "/download", 404)
        self.review(room, second, share_with_buyer=True)
        self.receive("buyer", second, changed)
        self.receive("professional", first, data)
        current = self.request("realtor", "GET", room)
        revoked = self.request("realtor", "POST", room + f"/members/{grant}/revoke",
                               body={"expected_version": current["version"]})
        self.request("professional", "GET", first + "/download", 404)
        _, new_grant = self.invite(revoked)
        self.assertNotEqual(new_grant, grant)
        self.request("professional", "POST", f"/invitations/{new_grant}/accept", body={})
        self.assertEqual(self.request("professional", "GET", documents)["items"], [])
        self.request("professional", "GET", first + "/download", 404)

    def test_provider_failure_can_resume_without_exposing_partial_object(self):
        room, documents = self.start_documents()
        data = self.file()
        payload = self.reservation(data)
        receipt = self.request("buyer", "POST", documents, body=payload)
        version = self.upload_path(documents, receipt)
        self.writing.side_effect = DocumentStorageUnavailable("PRIVATE_PROVIDER_VALUE")
        self.upload("buyer", version, data, 503)
        self.assertEqual(self.request("realtor", "GET", documents)["items"], [])
        self.assertEqual(self.request("buyer", "POST", documents, body=payload), receipt)
        self.writing.side_effect = self.synthetic_store
        self.upload("buyer", version, data)
        self.assertEqual(self.request("realtor", "GET", room)["version"], 2)

"""Personal favorites through real private routes on disposable PostgreSQL."""
import importlib.util
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import test_service_visit as visit_fixture
import test_service_workflow as workflow_fixture

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from models.service_favorites import NetworkServiceFavorite as Favorite
from models.service_workflow import NetworkServicePreference as Preference
from models.user import User


@unittest.skipUnless(os.environ.get("SEARCH_FILTER_TEST_DATABASE_URL"), "Requires reviewed disposable loopback Postgres")
class PrivateServiceFavorites(unittest.TestCase):
    _workflow = workflow_fixture.PrivateServiceWorkflow
    _visit = visit_fixture.PrivateAwardedServiceWork
    cleanup_owned_schema = classmethod(_workflow.cleanup_owned_schema.__func__)
    setUp = _workflow.setUp
    patched = _workflow.patched
    stop_patches = _workflow.stop_patches
    send = _workflow.send
    ok = _workflow.ok
    opt = _workflow.opt
    parallel = _workflow.parallel
    assert_private = _visit.assert_private
    assert_refused = _visit.assert_refused
    path = "/service-favorites"

    @classmethod
    def setUpClass(cls):
        cls._workflow.setUpClass.__func__(cls)
        path = Path(__file__).resolve().parents[1] / "alembic/versions/f71c5a9d243e_private_service_favorites.py"
        spec = importlib.util.spec_from_file_location("service_favorites_fixture_migration", path)
        cls.revision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.revision)
        with cls.engine.begin() as db:
            cls.revision.op = Operations(MigrationContext.configure(db))
            cls.revision.upgrade()

    def favorite(self, response):
        self.assert_private(response)
        data = self.ok(response, 201)
        self.assertEqual(set(data), {"id", "provider", "saved_at"})
        self.assertEqual(str(UUID(data["id"])), data["id"])
        self.assertIsNotNone(datetime.fromisoformat(data["saved_at"]).tzinfo)
        self.assertEqual(set(data["provider"]), {"user_code", "member_alias"})
        return data

    def save(self, provider="pro1", actor="owner"):
        return self.favorite(self.send("POST", self.path, actor, {"provider_user_code": self.codes[provider]}))

    def listed(self, actor="owner", page=1):
        response = self.send("GET", f"{self.path}?page={page}", actor)
        self.assert_private(response)
        data = self.ok(response)
        self.assertEqual(set(data), {"items", "total", "page", "page_size"})
        self.assertEqual((data["page"], data["page_size"]), (page, 30))
        return data

    def remove(self, identity, actor="owner"):
        response = self.send("DELETE", f"{self.path}/{identity}", actor, {})
        self.assert_private(response)
        self.assertEqual((response.status_code, response.content), (204, b""))

    def test_personal_access_and_exact_minimal_provider_projection(self):
        self.opt("pro1")
        self.opt("admin")
        first = self.save()
        self.assertEqual(first["provider"], {"user_code": self.codes["pro1"], "member_alias": "Deliberate alias pro1"})
        second = self.save(actor="manager")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.listed()["items"], [first])
        self.assertEqual(self.listed("manager")["items"], [second])
        self.assertEqual(self.listed("pro1")["items"], [])
        self.assert_refused(self.send("DELETE", f"{self.path}/{first['id']}", "manager", {}), 404)
        self.assert_refused(self.send("DELETE", f"{self.path}/{second['id']}", "admin", {}), 404)
        self.assert_refused(self.send("GET", self.path, "admin"), 404)
        self.assert_refused(self.send("GET", self.path, "unknown"), 401)
        self.assert_refused(self.send("GET", self.path, "suspended"), 403)
        for actor, provider in (("admin", "pro1"), ("owner", "admin"), ("owner", "pro2"), ("pro1", "pro1")):
            self.assert_refused(self.send("POST", self.path, actor, {"provider_user_code": self.codes[provider]}), 404)
        for invalid in (True, 0, -1, 4.0, "4", 2147483648):
            self.assert_refused(self.send("POST", self.path, body={"provider_user_code": invalid}), 422)
        self.assert_refused(self.send("POST", self.path, body={"provider_user_code": self.codes["pro1"],
                                                               "actor_id": str(self.ids["manager"])}), 422)
        self.assert_refused(self.send("GET", self.path + "?page=0"), 422)
        self.remove(first["id"])
        self.assertEqual(self.listed()["items"], [])
        self.assertEqual(self.listed("manager")["items"], [second])

    def test_current_alias_code_and_unavailable_placeholders_remain_removable(self):
        self.opt("pro1")
        original = self.save()
        self.opt("pro1", member_alias="Updated private test alias")
        updated_code = self.codes["pro1"] + 1000
        with Session(self.engine) as db:
            db.get(User, self.ids["pro1"]).user_code = updated_code
            db.commit()
        expected = {**original, "provider": {"user_code": updated_code, "member_alias": "Updated private test alias"}}
        self.assertEqual(self.listed()["items"], [expected])
        self.assertEqual(self.favorite(self.send("POST", self.path, body={"provider_user_code": updated_code})), expected)
        self.assert_refused(self.send("POST", self.path, body={"provider_user_code": self.codes["pro1"]}), 404)
        placeholder = {**original, "provider": None}
        for field, value, restore in (("email_verified", False, True), ("status", "suspended", "active"),
                                      ("role", "admin", "buyer"), ("user_code", None, updated_code)):
            with Session(self.engine) as db:
                setattr(db.get(User, self.ids["pro1"]), field, value)
                db.commit()
            self.assertEqual(self.listed()["items"], [placeholder])
            self.assert_refused(self.send("POST", self.path, body={"provider_user_code": updated_code}), 404)
            with Session(self.engine) as db:
                setattr(db.get(User, self.ids["pro1"]), field, restore)
                db.commit()
        self.opt("pro1", service_opt_in=False)
        self.assertEqual(self.listed()["items"], [placeholder])
        self.assert_refused(self.send("POST", self.path, body={"provider_user_code": updated_code}), 404)
        self.remove(original["id"])
        self.assertEqual(self.listed()["total"], 0)

    def test_concurrent_save_deduplicates_and_pages_stably(self):
        self.opt("pro1")
        calls = [lambda: self.send("POST", self.path, body={"provider_user_code": self.codes["pro1"]}) for _ in range(2)]
        first, duplicate = [self.favorite(response) for response in self.parallel(calls)]
        self.assertEqual(first, duplicate)
        instant = datetime(2026, 11, 12, 17, tzinfo=timezone.utc)
        identifiers = [first["id"]]
        with Session(self.engine) as db:
            db.get(Favorite, UUID(first["id"])).saved_at = instant
            for number in range(30):
                provider = User(id=uuid4(), user_code=8000 + number, email=f"favorites-fixture-{number}@example.com",
                                display_name="Synthetic provider", role="buyer", status="active", email_verified=True)
                db.add(provider)
                db.flush()
                db.add(Preference(user_id=provider.id, service_opt_in=True, member_alias=f"Synthetic alias {number}",
                                  category_ids=["pool_service"], property_class_ids=["house_villa"], version=1))
                favorite_id = uuid4()
                db.add(Favorite(id=favorite_id, actor_id=self.ids["owner"], provider_id=provider.id, saved_at=instant))
                identifiers.append(str(favorite_id))
            db.commit()
        page_one, page_two = self.listed(), self.listed(page=2)
        self.assertEqual((page_one["total"], page_two["total"], len(page_one["items"]), len(page_two["items"])), (31, 31, 30, 1))
        self.assertEqual([row["id"] for row in page_one["items"] + page_two["items"]], sorted(identifiers, reverse=True))

    def test_migration_denies_direct_roles_and_self_rows(self):
        with self.engine.connect() as db, db.begin() as rollback:
            for role in ("anon", "authenticated", "service_role"):
                db.execute(text(f"""DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN CREATE ROLE {role} NOLOGIN; END IF;
                END $$"""))
            db.execute(text("GRANT ALL ON network_service_favorites TO anon, authenticated, service_role"))
            db.execute(text("GRANT SELECT (provider_id) ON network_service_favorites TO anon, authenticated, service_role"))
            acl = text("""SELECT rolname,
                has_table_privilege(oid, to_regclass('network_service_favorites'), 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),
                has_column_privilege(oid, to_regclass('network_service_favorites'), 'provider_id', 'SELECT')
                FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role') ORDER BY rolname""")
            roles = ("anon", "authenticated", "service_role")
            self.assertEqual([tuple(row) for row in db.execute(acl)], [(role, True, True) for role in roles])
            self.revision.op = Operations(MigrationContext.configure(db))
            self.revision.upgrade()
            self.assertEqual([tuple(row) for row in db.execute(acl)], [(role, False, False) for role in roles])
            rollback.rollback()

        with self.engine.begin() as db:
            self.revision.op = Operations(MigrationContext.configure(db))
            self.revision.downgrade()
            self.assertIsNone(db.execute(text("SELECT to_regclass('network_service_favorites')")).scalar_one())
            self.revision.upgrade()
            private = db.execute(text("""SELECT c.relrowsecurity, NOT EXISTS (
                SELECT 1 FROM aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a WHERE a.grantee=0
            ) FROM pg_class c WHERE c.oid=to_regclass('network_service_favorites')""")).one()
            self.assertEqual(tuple(private), (True, True))

        self.opt("pro1")
        saved = self.save()
        for provider_id, expected in ((self.ids["owner"], "23514"), (self.ids["pro1"], "23505")):
            with self.assertRaises(DBAPIError) as rejected, Session(self.engine) as db:
                db.add(Favorite(id=uuid4(), actor_id=self.ids["owner"], provider_id=provider_id))
                db.commit()
            self.assertEqual(rejected.exception.orig.pgcode, expected)
        self.assertEqual(self.listed()["items"], [saved])


if __name__ == "__main__":
    unittest.main()

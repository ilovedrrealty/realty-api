"""Focused group contracts; native cases run only on an owned hosted database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
import os
import secrets
import threading
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4
from fastapi import HTTPException
from pydantic import ValidationError
from schemas.social_groups import (GroupCreate, GroupEdit, ArchiveGroup, MembershipAction, ManageMember,
    CreateInviteLink, ResolveInvite, AcceptInvite, RevokeInviteLink, GroupPostCreate,
    GroupCommentCreate, ModeratePost, HideComment)


class GroupInputTest(unittest.TestCase):
    def test_private_defaults_and_explicit_alias(self):
        body = GroupCreate(submission_id=uuid4(), name="Local group", owner_alias="Group owner")
        self.assertEqual((body.visibility, body.join_policy, body.post_policy), ("private", "approval_required", "members"))
        with self.assertRaises(ValidationError):
            GroupCreate(submission_id=uuid4(), name="Local group", owner_alias="", project_id=uuid4())

    def test_link_and_member_roles_are_bounded(self):
        with self.assertRaises(ValidationError):
            CreateInviteLink(submission_id=uuid4(), token="short", expires_in_days=1000)
        with self.assertRaises(ValidationError):
            ManageMember(submission_id=uuid4(), expected_membership_version=0, action="invite", role="admin")
        with self.assertRaises(ValidationError):
            ManageMember(submission_id=uuid4(), expected_membership_version=1, action="set_role", role="owner")

    def test_post_photos_are_unique_and_content_is_required(self):
        identity = uuid4()
        for fields in ({}, {"media_ids": [identity, identity]}, {"body": "\x00"}):
            with self.assertRaises(ValidationError):
                GroupPostCreate(submission_id=uuid4(), **fields)


@unittest.skipUnless(os.environ.get("SOCIAL_GROUPS_FIXTURE") == "yes", "requires owned isolated PostgreSQL")
class SocialGroupsTest(unittest.TestCase):
    def setUp(self):
        from database import SessionLocal
        from models.user import User
        from services import social_groups
        self.Session = SessionLocal
        self.service = social_groups
        self.db = SessionLocal()
        self.users = {}
        self.marker = uuid4().hex
        for actor, role in (("owner", "owner"), ("member", "buyer"), ("other", "realtor"), ("third", "buyer"), ("platform", "admin")):
            user = User(id=uuid4(), email=f"groups-{actor}-{self.marker}@example.com", password_hash="synthetic-not-used",
                        role=role, status="active", email_verified=True, display_name="Private account name sentinel")
            self.db.add(user)
            self.users[actor] = user
        self.db.commit()

    def tearDown(self):
        self.db.rollback()
        self.db.close()

    def group(self, **fields):
        body = GroupCreate(submission_id=uuid4(), name="Synthetic group " + self.marker, owner_alias="Shared owner alias", **fields)
        receipt = self.service.create_group(self.db, self.users["owner"], body)
        return UUID(receipt["target_id"]), receipt["current"]

    def invite(self, group_id, actor="member", version=0):
        body = ManageMember(submission_id=uuid4(), expected_membership_version=version, action="invite")
        return self.service.manage_member(self.db, self.users["owner"], group_id, self.users[actor].user_code, body)["current"]

    def accept(self, group_id, actor="member", version=1):
        return self.service.own_membership_action(self.db, self.users[actor], group_id, MembershipAction(
            submission_id=uuid4(), expected_membership_version=version, action="accept_invite", member_alias="Shared member alias"))["current"]

    def link(self, group_id, **fields):
        token = secrets.token_urlsafe(32)
        receipt = self.service.create_link(self.db, self.users["owner"], group_id,
            CreateInviteLink(submission_id=uuid4(), token=token, **fields))
        return token, receipt["current"]

    def edit(self, group_id, current, **changes):
        fields = {key: current[key] for key in ("name", "description", "purpose", "area_label", "visibility", "join_policy", "post_policy", "comments_enabled")}
        image_id = changes.pop("image_media_id", (current.get("image") or {}).get("id"))
        fields.update(changes)
        body = GroupEdit(submission_id=uuid4(), expected_version=current["version"], image_media_id=image_id, **fields)
        return self.service.edit_group(self.db, self.users["owner"], group_id, body)["current"]

    def assert_unavailable(self, call):
        with self.assertRaises(HTTPException) as error:
            call()
        self.assertIn(error.exception.status_code, (403, 404))
        self.db.rollback()

    def test_private_group_create_replay_and_targeted_consent(self):
        from models.social_groups import NetworkGroup, NetworkGroupAction
        body = GroupCreate(submission_id=uuid4(), name="Synthetic group " + self.marker, owner_alias="Owner alias")
        first = self.service.create_group(self.db, self.users["owner"], body)
        self.assertEqual(self.service.create_group(self.db, self.users["owner"], body)["target_id"], first["target_id"])
        group_id = UUID(first["target_id"])
        self.assert_unavailable(lambda: self.service.group_for(self.db, self.users["member"], group_id))
        pending = self.invite(group_id)
        self.assertEqual(pending["state"], "invited")
        self.assert_unavailable(lambda: self.service.posts(self.db, self.users["member"], group_id, 1))
        active = self.accept(group_id)
        self.assertEqual((active["state"], active["role"], active["member_alias"]), ("active", "member", "Shared member alias"))
        view = self.service.group_view(self.db, self.users["member"], self.service.group_for(self.db, self.users["member"], group_id))
        self.assertTrue(view["can_post"])
        self.assertNotIn("Private account name sentinel", str(view))
        self.assertEqual(self.db.query(NetworkGroup).filter_by(id=group_id).count(), 1)
        self.assertEqual(self.db.query(NetworkGroupAction).filter_by(group_id=group_id, operation="group_create").count(), 1)

    def test_registered_discovery_request_cancel_approval_and_leave(self):
        group_id, _ = self.group(visibility="network")
        before = self.service.listed(self.db, self.users["member"], "discover", self.marker, 1)
        self.assertEqual(before["total"], 1)
        requested = self.service.own_membership_action(self.db, self.users["member"], group_id, MembershipAction(
            submission_id=uuid4(), expected_membership_version=0, action="request", member_alias="Request alias"))["current"]
        cancelled = self.service.own_membership_action(self.db, self.users["member"], group_id, MembershipAction(
            submission_id=uuid4(), expected_membership_version=requested["version"], action="cancel"))["current"]
        requested = self.service.own_membership_action(self.db, self.users["member"], group_id, MembershipAction(
            submission_id=uuid4(), expected_membership_version=cancelled["version"], action="request", member_alias="Request alias"))["current"]
        approved = self.service.manage_member(self.db, self.users["owner"], group_id, self.users["member"].user_code,
            ManageMember(submission_id=uuid4(), expected_membership_version=requested["version"], action="approve"))["current"]
        left = self.service.own_membership_action(self.db, self.users["member"], group_id, MembershipAction(
            submission_id=uuid4(), expected_membership_version=approved["version"], action="leave"))["current"]
        self.assertEqual(left["state"], "left")

    def test_direct_invitation_decline_revoke_and_expiry(self):
        from models.social_groups import NetworkGroupMembership
        group_id, _ = self.group()
        invite = self.invite(group_id)
        declined = self.service.own_membership_action(self.db, self.users["member"], group_id, MembershipAction(
            submission_id=uuid4(), expected_membership_version=invite["version"], action="decline_invite"))["current"]
        invite = self.invite(group_id, version=declined["version"])
        revoked = self.service.manage_member(self.db, self.users["owner"], group_id, self.users["member"].user_code,
            ManageMember(submission_id=uuid4(), expected_membership_version=invite["version"], action="revoke_invite"))["current"]
        invite = self.invite(group_id, version=revoked["version"])
        with patch.object(self.service, "now", return_value=invite["invite_expires_at"] + timedelta(seconds=1)):
            self.assert_unavailable(lambda: self.accept(group_id, version=invite["version"]))
        row = self.db.query(NetworkGroupMembership).filter_by(group_id=group_id, user_id=self.users["member"].id).one()
        self.assertEqual(row.state, "invited")

    def test_link_hash_only_idempotency_revocation_and_ban(self):
        from models.social_groups import NetworkGroupInviteLink, NetworkGroupAction
        group_id, _ = self.group()
        token, link = self.link(group_id)
        resolved = self.service.resolve_link(self.db, self.users["member"], ResolveInvite(token=token))
        self.assertEqual(resolved["grants_role"], "member")
        body = AcceptInvite(token=token, submission_id=uuid4(), member_alias="Link alias")
        accepted = self.service.accept_link(self.db, self.users["member"], body)
        self.assertEqual(self.service.accept_link(self.db, self.users["member"], body)["target_id"], accepted["target_id"])
        self.assertEqual(self.db.get(NetworkGroupInviteLink, UUID(link["id"])).use_count, 1)
        self.assertNotIn(token, str([row.result_versions for row in self.db.query(NetworkGroupAction).filter_by(group_id=group_id)]))
        self.assertNotEqual(self.db.get(NetworkGroupInviteLink, UUID(link["id"])).token_sha256, token)
        self.service.revoke_link(self.db, self.users["owner"], group_id, UUID(link["id"]),
            RevokeInviteLink(submission_id=uuid4(), expected_version=link["version"]))
        self.assert_unavailable(lambda: self.service.resolve_link(self.db, self.users["other"], ResolveInvite(token=token)))
        member = accepted["current"]
        banned = self.service.manage_member(self.db, self.users["owner"], group_id, self.users["member"].user_code,
            ManageMember(submission_id=uuid4(), expected_membership_version=member["version"], action="block"))["current"]
        self.assertEqual(banned["state"], "blocked")
        self.assert_unavailable(lambda: self.service.group_for(self.db, self.users["member"], group_id))

    def test_last_link_use_is_transactionally_serialized(self):
        group_id, _ = self.group()
        token, link = self.link(group_id, max_uses=1)
        ids = [self.users["member"].id, self.users["other"].id]
        barrier = threading.Barrier(2)

        def accept(user_id):
            from models.user import User
            with self.Session() as db:
                user = db.get(User, user_id)
                barrier.wait(timeout=10)
                try:
                    self.service.accept_link(db, user, AcceptInvite(token=token, submission_id=uuid4(), member_alias="Racing member"))
                    return 200
                except HTTPException as error:
                    db.rollback()
                    return error.status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(accept, ids))
        self.assertEqual(sorted(outcomes), [200, 404])
        from models.social_groups import NetworkGroupInviteLink
        self.db.expire_all()
        self.assertEqual(self.db.get(NetworkGroupInviteLink, UUID(link["id"])).use_count, 1)

    def test_visibility_withdrawal_never_republishes_private_history(self):
        group_id, current = self.group()
        private = self.service.create_post(self.db, self.users["owner"], group_id,
            GroupPostCreate(submission_id=uuid4(), body="Private historical post"))["current"]
        with self.assertRaises(HTTPException) as unpublished:
            self.edit(group_id, current, visibility="network")
        self.assertEqual(unpublished.exception.status_code, 422)
        self.db.rollback()
        current = self.edit(group_id, current, visibility="network", publication_acknowledged=True)
        public = self.service.create_post(self.db, self.users["owner"], group_id,
            GroupPostCreate(submission_id=uuid4(), body="Registered network post"))["current"]
        visible = self.service.posts(self.db, self.users["other"], group_id, 1)
        self.assertEqual([row["id"] for row in visible["items"]], [public["id"]])
        self.assert_unavailable(lambda: self.service.post_for(self.db, self.users["other"], UUID(private["id"])))
        current = self.edit(group_id, current, visibility="private")
        self.assert_unavailable(lambda: self.service.post_for(self.db, self.users["other"], UUID(public["id"])))
        self.edit(group_id, current, visibility="network", publication_acknowledged=True)
        self.assertEqual(self.service.posts(self.db, self.users["other"], group_id, 1)["total"], 1)
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError
        with self.assertRaises(DBAPIError):
            self.db.execute(text("UPDATE network_group_posts SET created_audience='network',version=version+1 WHERE id=:id"),
                            {"id": UUID(private["id"])})
        self.db.rollback()

    def test_open_join_group_admin_boundaries_and_explicit_unblock(self):
        group_id, _ = self.group(visibility="network", join_policy="open")
        joined = self.service.own_membership_action(self.db, self.users["member"], group_id, MembershipAction(
            submission_id=uuid4(), expected_membership_version=0, action="join", member_alias="Joining alias"))["current"]
        admin = self.service.manage_member(self.db, self.users["owner"], group_id, self.users["member"].user_code,
            ManageMember(submission_id=uuid4(), expected_membership_version=joined["version"], action="set_role", role="admin"))["current"]
        invited = self.service.manage_member(self.db, self.users["member"], group_id, self.users["other"].user_code,
            ManageMember(submission_id=uuid4(), expected_membership_version=0, action="invite"))["current"]
        other = self.accept(group_id, actor="other", version=invited["version"])
        with self.assertRaises(HTTPException) as denied:
            self.service.manage_member(self.db, self.users["member"], group_id, self.users["other"].user_code,
                ManageMember(submission_id=uuid4(), expected_membership_version=other["version"], action="set_role", role="admin"))
        self.assertEqual(denied.exception.status_code, 403)
        self.db.rollback()
        blocked = self.service.manage_member(self.db, self.users["owner"], group_id, self.users["member"].user_code,
            ManageMember(submission_id=uuid4(), expected_membership_version=admin["version"], action="block"))["current"]
        unblocked = self.service.manage_member(self.db, self.users["owner"], group_id, self.users["member"].user_code,
            ManageMember(submission_id=uuid4(), expected_membership_version=blocked["version"], action="unblock"))["current"]
        self.assertEqual((unblocked["state"], unblocked["role"]), ("removed", "member"))
        rejoined = self.service.own_membership_action(self.db, self.users["member"], group_id, MembershipAction(
            submission_id=uuid4(), expected_membership_version=unblocked["version"], action="join", member_alias="Rejoined alias"))["current"]
        self.assertEqual((rejoined["state"], rejoined["role"]), ("active", "member"))

    def test_group_moderator_permissions_and_hidden_photo_comments(self):
        group_id, _ = self.group(post_policy="moderated")
        self.invite(group_id)
        active = self.accept(group_id)
        pending = self.service.create_post(self.db, self.users["member"], group_id,
            GroupPostCreate(submission_id=uuid4(), body="Pending member post"))["current"]
        self.assertEqual(pending["status"], "pending")
        promoted = self.service.manage_member(self.db, self.users["owner"], group_id, self.users["member"].user_code,
            ManageMember(submission_id=uuid4(), expected_membership_version=active["version"], action="set_role", role="moderator"))["current"]
        with self.assertRaises(HTTPException) as denied:
            self.service.manage_member(self.db, self.users["member"], group_id, self.users["other"].user_code,
                ManageMember(submission_id=uuid4(), expected_membership_version=0, action="invite"))
        self.assertEqual(denied.exception.status_code, 403)
        self.db.rollback()
        approved = self.service.moderate_post(self.db, self.users["member"], UUID(pending["id"]),
            ModeratePost(submission_id=uuid4(), expected_version=pending["version"], action="approve"))["current"]
        self.assertEqual(approved["status"], "visible")
        comment = self.service.create_comment(self.db, self.users["owner"], UUID(pending["id"]),
            GroupCommentCreate(submission_id=uuid4(), body="Photo discussion"))["current"]
        hidden = self.service.hide_comment(self.db, self.users["member"], UUID(comment["id"]),
            HideComment(submission_id=uuid4(), expected_version=comment["version"], action="hide"))["current"]
        self.assertTrue(hidden["hidden"])
        self.assertEqual(promoted["role"], "moderator")

    def test_private_photo_revocation_applies_to_original_uploader(self):
        from PIL import Image
        group_id, _ = self.group()
        self.invite(group_id)
        active = self.accept(group_id)
        image = BytesIO()
        Image.new("RGB", (2, 2), "white").save(image, "PNG")
        with patch.object(self.service.storage, "store_image") as store:
            photo = self.service.upload_media(self.db, self.users["member"], group_id, "post", uuid4(), image.getvalue())["current"]
            self.assertEqual(store.call_count, 1)
        post = self.service.create_post(self.db, self.users["member"], group_id,
            GroupPostCreate(submission_id=uuid4(), body="Private group photo", media_ids=[photo["id"]]))["current"]
        discussion = self.service.create_comment(self.db, self.users["owner"], UUID(post["id"]),
            GroupCommentCreate(submission_id=uuid4(), body="This photo", media_id=photo["id"]))["current"]
        self.assertEqual(discussion["media_id"], photo["id"])
        self.service.manage_member(self.db, self.users["owner"], group_id, self.users["member"].user_code,
            ManageMember(submission_id=uuid4(), expected_membership_version=active["version"], action="remove"))
        with patch.object(self.service.storage, "read_image") as read:
            self.assert_unavailable(lambda: self.service.media_bytes(self.db, self.users["member"], UUID(photo["id"])))
            read.assert_not_called()

    def test_joining_group_never_grants_existing_project_access(self):
        from models.property_network import NetworkProperty, NetworkProject, NetworkProjectMember
        from services.property_network import project_for
        owner = self.users["owner"]
        property = NetworkProperty(id=uuid4(), custodian_id=owner.id, current_custodian_id=owner.id, submission_id=uuid4(),
            title="Unrelated private property", location="Synthetic area", address="", notes="", property_class="house_villa",
            relationship="owner", status="active", version=1, creation_sha256="a" * 64, authority_acknowledged=True)
        self.db.add(property)
        self.db.flush()
        project = NetworkProject(id=uuid4(), property_id=property.id, owner_id=owner.id, created_by_id=owner.id,
            submission_id=uuid4(), creation_sha256="b" * 64, title="Unrelated private project", summary="", property_class="house_villa",
            service_category_ids=["pool_service"], status="open", version=1)
        self.db.add(project)
        self.db.commit()
        group_id, _ = self.group(purpose="property_project")
        self.assert_unavailable(lambda: project_for(self.db, self.users["member"], project.id))
        self.invite(group_id)
        self.accept(group_id)
        self.assert_unavailable(lambda: project_for(self.db, self.users["member"], project.id))
        self.assertEqual(self.db.query(NetworkProjectMember).filter_by(project_id=project.id).count(), 0)

    def test_archive_and_stale_version_do_not_resurrect_membership(self):
        group_id, current = self.group()
        self.invite(group_id)
        active = self.accept(group_id)
        body = MembershipAction(submission_id=uuid4(), expected_membership_version=active["version"], action="leave")
        left = self.service.own_membership_action(self.db, self.users["member"], group_id, body)
        self.invite(group_id, version=left["current"]["version"])
        replay = self.service.own_membership_action(self.db, self.users["member"], group_id, body)
        self.assertEqual(replay["current"]["state"], "invited")
        self.service.archive(self.db, self.users["owner"], group_id,
            ArchiveGroup(submission_id=uuid4(), expected_version=current["version"], action="archive"))
        self.assert_unavailable(lambda: self.service.posts(self.db, self.users["other"], group_id, 1))
        with self.assertRaises(HTTPException) as closed:
            self.accept(group_id, version=replay["current"]["version"])
        self.assertEqual(closed.exception.status_code, 409)

    def test_new_group_tables_are_private_and_receipts_retained(self):
        from sqlalchemy import text
        group_id, _ = self.group()
        tables = ["network_groups", "network_group_memberships", "network_group_invite_links", "network_group_posts",
                  "network_group_comments", "network_group_media", "network_group_actions"]
        rows = self.db.execute(text("""SELECT c.relname,c.relrowsecurity,
            (SELECT count(*) FROM pg_policy p WHERE p.polrelid=c.oid),
            (SELECT count(*) FROM aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
             LEFT JOIN pg_roles r ON r.oid=a.grantee WHERE coalesce(r.rolname,'PUBLIC') IN ('PUBLIC','anon','authenticated','service_role'))
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=ANY(:tables) ORDER BY c.relname"""), {"tables": tables}).all()
        self.assertEqual(rows, [(name, True, 0, 0) for name in sorted(tables)])
        from sqlalchemy.exc import DBAPIError
        with self.assertRaises(DBAPIError):
            self.db.execute(text("DELETE FROM network_groups WHERE id=:id"), {"id": group_id})
            self.db.flush()
        self.db.rollback()

    def test_real_route_auth_cookie_origin_and_no_store(self):
        import jwt
        from config import settings
        from fastapi.testclient import TestClient
        from main import app
        group_id, _ = self.group()
        client = TestClient(app)
        try:
            response = client.get("/property-network/groups")
            self.assertIn(response.status_code, (401, 403))
            self.assertEqual(response.headers.get("cache-control"), "no-store")
            token = jwt.encode({"sub": str(self.users["member"].id)}, settings.jwt_secret, algorithm="HS256")
            client.cookies.set("ildr_token", token)
            denied = client.get(f"/property-network/groups/{group_id}")
            self.assertEqual(denied.status_code, 404)
            self.assertNotIn("Shared owner alias", denied.text)
            body = {"submission_id": str(uuid4()), "name": "Route group", "owner_alias": "Route alias"}
            missing = client.post("/property-network/groups", json=body)
            self.assertEqual(missing.status_code, 403)
            wrong = client.post("/property-network/groups", headers={"Origin": "https://untrusted.example.com"}, json=body)
            self.assertEqual(wrong.status_code, 403)
            created = client.post("/property-network/groups", headers={"Origin": settings.origins_list[0]}, json=body)
            self.assertEqual(created.status_code, 201)
            self.assertEqual(created.headers.get("cache-control"), "no-store")
            self.assertEqual(created.json()["current"]["visibility"], "private")
        finally:
            client.close()

    def test_pending_members_cannot_read_private_group_images(self):
        from PIL import Image
        image = BytesIO()
        Image.new("RGB", (2, 2), "white").save(image, "PNG")
        group_id, current = self.group()
        with patch.object(self.service.storage, "store_image"):
            photo = self.service.upload_media(self.db, self.users["owner"], group_id, "group_image", uuid4(), image.getvalue())["current"]
        current = self.edit(group_id, current, image_media_id=photo["id"])
        self.invite(group_id)
        pending = self.service.group_view(self.db, self.users["member"], self.service.group_for(self.db, self.users["member"], group_id))
        self.assertIsNone(pending["image"])
        with patch.object(self.service.storage, "read_image", return_value=b"synthetic image") as read:
            self.assert_unavailable(lambda: self.service.media_bytes(self.db, self.users["member"], UUID(photo["id"])))
            read.assert_not_called()
            self.accept(group_id)
            self.assertEqual(self.service.media_bytes(self.db, self.users["member"], UUID(photo["id"])), b"synthetic image")

        network_id, network = self.group(visibility="network")
        with patch.object(self.service.storage, "store_image"):
            public_image = self.service.upload_media(self.db, self.users["owner"], network_id, "group_image", uuid4(), image.getvalue())["current"]
        network = self.edit(network_id, network, image_media_id=public_image["id"])
        self.service.own_membership_action(self.db, self.users["other"], network_id, MembershipAction(
            submission_id=uuid4(), expected_membership_version=0, action="request", member_alias="Pending requester"))
        self.assertIsNotNone(self.service.group_view(self.db, self.users["other"], self.service.group_for(self.db, self.users["other"], network_id))["image"])
        self.edit(network_id, network, visibility="private")
        hidden = self.service.group_view(self.db, self.users["other"], self.service.group_for(self.db, self.users["other"], network_id))
        self.assertEqual(hidden["membership"]["state"], "requested")
        self.assertIsNone(hidden["image"])
        with patch.object(self.service.storage, "read_image") as read:
            self.assert_unavailable(lambda: self.service.media_bytes(self.db, self.users["other"], UUID(public_image["id"])))
            read.assert_not_called()

    def test_moderator_demotion_conceals_hidden_comment_receipt(self):
        group_id, _ = self.group()
        self.invite(group_id)
        member = self.accept(group_id)
        moderator = self.service.manage_member(self.db, self.users["owner"], group_id, self.users["member"].user_code,
            ManageMember(submission_id=uuid4(), expected_membership_version=member["version"], action="set_role", role="moderator"))["current"]
        self.invite(group_id, actor="other")
        self.accept(group_id, actor="other")
        post = self.service.create_post(self.db, self.users["owner"], group_id,
            GroupPostCreate(submission_id=uuid4(), body="Shared parent"))["current"]
        comment = self.service.create_comment(self.db, self.users["other"], UUID(post["id"]),
            GroupCommentCreate(submission_id=uuid4(), body="Hidden comment sentinel"))["current"]
        body = HideComment(submission_id=uuid4(), expected_version=comment["version"], action="hide")
        self.service.hide_comment(self.db, self.users["member"], UUID(comment["id"]), body)
        self.service.manage_member(self.db, self.users["owner"], group_id, self.users["member"].user_code,
            ManageMember(submission_id=uuid4(), expected_membership_version=moderator["version"], action="set_role", role="member"))
        receipt = self.service.own_submission(self.db, self.users["member"], body.submission_id)
        self.assertIsNone(receipt["current"])
        from models.property_network import NetworkBlock
        self.db.add(NetworkBlock(blocker_id=self.users["other"].id, blocked_id=self.users["member"].id))
        self.db.commit()
        self.assertIsNone(self.service.own_submission(self.db, self.users["member"], body.submission_id)["current"])
        self.assertEqual(self.service.comments(self.db, self.users["member"], UUID(post["id"]), 1)["total"], 0)

    def test_cached_parent_is_refreshed_after_another_session_hides_it(self):
        from models.social_groups import NetworkGroupPost, NetworkGroupComment
        from models.user import User
        group_id, _ = self.group()
        self.invite(group_id)
        self.accept(group_id)
        post = self.service.create_post(self.db, self.users["owner"], group_id,
            GroupPostCreate(submission_id=uuid4(), body="Parent before moderation"))["current"]
        identity = UUID(post["id"])
        cached = self.db.get(NetworkGroupPost, identity)
        self.assertEqual(cached.status, "visible")
        with self.Session() as other:
            owner = other.get(User, self.users["owner"].id)
            self.service.moderate_post(other, owner, identity,
                ModeratePost(submission_id=uuid4(), expected_version=post["version"], action="hide"))
        # The request has the same cached hint it could have loaded before
        # waiting behind the moderator's group lock. The parent must refresh.
        self.assertEqual(cached.status, "visible")
        self.assert_unavailable(lambda: self.service.create_comment(self.db, self.users["member"], identity,
            GroupCommentCreate(submission_id=uuid4(), body="Must not reach hidden parent")))
        self.assertEqual(self.db.query(NetworkGroupComment).filter_by(post_id=identity).count(), 0)

    def test_closed_groups_allow_withdrawal_but_no_new_activity(self):
        for closure in ("archived", "owner_suspended"):
            group_id, current = self.group(visibility="network")
            self.invite(group_id)
            member = self.accept(group_id)
            invitation = self.invite(group_id, actor="other")
            request = self.service.own_membership_action(self.db, self.users["third"], group_id, MembershipAction(
                submission_id=uuid4(), expected_membership_version=0, action="request", member_alias="Waiting requester"))["current"]
            if closure == "archived":
                self.service.archive(self.db, self.users["owner"], group_id,
                    ArchiveGroup(submission_id=uuid4(), expected_version=current["version"], action="archive"))
            else:
                self.users["owner"].status = "suspended"
                self.db.commit()
            for call in (
                lambda: self.accept(group_id, actor="other", version=invitation["version"]),
                lambda: self.service.create_post(self.db, self.users["member"], group_id,
                    GroupPostCreate(submission_id=uuid4(), body="Closed activity")),
                lambda: self.service.own_membership_action(self.db, self.users["platform"], group_id, MembershipAction(
                    submission_id=uuid4(), expected_membership_version=0, action="request", member_alias="New requester")),
            ):
                with self.assertRaises(HTTPException) as denied:
                    call()
                self.assertEqual(denied.exception.status_code, 409)
                self.db.rollback()
            for actor, action, version in (("member", "leave", member["version"]),
                                            ("other", "decline_invite", invitation["version"]),
                                            ("third", "cancel", request["version"])):
                result = self.service.own_membership_action(self.db, self.users[actor], group_id, MembershipAction(
                    submission_id=uuid4(), expected_membership_version=version, action=action))["current"]
                self.assertEqual(result["state"], {"leave": "left", "decline_invite": "declined", "cancel": "cancelled"}[action])
            if closure == "owner_suspended":
                self.users["owner"].status = "active"
                self.db.commit()

    def test_closed_group_managers_can_revoke_private_access_without_new_grants(self):
        from PIL import Image
        from models.user import User
        waiting = User(id=uuid4(), email=f"groups-waiting-{self.marker}@example.com", password_hash="synthetic-not-used",
                       role="buyer", status="active", email_verified=True, display_name="Synthetic waiting member")
        self.db.add(waiting)
        self.db.commit()
        image = BytesIO()
        Image.new("RGB", (2, 2), "white").save(image, "PNG")
        for closure in ("archived", "owner_suspended"):
            group_id, current = self.group(visibility="network")
            self.invite(group_id)
            administrator = self.accept(group_id)
            administrator = self.service.manage_member(self.db, self.users["owner"], group_id, self.users["member"].user_code,
                ManageMember(submission_id=uuid4(), expected_membership_version=administrator["version"], action="set_role", role="admin"))["current"]
            self.invite(group_id, actor="other")
            moderator = self.accept(group_id, actor="other")
            moderator = self.service.manage_member(self.db, self.users["owner"], group_id, self.users["other"].user_code,
                ManageMember(submission_id=uuid4(), expected_membership_version=moderator["version"], action="set_role", role="moderator"))["current"]
            self.invite(group_id, actor="third")
            regular = self.accept(group_id, actor="third")
            pending = self.invite(group_id, actor="platform")
            requested = self.service.own_membership_action(self.db, waiting, group_id, MembershipAction(
                submission_id=uuid4(), expected_membership_version=0, action="request", member_alias="Waiting alias"))["current"]
            current = self.edit(group_id, current, visibility="private")
            token, link = self.link(group_id)
            unused_token, unused_link = self.link(group_id)
            with patch.object(self.service.storage, "store_image"):
                photo = self.service.upload_media(self.db, self.users["third"], group_id, "post", uuid4(), image.getvalue())["current"]
            self.service.create_post(self.db, self.users["third"], group_id,
                GroupPostCreate(submission_id=uuid4(), body="Retained private photo", media_ids=[photo["id"]]))
            if closure == "archived":
                self.service.archive(self.db, self.users["owner"], group_id,
                    ArchiveGroup(submission_id=uuid4(), expected_version=current["version"], action="archive"))
            else:
                self.users["owner"].status = "suspended"
                self.db.commit()
            admin = self.users["member"]
            capabilities = self.service.group_view(self.db, admin, self.service.group_for(self.db, admin, group_id))
            self.assertTrue(capabilities["can_revoke_access"] and capabilities["can_revoke_links"])
            self.assertFalse(capabilities["can_manage"] or capabilities["can_post"] or capabilities["can_publish"])
            removed = self.service.manage_member(self.db, self.users["other"], group_id, self.users["third"].user_code,
                ManageMember(submission_id=uuid4(), expected_membership_version=regular["version"], action="remove"))["current"]
            with patch.object(self.service.storage, "read_image") as read:
                self.assert_unavailable(lambda: self.service.media_bytes(self.db, self.users["third"], UUID(photo["id"])))
                read.assert_not_called()
            blocked = self.service.manage_member(self.db, self.users["other"], group_id, self.users["third"].user_code,
                ManageMember(submission_id=uuid4(), expected_membership_version=removed["version"], action="block"))["current"]
            demoted = self.service.manage_member(self.db, admin, group_id, self.users["other"].user_code,
                ManageMember(submission_id=uuid4(), expected_membership_version=moderator["version"], action="set_role", role="member"))["current"]
            self.assertEqual(demoted["role"], "member")
            self.assertFalse(self.service.group_view(self.db, self.users["other"],
                self.service.group_for(self.db, self.users["other"], group_id))["can_revoke_access"])
            self.assert_unavailable(lambda: self.service.manage_member(self.db, self.users["other"], group_id, self.users["third"].user_code,
                ManageMember(submission_id=uuid4(), expected_membership_version=blocked["version"], action="remove")))
            revoked = self.service.manage_member(self.db, admin, group_id, self.users["platform"].user_code,
                ManageMember(submission_id=uuid4(), expected_membership_version=pending["version"], action="revoke_invite"))["current"]
            self.assertEqual(revoked["state"], "cancelled")
            self.service.revoke_link(self.db, admin, group_id, UUID(link["id"]),
                RevokeInviteLink(submission_id=uuid4(), expected_version=link["version"]))
            self.assert_unavailable(lambda: self.service.resolve_link(self.db, waiting, ResolveInvite(token=token)))
            self.assert_unavailable(lambda: self.service.accept_link(self.db, waiting,
                AcceptInvite(token=unused_token, submission_id=uuid4(), member_alias="New link member")))
            for body, code in (
                (ManageMember(submission_id=uuid4(), expected_membership_version=requested["version"], action="approve"), waiting.user_code),
                (ManageMember(submission_id=uuid4(), expected_membership_version=demoted["version"], action="set_role", role="moderator"), self.users["other"].user_code),
                (ManageMember(submission_id=uuid4(), expected_membership_version=requested["version"], action="invite"), waiting.user_code),
            ):
                with self.assertRaises(HTTPException) as denied:
                    self.service.manage_member(self.db, admin, group_id, code, body)
                self.assertEqual(denied.exception.status_code, 409)
                self.db.rollback()
            self.assert_unavailable(lambda: self.service.manage_member(self.db, admin, group_id, self.users["owner"].user_code,
                ManageMember(submission_id=uuid4(), expected_membership_version=0, action="remove")))
            from models.social_groups import NetworkGroupInviteLink
            self.assertEqual(self.db.get(NetworkGroupInviteLink, UUID(unused_link["id"])).use_count, 0)
            if closure == "owner_suspended":
                self.users["owner"].status = "active"
                self.db.commit()

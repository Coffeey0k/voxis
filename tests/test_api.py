from fastapi.testclient import TestClient
import server


def register(client, username):
    response = client.post("/api/register", json={
        "username": username,
        "password": "correct-horse-battery",
        "device_id": f"device-{username}",
        "platform": "test",
    })
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_message_lifecycle_and_privacy(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB", tmp_path / "test.sqlite3")
    monkeypatch.setattr(server, "UPLOADS", tmp_path / "uploads")
    server.UPLOADS.mkdir(parents=True, exist_ok=True)

    with TestClient(server.app) as client:
        alice = register(client, "alice_test")
        bob = register(client, "bob_test")
        ah = {"Authorization": f"Bearer {alice}"}
        bh = {"Authorization": f"Bearer {bob}"}

        sent = client.post("/api/messages", headers=ah, json={
            "recipient": "bob_test", "body": "hello from tests"
        })
        assert sent.status_code == 200, sent.text
        message_id = sent.json()["message"]["id"]

        history = client.get("/api/messages/alice_test", headers=bh)
        assert history.status_code == 200
        assert history.json()["messages"][0]["body"] == "hello from tests"

        search = client.get("/api/search/messages?q=hello", headers=bh)
        assert search.status_code == 200
        assert len(search.json()["messages"]) == 1

        reaction = client.post(
            f"/api/messages/{message_id}/reactions",
            headers=bh,
            json={"reaction": "👍"},
        )
        assert reaction.status_code == 200
        assert reaction.json()["reactions"][0]["reaction"] == "👍"

        read = client.post(f"/api/messages/{message_id}/read", headers=bh)
        assert read.status_code == 200
        assert read.json()["read_at"]

        edit = client.patch(
            f"/api/messages/{message_id}",
            headers=ah,
            json={"body": "edited text"},
        )
        assert edit.status_code == 200
        assert edit.json()["message"]["body"] == "edited text"

        pin = client.post(f"/api/messages/{message_id}/pin", headers=bh)
        assert pin.status_code == 200
        assert client.get("/api/pinned-messages", headers=bh).json()["messages"]

        block = client.post("/api/blocks", headers=bh, json={"username": "alice_test"})
        assert block.status_code == 200
        denied = client.post("/api/messages", headers=ah, json={
            "recipient": "bob_test", "body": "this should be blocked"
        })
        assert denied.status_code == 403

        deleted = client.delete(f"/api/messages/{message_id}", headers=ah)
        assert deleted.status_code == 200
        assert client.get(f"/api/messages/{message_id}/reactions", headers=bh).status_code == 404



def test_push_delivery_is_best_effort_and_contains_no_message_body(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB", tmp_path / "push.sqlite3")
    monkeypatch.setattr(server, "UPLOADS", tmp_path / "uploads")
    server.UPLOADS.mkdir(parents=True, exist_ok=True)
    calls = []
    monkeypatch.setattr(server, "push_to_user", lambda username, kind, data: calls.append((username, kind, data)) or {"sent": 1})

    with TestClient(server.app) as client:
        alice = register(client, "push_alice")
        bob = register(client, "push_bob")
        ah = {"Authorization": f"Bearer {alice}"}
        sent = client.post("/api/messages", headers=ah, json={
            "recipient": "push_bob", "body": "private text must not go to push"
        })
        assert sent.status_code == 200, sent.text
        assert calls == [("push_bob", "message", {
            "message_id": sent.json()["message"]["id"], "sender": "push_alice"
        })]
        assert "private text" not in repr(calls)


def test_web_client_is_served_from_root(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB", tmp_path / "web.sqlite3")
    monkeypatch.setattr(server, "UPLOADS", tmp_path / "uploads")
    server.UPLOADS.mkdir(parents=True, exist_ok=True)
    with TestClient(server.app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Voxis" in response.text
        assert 'id="chatList"' in response.text
        assert 'id="authForm"' in response.text
        assert 'id="themeBtn"' in response.text
        assert 'id="profileForm"' in response.text
        assert 'id="settingsOverlay"' in response.text
        assert 'id="groupInfoOverlay"' in response.text
        assert 'id="prefNotify"' in response.text
        assert 'id="fileInput"' in response.text
        assert 'function connectWS()' in response.text
        assert 'data-act="edit"' in response.text
        assert 'data-act="delete"' in response.text
        assert "body.dark" in response.text
        assert 'rel="manifest"' in response.text
        manifest = client.get("/manifest.webmanifest")
        assert manifest.status_code == 200
        assert manifest.json()["display"] == "standalone"
        assert client.get("/icon.svg").headers["content-type"].startswith("image/svg+xml")
        worker = client.get("/sw.js")
        assert worker.status_code == 200
        assert "voxis-shell-v1" in worker.text



def test_group_management_and_device_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB", tmp_path / "groups.sqlite3")
    monkeypatch.setattr(server, "UPLOADS", tmp_path / "uploads")
    server.UPLOADS.mkdir(parents=True, exist_ok=True)

    with TestClient(server.app) as client:
        alice = register(client, "group_owner")
        bob = register(client, "group_member")
        ah = {"Authorization": f"Bearer {alice}"}
        bh = {"Authorization": f"Bearer {bob}"}

        devices = client.get("/api/devices", headers=ah)
        assert devices.status_code == 200
        assert any(x["id"] == "device-group_owner" for x in devices.json()["devices"])

        created = client.post("/api/groups", headers=ah, json={
            "name": "Test room", "members": ["group_member"]
        })
        assert created.status_code == 200, created.text
        gid = created.json()["id"]
        assert len(client.get(f"/api/groups/{gid}/members", headers=bh).json()["members"]) == 2

        renamed = client.patch(f"/api/groups/{gid}", headers=ah, json={"name": "Renamed room"})
        assert renamed.status_code == 200, renamed.text
        assert any(g["name"] == "Renamed room" for g in client.get("/api/groups", headers=ah).json()["groups"])

        denied = client.patch(f"/api/groups/{gid}", headers=bh, json={"name": "Nope"})
        assert denied.status_code == 403

        left = client.delete(f"/api/groups/{gid}", headers=bh)
        assert left.status_code == 200
        assert client.get(f"/api/groups/{gid}/members", headers=bh).status_code == 403

        deleted = client.delete(f"/api/groups/{gid}", headers=ah)
        assert deleted.status_code == 200
        assert client.get(f"/api/groups/{gid}/members", headers=ah).status_code == 404


def test_group_invite_lifecycle_and_owner_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB", tmp_path / "invites.sqlite3")
    monkeypatch.setattr(server, "UPLOADS", tmp_path / "uploads")
    server.UPLOADS.mkdir(parents=True, exist_ok=True)
    with TestClient(server.app) as client:
        owner = register(client, "invite_owner")
        guest = register(client, "invite_guest")
        oh = {"Authorization": f"Bearer {owner}"}
        gh = {"Authorization": f"Bearer {guest}"}
        created = client.post("/api/groups", headers=oh, json={"name": "Invite room", "members": []})
        assert created.status_code == 200, created.text
        gid = created.json()["id"]

        denied = client.post(f"/api/groups/{gid}/invites", headers=gh, json={})
        assert denied.status_code == 403

        made = client.post(f"/api/groups/{gid}/invites", headers=oh, json={"expires_in_hours": 24, "max_uses": 1})
        assert made.status_code == 200, made.text
        token = made.json()["token"]
        assert len(token) >= 20

        joined = client.post("/api/groups/join-by-invite", headers=gh, json={"token": token})
        assert joined.status_code == 200, joined.text
        assert joined.json()["group_id"] == gid
        assert any(m["username"] == "invite_guest" for m in client.get(f"/api/groups/{gid}/members", headers=gh).json()["members"])

        exhausted = client.post("/api/groups/join-by-invite", headers=oh, json={"token": token})
        assert exhausted.status_code == 404

        made2 = client.post(f"/api/groups/{gid}/invites", headers=oh, json={})
        assert made2.status_code == 200, made2.text
        invite_id = made2.json()["id"]
        revoked = client.delete(f"/api/groups/{gid}/invites/{invite_id}", headers=oh)
        assert revoked.status_code == 200
        rejected = client.post("/api/groups/join-by-invite", headers=oh, json={"token": made2.json()["token"]})
        assert rejected.status_code == 404

def test_group_moderator_permissions_are_limited(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB", tmp_path / "moderators.sqlite3")
    monkeypatch.setattr(server, "UPLOADS", tmp_path / "uploads")
    server.UPLOADS.mkdir(parents=True, exist_ok=True)
    with TestClient(server.app) as client:
        owner = register(client, "mod_owner")
        moderator = register(client, "mod_user")
        member = register(client, "mod_member")
        other = register(client, "mod_other")
        oh = {"Authorization": f"Bearer {owner}"}
        mh = {"Authorization": f"Bearer {moderator}"}
        created = client.post("/api/groups", headers=oh, json={"name": "Moderated", "members": ["mod_user", "mod_member"]})
        gid = created.json()["id"]
        assert client.patch(f"/api/groups/{gid}/members/mod_user", headers=oh, json={"role": "moderator"}).status_code == 200
        added = client.post(f"/api/groups/{gid}/members", headers=mh, json={"name": "ignored", "members": ["mod_other"]})
        assert added.status_code == 200, added.text
        removed = client.delete(f"/api/groups/{gid}/members/mod_member", headers=mh)
        assert removed.status_code == 200, removed.text
        protected = client.delete(f"/api/groups/{gid}/members/mod_user", headers=mh)
        assert protected.status_code == 403
        denied = client.patch(f"/api/groups/{gid}/members/mod_member", headers=mh, json={"role": "moderator"})
        assert denied.status_code == 403


def test_poll_permissions_and_single_vote(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB", tmp_path / "polls.sqlite3")
    monkeypatch.setattr(server, "UPLOADS", tmp_path / "uploads")
    server.UPLOADS.mkdir(parents=True, exist_ok=True)
    with TestClient(server.app) as client:
        owner = register(client, "poll_owner")
        guest = register(client, "poll_guest")
        outsider = register(client, "poll_outsider")
        oh = {"Authorization": f"Bearer {owner}"}
        gh = {"Authorization": f"Bearer {guest}"}
        xh = {"Authorization": f"Bearer {outsider}"}
        group = client.post("/api/groups", headers=oh, json={"name": "Poll room", "members": ["poll_guest"]})
        assert group.status_code == 200, group.text
        gid = group.json()["id"]
        created = client.post("/api/polls", headers=oh, json={
            "group_id": gid, "question": "Choose one", "options": ["A", "B", "C"]
        })
        assert created.status_code == 200, created.text
        poll = created.json()["poll"]
        assert poll["total_votes"] == 0 and len(poll["options"]) == 3
        first = client.post(f"/api/polls/{poll['id']}/vote", headers=gh, json={"option_id": poll["options"][0]["id"]})
        assert first.status_code == 200, first.text
        second = client.post(f"/api/polls/{poll['id']}/vote", headers=gh, json={"option_id": poll["options"][1]["id"]})
        assert second.status_code == 200, second.text
        assert second.json()["poll"]["total_votes"] == 1
        assert next(o for o in second.json()["poll"]["options"] if o["selected"])["label"] == "B"
        assert client.get(f"/api/polls/{poll['id']}", headers=xh).status_code == 403
        duplicate = client.post("/api/polls", headers=oh, json={"group_id": gid, "question": "dup", "options": ["A", "a"]})
        assert duplicate.status_code == 422

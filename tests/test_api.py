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

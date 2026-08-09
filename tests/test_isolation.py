from fastapi.testclient import TestClient

from concept_branch.app import create_app
from concept_branch.config import ConfigStore
from concept_branch.db import Database
from concept_branch.model import ProviderError


class FakeModel:
    async def complete(self, config, messages):
        if messages[-1]["content"] == "只回复 OK":
            return "OK"
        return "这里的梯度下降是一个优化方法。\n\n```python\nloss.backward()\n```"

    async def discover_models(self, config):
        return ["discovered-model", config.model]


def make_two_users(tmp_path):
    base = tmp_path / "config"
    app = create_app(Database(tmp_path / "db.sqlite3"), ConfigStore(base), FakeModel())
    alice, bob = TestClient(app), TestClient(app)
    alice_created = alice.post("/api/auth/register", json={"username": "alice", "password": "alice-pass-123"})
    bob_created = bob.post("/api/auth/register", json={"username": "bob", "password": "bob-pass-12345"})
    assert alice_created.status_code == 201 and bob_created.status_code == 201
    alice_store = ConfigStore(base / "users" / alice_created.json()["user"]["id"])
    alice_store.save(__import__("concept_branch.config", fromlist=["ModelConfig"]).ModelConfig("http://mock/v1", "chat_completions", "mock-model", "test-key"))
    return alice, bob


def test_users_see_only_their_own_discussions(tmp_path):
    alice, bob = make_two_users(tmp_path)
    created = alice.post("/api/discussions", json={"title": "艾丽丝的讨论"}).json()
    discussion_id = created["discussion"]["id"]
    root_id = created["root_node"]["id"]
    alice.post(f"/api/nodes/{root_id}/messages", json={"content": "解释训练过程"})

    assert bob.get("/api/discussions").json() == []
    assert alice.get("/api/discussions").json()[0]["id"] == discussion_id
    assert alice.get(f"/api/discussions/{discussion_id}").json()["title"] == "艾丽丝的讨论"

    assert bob.get(f"/api/discussions/{discussion_id}").status_code == 404
    assert bob.get(f"/api/discussions/{discussion_id}/nodes").status_code == 404
    assert bob.patch(f"/api/discussions/{discussion_id}", json={"title": "hack"}).status_code == 404
    assert bob.delete(f"/api/discussions/{discussion_id}").status_code == 404
    assert alice.patch(f"/api/discussions/{discussion_id}", json={"title": "艾丽丝改"}).status_code == 200
    assert alice.get("/api/search", params={"q": "解释训练"}).json()["results"]
    assert bob.get("/api/search", params={"q": "解释训练"}).json()["results"] == []


def test_foreign_nodes_and_messages_are_invisible(tmp_path):
    alice, bob = make_two_users(tmp_path)
    created = alice.post("/api/discussions", json={"title": "私密"}).json()
    root_id = created["root_node"]["id"]
    assistant = alice.post(f"/api/nodes/{root_id}/messages", json={"content": "解释训练过程"}).json()["assistant"]
    child = alice.post(f"/api/nodes/{root_id}/expand", json={
        "source_message_id": assistant["id"],
        "selected_text": "梯度下降",
        "custom_question": "继续讲",
    }).json()["node"]

    assert bob.get(f"/api/nodes/{root_id}/messages").status_code == 404
    assert bob.get(f"/api/nodes/{root_id}/attachments").status_code == 404
    assert bob.post(f"/api/nodes/{root_id}/attachments", files={"file": ("private.txt", b"private", "text/plain")}).status_code == 404
    assert bob.post(f"/api/nodes/{root_id}/messages", json={"content": "入侵"}).status_code == 404
    assert bob.post(f"/api/nodes/{root_id}/expand", json={
        "source_message_id": assistant["id"],
        "selected_text": "梯度下降",
    }).status_code == 404
    assert bob.patch(f"/api/discussions/{created['discussion']['id']}/nodes/{child['id']}", json={"title": "hack"}).status_code == 404
    assert bob.delete(f"/api/discussions/{created['discussion']['id']}/nodes/{child['id']}").status_code == 404

    assert alice.get(f"/api/nodes/{root_id}/messages").status_code == 200
    assert len(alice.get(f"/api/discussions/{created['discussion']['id']}/nodes").json()) == 2


def test_foreign_ids_cannot_be_used_as_parent(tmp_path):
    alice, bob = make_two_users(tmp_path)
    created = alice.post("/api/discussions", json={"title": "a"}).json()
    root_id = created["root_node"]["id"]
    bob_created = bob.post("/api/discussions", json={"title": "b"}).json()
    bob_root = bob_created["root_node"]["id"]

    response = bob.post(f"/api/discussions/{bob_created['discussion']['id']}/nodes", json={"title": "越权", "parent_id": root_id})
    assert response.status_code == 400
    response = bob.post(f"/api/discussions/{created['discussion']['id']}/nodes", json={"title": "越权", "parent_id": bob_root})
    assert response.status_code == 400
    response = bob.post(f"/api/discussions/{created['discussion']['id']}/nodes", json={"title": "越权", "parent_id": "不存在的节点"})
    assert response.status_code == 400


def test_providers_are_per_user_and_keys_isolated(tmp_path):
    alice, bob = make_two_users(tmp_path)
    assert len(alice.get("/api/providers").json()["providers"]) == 1
    assert bob.get("/api/providers").json()["providers"] == []

    alice_created = alice.post("/api/providers", json={
        "name": "爱丽丝的中转",
        "base_url": "http://alice.example/v1",
        "protocol": "chat_completions",
        "model": "alice-model",
        "api_key": "alice-secret-never-echo",
    })
    assert alice_created.status_code == 201
    assert "alice-secret-never-echo" not in alice_created.text
    assert bob.get("/api/providers").json()["providers"] == []

    bob_created = bob.post("/api/providers", json={
        "name": "鲍勃的中转",
        "base_url": "http://bob.example/v1",
        "protocol": "chat_completions",
        "model": "bob-model",
        "api_key": "bob-secret-never-echo",
    })
    assert bob_created.status_code == 201
    assert "bob-secret-never-echo" not in alice.get("/api/providers").text
    assert "alice-secret-never-echo" not in bob.get("/api/providers").text

    alice_ids = [p["id"] for p in alice.get("/api/providers").json()["providers"]]
    bob_ids = [p["id"] for p in bob.get("/api/providers").json()["providers"]]
    assert set(alice_ids).isdisjoint(bob_ids)

    alice_switched = alice.put("/api/active-model", json={"provider_id": alice_created.json()["provider"]["id"], "model": "alice-model"})
    assert alice_switched.status_code == 200
    assert alice.get("/api/active-model").json()["provider_id"] == alice_created.json()["provider"]["id"]
    assert bob.get("/api/active-model").json()["provider_id"] != alice_created.json()["provider"]["id"]

    bob_deleted = bob.delete(f"/api/providers/{alice_created.json()['provider']['id']}")
    assert bob_deleted.status_code == 404
    assert alice.get("/api/providers").json()["providers"][-1]["id"] == alice_created.json()["provider"]["id"]

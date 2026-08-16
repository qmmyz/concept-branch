import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from concept_branch.app import _resolve_frontend_file, create_app, selection_matches_source
from concept_branch.config import ConfigStore
from concept_branch.db import Database
from concept_branch.model import ProviderError


class FakeModel:
    def __init__(self):
        self.calls = []

    async def complete(self, config, messages):
        self.calls.append({"config": config, "messages": messages})
        if config.api_key == "bad-key":
            raise ProviderError("模型服务返回 HTTP 401")
        if messages[-1]["content"] == "只回复 OK":
            return "OK"
        return "这里的梯度下降是一个优化方法。\n\n```python\nloss.backward()\n```"

    async def discover_models(self, config):
        self.calls.append({"discovery": config})
        return ["discovered-model", config.model]


def make_client(tmp_path):
    fake = FakeModel()
    base = tmp_path / "config"
    app = create_app(Database(tmp_path / "db.sqlite3"), ConfigStore(base), fake)
    client = TestClient(app)
    created = client.post("/api/auth/register", json={"username": "tester", "password": "test-pass-1234"})
    assert created.status_code == 201, created.text
    user_dir = base / "users" / created.json()["user"]["id"]
    store = ConfigStore(user_dir)
    store.save(__import__("concept_branch.config", fromlist=["ModelConfig"]).ModelConfig("http://mock/v1", "chat_completions", "mock-model", "test-key"))
    return client, fake, store


def test_frontend_file_resolver_blocks_path_and_symlink_escape(tmp_path):
    frontend_root = tmp_path / "dist"
    assets = frontend_root / "assets"
    assets.mkdir(parents=True)
    safe_file = assets / "app.js"
    safe_file.write_text("console.log('safe')")
    outside_file = tmp_path / "private.txt"
    outside_file.write_text("private")

    assert _resolve_frontend_file(frontend_root, "assets/app.js") == safe_file
    assert _resolve_frontend_file(frontend_root, "missing.js") is None
    for path in ("../../private.txt", str(outside_file)):
        with pytest.raises(HTTPException) as error:
            _resolve_frontend_file(frontend_root, path)
        assert error.value.status_code == 404

    symlink = frontend_root / "outside-link.txt"
    symlink.symlink_to(outside_file)
    with pytest.raises(HTTPException) as error:
        _resolve_frontend_file(frontend_root, symlink.name)
    assert error.value.status_code == 404


def test_crud_chat_expand_persistence_and_parent_is_unchanged(tmp_path):
    client, fake, _ = make_client(tmp_path)
    created = client.post("/api/discussions", json={"title": "优化算法"}).json()
    discussion_id = created["discussion"]["id"]
    root_id = created["root_node"]["id"]
    exchange = client.post(f"/api/nodes/{root_id}/messages", json={"content": "解释训练过程"})
    assert exchange.status_code == 201
    assistant = exchange.json()["assistant"]
    parent_before = client.get(f"/api/nodes/{root_id}/messages").json()

    expanded = client.post(f"/api/nodes/{root_id}/expand", json={
        "source_message_id": assistant["id"],
        "selected_text": "梯度下降",
        "custom_question": "它为什么会收敛？",
    })
    assert expanded.status_code == 201, expanded.text
    child = expanded.json()["node"]
    assert child["parent_id"] == root_id
    assert child["context"]["related_user_question"] == "解释训练过程"
    assert child["context"]["selected_text"] == "梯度下降"
    assert child["context"]["source_answer"] == assistant["content"]
    assert expanded.json()["messages"][0]["content"] == "它为什么会收敛？"
    assert client.get(f"/api/nodes/{root_id}/messages").json() == parent_before

    grand_exchange = client.post(f"/api/nodes/{child['id']}/messages", json={"content": "再举一个例子"})
    assert grand_exchange.status_code == 201
    assert len(client.get(f"/api/nodes/{child['id']}/messages").json()) == 4
    child_assistant = grand_exchange.json()["assistant"]
    grandchild = client.post(f"/api/nodes/{child['id']}/expand", json={
        "source_message_id": child_assistant["id"],
        "selected_text": "梯度下降",
    })
    assert grandchild.status_code == 201
    assert grandchild.json()["node"]["parent_id"] == child["id"]
    assert grandchild.json()["node"]["context"]["ancestor_titles"] == ["主线", "梯度下降"]
    assert len(client.get(f"/api/discussions/{discussion_id}/nodes").json()) == 3

    renamed = client.patch(f"/api/discussions/{discussion_id}", json={"title": "已重命名"})
    assert renamed.json()["title"] == "已重命名"
    second = client.post("/api/discussions", json={"title": "另一个讨论"}).json()
    assert client.delete(f"/api/discussions/{discussion_id}").status_code == 204
    remaining = client.get("/api/discussions").json()
    assert [item["id"] for item in remaining] == [second["discussion"]["id"]]


def test_expansion_rejects_text_not_in_source(tmp_path):
    client, _, _ = make_client(tmp_path)
    created = client.post("/api/discussions", json={"title": "x"}).json()
    root = created["root_node"]["id"]
    assistant = client.post(f"/api/nodes/{root}/messages", json={"content": "q"}).json()["assistant"]
    response = client.post(f"/api/nodes/{root}/expand", json={"source_message_id": assistant["id"], "selected_text": "不存在"})
    assert response.status_code == 400


def test_expansion_accepts_text_selected_from_rendered_markdown():
    source = "这里的 **梯度下降**\n是一种 `优化方法`。"
    assert selection_matches_source("梯度下降 是一种 优化方法", source)
    assert not selection_matches_source("完全不存在的文字", source)


def test_expansion_from_user_background_material(tmp_path):
    client, _, _ = make_client(tmp_path)
    created = client.post("/api/discussions", json={"title": "材料讨论"}).json()
    root = created["root_node"]["id"]
    exchange = client.post(f"/api/nodes/{root}/messages", json={"content": "背景材料：蛋白质折叠依赖疏水作用。"}).json()
    response = client.post(f"/api/nodes/{root}/expand", json={
        "source_message_id": exchange["user"]["id"],
        "selected_text": "蛋白质折叠依赖疏水作用",
        "custom_question": "这句话是什么意思？",
    })
    assert response.status_code == 201, response.text
    context = response.json()["node"]["context"]
    assert context["source_role"] == "user"
    assert context["source_content"] == exchange["user"]["content"]
    assert context["source_answer"] == ""
    assert response.json()["messages"][0]["content"] == "这句话是什么意思？"


def test_chat_logs_safe_operation_metadata_without_prompt(tmp_path, caplog):
    client, _, _ = make_client(tmp_path)
    root = client.post("/api/discussions", json={"title": "logs"}).json()["root_node"]["id"]
    secret_prompt = "这段问题正文不应进入日志"
    with caplog.at_level(logging.INFO, logger="uvicorn.error.concept_branch.operations"):
        response = client.post(f"/api/nodes/{root}/messages", json={"content": secret_prompt})
    assert response.status_code == 201
    assert "chat_started" in caplog.text
    assert "chat_saved" in caplog.text
    assert "operation_id=" in caplog.text
    assert secret_prompt not in caplog.text


def test_attachment_upload_context_inheritance_and_delete(tmp_path):
    client, fake, _ = make_client(tmp_path)
    created = client.post("/api/discussions", json={"title": "文件对话"}).json()
    root = created["root_node"]["id"]
    uploaded = client.post(
        f"/api/nodes/{root}/attachments",
        files={"file": ("background.txt", "酶活性由底物浓度影响。".encode(), "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    metadata = uploaded.json()
    assert metadata["filename"] == "background.txt"
    assert "content" not in metadata and "extracted_text" not in metadata

    exchange = client.post(f"/api/nodes/{root}/messages", json={"content": "文件说了什么？"})
    assert exchange.status_code == 201
    assert "酶活性由底物浓度影响" in fake.calls[-1]["messages"][0]["content"]

    child = client.post(f"/api/nodes/{root}/expand", json={
        "source_message_id": exchange.json()["assistant"]["id"],
        "selected_text": "梯度下降",
    }).json()["node"]
    inherited = client.get(f"/api/nodes/{child['id']}/attachments").json()["attachments"]
    assert len(inherited) == 1 and inherited[0]["inherited"] is True
    assert client.delete(f"/api/nodes/{child['id']}/attachments/{metadata['id']}").status_code == 404
    assert client.delete(f"/api/nodes/{root}/attachments/{metadata['id']}").status_code == 204
    assert client.get(f"/api/nodes/{root}/attachments").json()["attachments"] == []


def test_attachment_rejects_unsupported_format(tmp_path):
    client, _, _ = make_client(tmp_path)
    root = client.post("/api/discussions", json={"title": "x"}).json()["root_node"]["id"]
    response = client.post(f"/api/nodes/{root}/attachments", files={"file": ("bad.exe", b"x", "application/octet-stream")})
    assert response.status_code == 422
    assert "仅支持" in response.json()["detail"]


def test_search_discussions_nodes_and_messages(tmp_path):
    client, _, _ = make_client(tmp_path)
    created = client.post("/api/discussions", json={"title": "蛋白质设计路线"}).json()
    root = created["root_node"]["id"]
    exchange = client.post(f"/api/nodes/{root}/messages", json={"content": "比较结构预测方案"})
    child = client.post(f"/api/nodes/{root}/expand", json={
        "source_message_id": exchange.json()["assistant"]["id"],
        "selected_text": "梯度下降",
        "custom_question": "解释优化方法",
    }).json()["node"]

    discussion_results = client.get("/api/search", params={"q": "蛋白质"}).json()["results"]
    assert any(item["kind"] == "discussion" and item["discussion_id"] == created["discussion"]["id"] for item in discussion_results)
    message_results = client.get("/api/search", params={"q": "结构预测"}).json()["results"]
    assert any(item["kind"] == "message" and item["node_id"] == root for item in message_results)
    node_results = client.get("/api/search", params={"q": "梯度下降"}).json()["results"]
    assert any(item["kind"] == "node" and item["node_id"] == child["id"] for item in node_results)
    assert client.get("/api/search", params={"q": ""}).status_code == 422


def test_settings_never_echo_key_and_failed_test_does_not_overwrite(tmp_path):
    client, _, store = make_client(tmp_path)
    good = client.get("/api/settings")
    assert good.status_code == 200
    assert "test-key" not in good.text
    assert set(good.json()) == {"base_url", "protocol", "model", "has_api_key"}

    failed = client.put("/api/settings", json={"base_url": "http://mock/v1", "protocol": "responses", "model": "new-model", "api_key": "bad-key"})
    assert failed.status_code == 502
    assert "bad-key" not in failed.text
    assert store.load().model == "mock-model"
    assert store.load().api_key == "test-key"

    saved = client.put("/api/settings", json={"base_url": "http://mock/v1", "protocol": "responses", "model": "new-model", "api_key": "new-secret"})
    assert saved.status_code == 200
    assert "new-secret" not in saved.text
    assert store.load().protocol == "responses"


def test_legacy_settings_create_a_usable_provider_after_registry_is_empty(tmp_path):
    client, fake, _ = make_client(tmp_path)
    initial_provider = client.get("/api/providers").json()["providers"][0]
    assert client.delete(f"/api/providers/{initial_provider['id']}").status_code == 204
    assert client.get("/api/providers").json()["providers"] == []

    saved = client.put("/api/settings", json={
        "base_url": "http://legacy.example/v1",
        "protocol": "responses",
        "model": "legacy-model",
        "api_key": "legacy-key",
    })
    assert saved.status_code == 200
    providers = client.get("/api/providers").json()
    assert len(providers["providers"]) == 1
    assert providers["active"]["provider_id"] == providers["providers"][0]["id"]
    assert providers["active"]["model"] == "legacy-model"

    root_id = client.post("/api/discussions", json={"title": "legacy settings"}).json()["root_node"]["id"]
    response = client.post(f"/api/nodes/{root_id}/messages", json={"content": "settings should work"})
    assert response.status_code == 201
    assert fake.calls[-1]["config"].model == "legacy-model"


def test_isolated_provider_registry_crud_switch_discovery_and_key_boundary(tmp_path):
    client, fake, _ = make_client(tmp_path)
    initial = client.get("/api/providers").json()
    assert len(initial["providers"]) == 1
    old_id = initial["providers"][0]["id"]

    created = client.post("/api/providers", json={
        "name": "design-only 中转",
        "base_url": "http://design.example/v1",
        "protocol": "chat_completions",
        "model": "design-model",
        "api_key": "design-secret-never-echo",
    })
    assert created.status_code == 201
    assert "design-secret-never-echo" not in created.text
    provider = created.json()["provider"]
    assert provider["has_api_key"] is True
    assert created.json()["active"]["provider_id"] == provider["id"]

    discovered = client.post(f"/api/providers/{provider['id']}/discover-models")
    assert discovered.status_code == 200
    assert "discovered-model" in discovered.json()["models"]
    assert "design-secret-never-echo" not in client.get("/api/providers").text
    switched = client.put("/api/active-model", json={"provider_id": provider["id"], "model": "discovered-model"})
    assert switched.json() == {"provider_id": provider["id"], "model": "discovered-model"}

    assert client.delete(f"/api/providers/{old_id}").status_code == 204
    remaining = client.get("/api/providers").json()
    assert [item["id"] for item in remaining["providers"]] == [provider["id"]]
    assert fake.calls[-1]["discovery"].model == "model-discovery"


def test_provider_update_rejects_empty_model_list(tmp_path):
    client, _, _ = make_client(tmp_path)
    provider_id = client.get("/api/providers").json()["providers"][0]["id"]
    response = client.patch(f"/api/providers/{provider_id}", json={"models": ["", "  "]})
    assert response.status_code == 422
    assert response.json()["detail"] == "模型列表不能为空"


def test_active_provider_is_cleared_when_it_becomes_invalid(tmp_path):
    client, fake, _ = make_client(tmp_path)
    provider = client.get("/api/providers").json()["providers"][0]
    provider_id = provider["id"]
    model = provider["models"][0]
    root_id = client.post("/api/discussions", json={"title": "provider state"}).json()["root_node"]["id"]

    for patch in (
        {"enabled": False},
        {"kind": "design"},
        {"models": ["replacement-model"]},
    ):
        if patch != {"enabled": False}:
            restore = {"enabled": True, "kind": "chat", "models": [model]}
            assert client.patch(f"/api/providers/{provider_id}", json=restore).status_code == 200
        assert client.put("/api/active-model", json={"provider_id": provider_id, "model": model}).status_code == 200
        calls_before = len(fake.calls)
        assert client.patch(f"/api/providers/{provider_id}", json=patch).status_code == 200
        assert client.get("/api/active-model").json() == {
            "provider_id": None,
            "provider_name": None,
            "model": None,
        }
        response = client.post(f"/api/nodes/{root_id}/messages", json={"content": "不得发送"})
        assert response.status_code == 409
        assert len(fake.calls) == calls_before


def test_deleting_active_provider_does_not_select_another_implicitly(tmp_path):
    client, _, _ = make_client(tmp_path)
    old_provider = client.get("/api/providers").json()["providers"][0]
    created = client.post("/api/providers", json={
        "name": "second chat provider",
        "base_url": "http://second.example/v1",
        "protocol": "chat_completions",
        "model": "second-model",
        "api_key": "second-key",
    }).json()["provider"]
    assert client.get("/api/active-model").json()["provider_id"] == created["id"]
    assert client.delete(f"/api/providers/{created['id']}").status_code == 204
    assert client.get("/api/active-model").json() == {
        "provider_id": None,
        "provider_name": None,
        "model": None,
    }
    assert [item["id"] for item in client.get("/api/providers").json()["providers"]] == [old_provider["id"]]


def test_frontend_catalog_keeps_classic_fallback(tmp_path):
    client, _, _ = make_client(tmp_path)
    catalog = client.get("/api/frontends").json()["frontends"]
    assert catalog[0]["id"] == "classic"
    assert isinstance(catalog[0]["available"], bool)
    assert len(catalog) == 1
    launcher = client.get("/launcher")
    assert launcher.status_code == 200
    assert '<script src="/launcher.js" defer></script>' in launcher.text
    assert "fetch('/api/frontends')" not in launcher.text
    launcher_script = client.get("/launcher.js")
    assert launcher_script.status_code == 200
    assert launcher_script.headers["content-type"].startswith("text/javascript")


def test_design_provider_cannot_be_selected_for_chat(tmp_path):
    client, _, _ = make_client(tmp_path)
    created = client.post("/api/providers", json={
        "name": "design worker",
        "base_url": "http://design.example/v1",
        "protocol": "chat_completions",
        "model": "design-model",
        "kind": "design",
        "api_key": "design-key",
    }).json()["provider"]
    assert client.put("/api/active-model", json={"provider_id": created["id"], "model": "design-model"}).status_code == 400


def test_provider_can_discover_model_when_model_name_is_unknown(tmp_path):
    client, _, _ = make_client(tmp_path)
    response = client.post("/api/providers", json={
        "name": "自动发现的中转站",
        "base_url": "http://provider.example/v1",
        "protocol": "chat_completions",
        "model": "",
        "kind": "design",
        "api_key": "discovery-key",
    })
    assert response.status_code == 201, response.text
    assert response.json()["provider"]["models"]
    assert response.json()["provider"]["models"][0] == "discovered-model"

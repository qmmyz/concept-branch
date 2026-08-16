from __future__ import annotations

import os
import logging
import re
import time
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .auth import (
    COOKIE_NAME,
    hash_password,
    new_session_token,
    session_expires,
    session_token_hash,
    verify_password,
)
from .attachments import AttachmentError, MAX_FILE_BYTES, build_attachment_context, extract_attachment
from .config import ConfigStore, ModelConfig, ProviderStore
from .db import Database
from .model import ModelClient, ProviderError


SYSTEM_PROMPT = "你是清晰、严谨的思考伙伴。回答语言跟随用户当前输入；需要时使用 Markdown、LaTeX 和代码块。"
logger = logging.getLogger("uvicorn.error.concept_branch.operations")


def configured_cors_origins() -> list[str]:
    configured = os.environ.get("CONCEPT_BRANCH_CORS_ORIGINS", "")
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


def secure_cookies_enabled() -> bool:
    return os.environ.get("CONCEPT_BRANCH_SECURE_COOKIES", "0").lower() in {"1", "true", "yes"}


def _resolve_frontend_file(frontend_root: Path, path: str) -> Path | None:
    root = frontend_root.resolve()
    requested = (root / path).resolve()
    try:
        requested.relative_to(root)
    except ValueError as error:
        raise HTTPException(404, "页面不存在") from error
    return requested if requested.is_file() else None


def canonical_selection(value: str) -> str:
    """Compare rendered selections with Markdown source without trusting arbitrary text."""
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", value)
    value = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[*_~`>#|]", "", value)
    return re.sub(r"\s+", " ", value).strip()


def selection_matches_source(selected_text: str, source: str) -> bool:
    if selected_text in source:
        return True
    selected = canonical_selection(selected_text)
    return bool(selected) and selected in canonical_selection(source)


class TitleBody(BaseModel):
    title: str = Field(min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return value.strip()


class MessageBody(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)

    @field_validator("content")
    @classmethod
    def clean_content(cls, value: str) -> str:
        return value.strip()


class NodeBody(TitleBody):
    parent_id: str


class ExpandBody(BaseModel):
    source_message_id: str
    selected_text: str = Field(min_length=1, max_length=20_000)
    custom_question: str | None = Field(default=None, max_length=20_000)

    @field_validator("selected_text")
    @classmethod
    def clean_selection(cls, value: str) -> str:
        return value.strip()


class SettingsBody(BaseModel):
    base_url: str = Field(min_length=1, max_length=2_000)
    protocol: Literal["chat_completions", "responses"]
    model: str = Field(min_length=1, max_length=300)
    api_key: str = Field(default="", max_length=20_000)

    def config(self, existing_key: str = "") -> ModelConfig:
        return ModelConfig(
            self.base_url.strip(), self.protocol, self.model.strip(), self.api_key or existing_key
        )


class ProviderBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=2_000)
    protocol: Literal["chat_completions", "responses"]
    model: str = Field(default="", max_length=300)
    api_key: str = Field(default="", max_length=20_000)
    models: list[str] = Field(default_factory=list, max_length=200)
    kind: Literal["chat", "design"] = "chat"


class ActiveModelBody(BaseModel):
    provider_id: str
    model: str = Field(min_length=1, max_length=300)


class AuthBody(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("username")
    @classmethod
    def clean_username(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.@\-]+", value):
            raise ValueError("用户名只能包含字母、数字、下划线、点、@ 和连字符")
        return value


class ProviderPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=1, max_length=2_000)
    protocol: Literal["chat_completions", "responses"] | None = None
    models: list[str] | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    api_key: str | None = Field(default=None, max_length=20_000)
    kind: Literal["chat", "design"] | None = None


def create_app(
    database: Database | None = None,
    config_store: ConfigStore | None = None,
    model_client: ModelClient | None = None,
    provider_store: ProviderStore | None = None,
) -> FastAPI:
    app = FastAPI(title="Concept Branch", version="0.1.0")
    app.state.db = database or Database()
    app.state.config = config_store or ConfigStore()
    app.state.providers = provider_store or ProviderStore(config_store.root if config_store else None)
    app.state.provider_base = Path(app.state.providers.root)
    app.state.models = model_client or ModelClient()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=configured_cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if secure_cookies_enabled():
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    def db() -> Database:
        return app.state.db

    def config_store() -> ConfigStore:
        return app.state.config

    def providers() -> ProviderStore:
        return app.state.providers

    def providers_for(user_id: str) -> ProviderStore:
        return ProviderStore(app.state.provider_base / "users" / user_id)

    def config_store_for(user_id: str) -> ConfigStore:
        return ConfigStore(app.state.provider_base / "users" / user_id)

    def file_context_for(node_id: str, user_id: str) -> tuple[str, int]:
        attachments = db().list_context_attachments(node_id, user_id, include_text=True)
        context = build_attachment_context(attachments)
        return context, len(attachments)

    def get_current_user(request: Request) -> dict[str, Any]:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            raise HTTPException(401, "未登录")
        user = db().get_session_user(session_token_hash(token))
        if not user:
            raise HTTPException(401, "未登录")
        return user

    def public_user(user: dict[str, Any]) -> dict[str, Any]:
        return {"id": user["id"], "username": user["username"], "role": user["role"]}

    def issue_session(response: Response, user_id: str) -> str:
        token = new_session_token()
        db().create_session(session_token_hash(token), user_id, session_expires())
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=60 * 60 * 24 * 7,
            httponly=True,
            secure=secure_cookies_enabled(),
            samesite="lax",
            path="/",
        )
        return token

    @app.post("/api/auth/register", status_code=201)
    def register(body: AuthBody, response: Response):
        if db().get_user_by_username(body.username):
            raise HTTPException(409, "用户名已被占用")
        role = "admin" if db().user_count() == 0 else "user"
        digest, salt = hash_password(body.password)
        user = db().create_user(body.username, digest, salt, role)
        issue_session(response, user["id"])
        return {"user": public_user(user)}

    @app.post("/api/auth/login")
    def login(body: AuthBody, response: Response):
        user = db().get_user_by_username(body.username)
        if not user or not verify_password(body.password, user["salt"], user["password_hash"]):
            raise HTTPException(401, "用户名或密码错误")
        issue_session(response, user["id"])
        return {"user": public_user(user)}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get(COOKIE_NAME)
        if token:
            db().delete_session(session_token_hash(token))
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True}

    @app.get("/api/auth/me")
    def me(user: dict[str, Any] = Depends(get_current_user)):
        return {"user": public_user(user)}

    async def model_reply(
        messages: list[dict[str, str]],
        user_id: str,
        config: ModelConfig | None = None,
        *,
        operation: str = "model",
        operation_id: str = "-",
    ) -> str:
        active = config
        if active is None:
            selected = providers_for(user_id).active()
            if selected:
                active = selected[0]
        if not active or not active.base_url or not active.model or not active.api_key:
            raise HTTPException(409, "请先在设置中配置并测试模型服务")
        started = time.perf_counter()
        try:
            reply = await app.state.models.complete(active, messages)
            logger.info(
                "model_complete operation=%s operation_id=%s user=%s model=%s elapsed_ms=%d",
                operation, operation_id, user_id[:8], active.model, (time.perf_counter() - started) * 1000,
            )
            return reply
        except ProviderError as exc:
            logger.warning(
                "model_failed operation=%s operation_id=%s user=%s model=%s reason=%s elapsed_ms=%d",
                operation, operation_id, user_id[:8], active.model, str(exc), (time.perf_counter() - started) * 1000,
            )
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/providers")
    def list_providers(user: dict[str, Any] = Depends(get_current_user)):
        public = []
        for provider in providers_for(user["id"]).list_public():
            item = dict(provider)
            item["has_api_key"] = bool(providers_for(user["id"]).get_secret(str(provider["id"])))
            public.append(item)
        return {"providers": public, "active": providers_for(user["id"]).active_public()}

    @app.post("/api/providers", status_code=201)
    async def create_provider(body: ProviderBody, user: dict[str, Any] = Depends(get_current_user)):
        requested_model = body.model.strip()
        models = sorted(set(([requested_model] if requested_model else []) + body.models))
        candidate = ModelConfig(body.base_url.strip(), body.protocol, requested_model or "model-discovery", body.api_key)
        if not candidate.api_key:
            raise HTTPException(422, "新增 provider 必须填写 API key")
        if not requested_model:
            try:
                models = sorted(set([*models, *(await app.state.models.discover_models(candidate))]))
            except ProviderError as exc:
                raise HTTPException(502, "未填写模型且无法自动发现模型；请确认该中转站支持 /models，或手动填写模型名") from exc
            if not models:
                raise HTTPException(502, "模型列表为空，请手动填写模型名")
            requested_model = models[0]
            candidate = ModelConfig(candidate.base_url, candidate.protocol, requested_model, candidate.api_key)
        else:
            await model_reply([{"role": "user", "content": "只回复 OK"}], user["id"], candidate)
        provider = providers_for(user["id"]).create(body.name.strip(), candidate.base_url, candidate.protocol, models, body.kind)
        providers_for(user["id"]).save_secret(str(provider["id"]), candidate.api_key)
        if body.kind == "chat":
            providers_for(user["id"]).set_active(str(provider["id"]), requested_model)
        item = dict(provider); item["has_api_key"] = True
        return {"provider": item, "active": providers_for(user["id"]).active_public()}

    @app.patch("/api/providers/{provider_id}")
    async def update_provider(provider_id: str, body: ProviderPatchBody, user: dict[str, Any] = Depends(get_current_user)):
        current = providers_for(user["id"]).get_public(provider_id)
        if not current:
            raise HTTPException(404, "provider 不存在")
        key = body.api_key if body.api_key is not None and body.api_key else providers_for(user["id"]).get_secret(provider_id)
        changed = body.model_dump(exclude_unset=True)
        changed.pop("api_key", None)
        if body.base_url: changed["base_url"] = body.base_url.strip()
        if body.models is not None:
            models = sorted(set(model.strip() for model in body.models if model.strip()))
            if not models:
                raise HTTPException(422, "模型列表不能为空")
            changed["models"] = models
        if body.api_key is not None and not key:
            raise HTTPException(422, "API key 不能为空")
        if changed.get("protocol") or changed.get("base_url"):
            probe_model = (body.models or current.get("models") or [""])[0]
            await model_reply([{"role": "user", "content": "只回复 OK"}], user["id"], ModelConfig(str(changed.get("base_url", current["base_url"])), str(changed.get("protocol", current["protocol"])), str(probe_model), key))
        updated = providers_for(user["id"]).update(provider_id, **changed)
        if body.api_key is not None: providers_for(user["id"]).save_secret(provider_id, key)
        item = dict(updated); item["has_api_key"] = bool(key)
        return item

    @app.delete("/api/providers/{provider_id}", status_code=204)
    def delete_provider(provider_id: str, user: dict[str, Any] = Depends(get_current_user)):
        if not providers_for(user["id"]).delete(provider_id):
            raise HTTPException(404, "provider 不存在")

    @app.post("/api/providers/{provider_id}/test")
    async def test_provider(provider_id: str, user: dict[str, Any] = Depends(get_current_user)):
        provider = providers_for(user["id"]).get_public(provider_id)
        if not provider:
            raise HTTPException(404, "provider 不存在")
        models = provider.get("models", [])
        if not models:
            raise HTTPException(422, "请先添加至少一个模型")
        await model_reply([{"role": "user", "content": "只回复 OK"}], user["id"], ModelConfig(str(provider["base_url"]), str(provider["protocol"]), str(models[0]), providers_for(user["id"]).get_secret(provider_id)))
        return {"ok": True}

    @app.post("/api/providers/{provider_id}/discover-models")
    async def discover_models(provider_id: str, user: dict[str, Any] = Depends(get_current_user)):
        provider = providers_for(user["id"]).get_public(provider_id)
        if not provider:
            raise HTTPException(404, "provider 不存在")
        try:
            models = await app.state.models.discover_models(ModelConfig(str(provider["base_url"]), str(provider["protocol"]), "model-discovery", providers_for(user["id"]).get_secret(provider_id)))
        except ProviderError as exc:
            raise HTTPException(502, str(exc)) from exc
        providers_for(user["id"]).update(provider_id, models=sorted(set([*provider.get("models", []), *models])))
        return {"models": providers_for(user["id"]).get_public(provider_id).get("models", [])}

    @app.get("/api/active-model")
    def get_active_model(user: dict[str, Any] = Depends(get_current_user)):
        return providers_for(user["id"]).active_public()

    @app.put("/api/active-model")
    def set_active_model(body: ActiveModelBody, user: dict[str, Any] = Depends(get_current_user)):
        try:
            return providers_for(user["id"]).set_active(body.provider_id, body.model)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/discussions")
    def list_discussions(user: dict[str, Any] = Depends(get_current_user)):
        return db().list_discussions(user["id"])

    @app.get("/api/search")
    def search(q: str = Query(min_length=1, max_length=200), user: dict[str, Any] = Depends(get_current_user)):
        query = q.strip()
        if not query:
            raise HTTPException(422, "搜索内容不能为空")
        return {"results": db().search(user["id"], query)}

    @app.post("/api/discussions", status_code=201)
    def create_discussion(body: TitleBody, user: dict[str, Any] = Depends(get_current_user)):
        discussion, root = db().create_discussion(body.title, user["id"])
        return {"discussion": discussion, "root_node": root}

    @app.get("/api/discussions/{discussion_id}")
    def get_discussion(discussion_id: str, user: dict[str, Any] = Depends(get_current_user)):
        discussion = db().get_discussion(discussion_id, user["id"])
        if not discussion:
            raise HTTPException(404, "讨论不存在")
        return discussion

    @app.patch("/api/discussions/{discussion_id}")
    def update_discussion(discussion_id: str, body: TitleBody, user: dict[str, Any] = Depends(get_current_user)):
        result = db().update_discussion(discussion_id, user["id"], body.title)
        if not result:
            raise HTTPException(404, "讨论不存在")
        return result

    @app.delete("/api/discussions/{discussion_id}", status_code=204)
    def delete_discussion(discussion_id: str, user: dict[str, Any] = Depends(get_current_user)):
        if not db().delete_discussion(discussion_id, user["id"]):
            raise HTTPException(404, "讨论不存在")

    @app.get("/api/discussions/{discussion_id}/nodes")
    def list_nodes(discussion_id: str, user: dict[str, Any] = Depends(get_current_user)):
        if not db().get_discussion(discussion_id, user["id"]):
            raise HTTPException(404, "讨论不存在")
        return db().list_nodes(discussion_id, user["id"])

    @app.post("/api/discussions/{discussion_id}/nodes", status_code=201)
    def create_node(discussion_id: str, body: NodeBody, user: dict[str, Any] = Depends(get_current_user)):
        parent = db().get_node(body.parent_id, user["id"])
        if not parent or parent["discussion_id"] != discussion_id:
            raise HTTPException(400, "父卡片不属于当前讨论")
        return db().create_node(discussion_id, body.title, body.parent_id, user["id"])

    @app.patch("/api/discussions/{discussion_id}/nodes/{node_id}")
    def update_node(discussion_id: str, node_id: str, body: TitleBody, user: dict[str, Any] = Depends(get_current_user)):
        node = db().get_node(node_id, user["id"])
        if not node or node["discussion_id"] != discussion_id:
            raise HTTPException(404, "卡片不存在")
        return db().update_node(node_id, user["id"], body.title)

    @app.delete("/api/discussions/{discussion_id}/nodes/{node_id}")
    def delete_node(discussion_id: str, node_id: str, user: dict[str, Any] = Depends(get_current_user)):
        node = db().get_node(node_id, user["id"])
        if not node or node["discussion_id"] != discussion_id:
            raise HTTPException(404, "卡片不存在")
        if node["parent_id"] is None:
            raise HTTPException(400, "不能删除讨论主线")
        db().delete_node(node_id, user["id"])

    @app.get("/api/nodes/{node_id}/messages")
    def list_messages(node_id: str, user: dict[str, Any] = Depends(get_current_user)):
        if not db().get_node(node_id, user["id"]):
            raise HTTPException(404, "卡片不存在")
        return db().list_messages(node_id, user["id"])

    @app.get("/api/nodes/{node_id}/attachments")
    def list_attachments(node_id: str, user: dict[str, Any] = Depends(get_current_user)):
        if not db().get_node(node_id, user["id"]):
            raise HTTPException(404, "卡片不存在")
        return {"attachments": db().list_context_attachments(node_id, user["id"])}

    @app.post("/api/nodes/{node_id}/attachments", status_code=201)
    async def upload_attachment(node_id: str, file: UploadFile = File(...), user: dict[str, Any] = Depends(get_current_user)):
        if not db().get_node(node_id, user["id"]):
            raise HTTPException(404, "卡片不存在")
        filename = Path(file.filename or "").name.strip()
        if not filename or len(filename) > 200:
            raise HTTPException(422, "文件名无效或超过 200 个字符")
        content = await file.read(MAX_FILE_BYTES + 1)
        try:
            extracted_text, truncated, file_format = extract_attachment(filename, content)
        except AttachmentError as exc:
            raise HTTPException(422, str(exc)) from exc
        attachment = db().create_attachment(
            node_id, user["id"], filename, file.content_type or "application/octet-stream",
            file_format, content, extracted_text, truncated,
        )
        logger.info(
            "attachment_uploaded user=%s node=%s attachment=%s format=%s size_bytes=%d extracted_chars=%d truncated=%s",
            user["id"][:8], node_id[:8], attachment["id"][:8], file_format, len(content), len(extracted_text), truncated,
        )
        return attachment

    @app.delete("/api/nodes/{node_id}/attachments/{attachment_id}", status_code=204)
    def delete_attachment(node_id: str, attachment_id: str, user: dict[str, Any] = Depends(get_current_user)):
        if not db().delete_attachment(attachment_id, node_id, user["id"]):
            raise HTTPException(404, "文件不存在，或继承文件不能在当前子卡片删除")
        logger.info(
            "attachment_deleted user=%s node=%s attachment=%s",
            user["id"][:8], node_id[:8], attachment_id[:8],
        )

    @app.post("/api/nodes/{node_id}/messages", status_code=201)
    async def send_message(node_id: str, body: MessageBody, user: dict[str, Any] = Depends(get_current_user)):
        operation_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        auth_user_id = user["id"]
        node = db().get_node(node_id, user["id"])
        if not node:
            raise HTTPException(404, "卡片不存在")
        history = [{"role": item["role"], "content": item["content"]} for item in db().list_messages(node_id, user["id"])]
        file_context, attachment_count = file_context_for(node_id, user["id"])
        system_prompt = SYSTEM_PROMPT
        if file_context:
            system_prompt += "\n以下是用户上传并与当前卡片关联的背景文件。回答时以文件内容为依据；若文件没有提供答案，请明确说明。\n" + file_context
        prompt = [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": body.content}]
        selected = providers_for(user["id"]).active()
        logger.info(
            "chat_started operation_id=%s user=%s node=%s chars=%d attachments=%d",
            operation_id, user["id"][:8], node_id[:8], len(body.content), attachment_count,
        )
        reply = await model_reply(prompt, user["id"], operation="chat", operation_id=operation_id)
        user_message, assistant = db().add_exchange(node_id, body.content, reply, auth_user_id, selected[1] if selected else None, selected[2] if selected else None)
        logger.info(
            "chat_saved operation_id=%s user=%s node=%s elapsed_ms=%d",
            operation_id, auth_user_id[:8], node_id[:8], (time.perf_counter() - started) * 1000,
        )
        return {"user": user_message, "assistant": assistant}

    @app.post("/api/nodes/{node_id}/expand", status_code=201)
    async def expand_node(node_id: str, body: ExpandBody, user: dict[str, Any] = Depends(get_current_user)):
        operation_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        auth_user_id = user["id"]
        parent = db().get_node(node_id, user["id"])
        if not parent:
            raise HTTPException(404, "卡片不存在")
        source = db().get_message(body.source_message_id, user["id"])
        if not source or source["node_id"] != node_id or source["role"] not in {"user", "assistant"}:
            raise HTTPException(400, "展开来源必须是当前卡片中的一条消息")
        if not selection_matches_source(body.selected_text, source["content"]):
            logger.warning(
                "expand_rejected operation_id=%s user=%s node=%s reason=selection_mismatch selected_chars=%d source_chars=%d",
                operation_id, user["id"][:8], node_id[:8], len(body.selected_text), len(source["content"]),
            )
            raise HTTPException(400, "选中文字不在来源回答中")
        related = db().related_user_message(body.source_message_id, user["id"]) if source["role"] == "assistant" else source
        path_titles = [item["title"] for item in db().ancestors(node_id, user["id"])]
        question = (body.custom_question or "请解释这段文字，说明它在当前上下文中的含义，并给出必要例子。 ").strip()
        context = {
            "parent_title": parent["title"],
            "related_user_question": related["content"] if related else "",
            "selected_text": body.selected_text,
            "source_role": source["role"],
            "source_content": source["content"],
            "source_answer": source["content"] if source["role"] == "assistant" else "",
            "ancestor_titles": path_titles,
        }
        file_context, attachment_count = file_context_for(node_id, user["id"])
        source_label = "用户提供的背景材料" if source["role"] == "user" else "AI 回答"
        system = (
            f"{SYSTEM_PROMPT}\n这是从父卡片展开的子讨论。只使用下面给出的局部背景；不要假设父卡片的其他内容。\n"
            f"祖先路径：{' > '.join(path_titles)}\n父卡片：{parent['title']}\n"
            f"相关用户问题或材料：{context['related_user_question']}\n来源类型：{source_label}\n来源内容：{source['content']}\n"
            f"选中文字：{body.selected_text}"
        )
        if file_context:
            system += "\n当前卡片及祖先卡片关联的背景文件：\n" + file_context
        selected = providers_for(user["id"]).active()
        logger.info(
            "expand_started operation_id=%s user=%s node=%s source_message=%s source_role=%s selected_chars=%d custom=%s attachments=%d",
            operation_id, user["id"][:8], node_id[:8], source["id"][:8], source["role"], len(body.selected_text), bool(body.custom_question), attachment_count,
        )
        reply = await model_reply(
            [{"role": "system", "content": system}, {"role": "user", "content": question}],
            user["id"], operation="expand", operation_id=operation_id,
        )
        title = body.selected_text.replace("\n", " ")[:42]
        child = db().create_node(parent["discussion_id"], title, node_id, user["id"], source["id"], body.selected_text, context)
        user_message, assistant = db().add_exchange(child["id"], question, reply, auth_user_id, selected[1] if selected else None, selected[2] if selected else None)
        logger.info(
            "expand_saved operation_id=%s user=%s parent=%s child=%s elapsed_ms=%d",
            operation_id, auth_user_id[:8], node_id[:8], child["id"][:8], (time.perf_counter() - started) * 1000,
        )
        return {"node": child, "messages": [user_message, assistant]}

    @app.get("/api/settings")
    def get_settings(user: dict[str, Any] = Depends(get_current_user)):
        active = config_store_for(user["id"]).load()
        return active.public() if active else {"base_url": "", "protocol": "chat_completions", "model": "", "has_api_key": False}

    @app.post("/api/settings/test")
    async def test_settings(body: SettingsBody, user: dict[str, Any] = Depends(get_current_user)):
        current = config_store_for(user["id"]).load()
        candidate = body.config(current.api_key if current else "")
        await model_reply([{"role": "user", "content": "只回复 OK"}], user["id"], candidate)
        return {"ok": True}

    @app.put("/api/settings")
    async def put_settings(body: SettingsBody, user: dict[str, Any] = Depends(get_current_user)):
        current = config_store_for(user["id"]).load()
        candidate = body.config(current.api_key if current else "")
        if not candidate.api_key:
            raise HTTPException(422, "首次配置必须填写 API key")
        await model_reply([{"role": "user", "content": "只回复 OK"}], user["id"], candidate)
        config_store_for(user["id"]).save(candidate)
        provider_store = providers_for(user["id"])
        selected = provider_store.active()
        if selected:
            provider_id = selected[1]
            provider_store.update(
                provider_id,
                base_url=candidate.base_url,
                protocol=candidate.protocol,
                models=[candidate.model],
                enabled=True,
                kind="chat",
            )
        else:
            provider = provider_store.create(
                "兼容设置",
                candidate.base_url,
                candidate.protocol,
                [candidate.model],
            )
            provider_id = str(provider["id"])
        provider_store.save_secret(provider_id, candidate.api_key)
        provider_store.set_active(provider_id, candidate.model)
        return candidate.public()

    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    launcher = Path(__file__).resolve().parents[2] / "frontends" / "launcher" / "index.html"
    launcher_script = launcher.with_name("launcher.js")
    @app.get("/api/frontends")
    def list_frontends(user: dict[str, Any] = Depends(get_current_user)):
        return {"frontends": [
            {"id": "classic", "label": "Classic", "available": frontend_dist.exists(), "url": "/ui/classic/"},
        ]}

    @app.get("/launcher", include_in_schema=False)
    def frontend_launcher():
        if not launcher.exists():
            raise HTTPException(404, "前端 launcher 不存在")
        return FileResponse(launcher)

    @app.get("/launcher.js", include_in_schema=False)
    def frontend_launcher_script():
        if not launcher_script.exists():
            raise HTTPException(404, "前端 launcher 脚本不存在")
        return FileResponse(launcher_script, media_type="text/javascript")

    @app.get("/ui/classic/{path:path}", include_in_schema=False)
    def classic_ui(path: str):
        requested = _resolve_frontend_file(frontend_dist, path)
        if path and requested:
            return FileResponse(requested)
        index = frontend_dist / "index.html"
        if not index.is_file():
            raise HTTPException(404, "Classic 前端尚未构建")
        return FileResponse(index)

    if frontend_dist.exists() and os.environ.get("CONCEPT_BRANCH_SERVE_FRONTEND", "1") == "1":
        assets = frontend_dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "接口不存在")
            requested = _resolve_frontend_file(frontend_dist, path)
            if path and requested:
                return FileResponse(requested)
            return FileResponse(frontend_dist / "index.html")

    return app


app = create_app()

from __future__ import annotations

from typing import Any

import httpx

from .config import ModelConfig


class ProviderError(RuntimeError):
    """A sanitized model provider error safe for API clients."""


def _endpoint(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}{suffix}"


def extract_chat_text(payload: dict[str, Any]) -> str:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("模型返回格式无效") from exc
    if isinstance(content, list):
        content = "".join(
            str(item.get("text", "")) for item in content if isinstance(item, dict)
        )
    if not isinstance(content, str) or not content.strip():
        raise ProviderError("模型返回了空响应")
    return content.strip()


def extract_responses_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    chunks: list[str] = []
    for output in payload.get("output", []) if isinstance(payload.get("output"), list) else []:
        if not isinstance(output, dict):
            continue
        for item in output.get("content", []) if isinstance(output.get("content"), list) else []:
            if isinstance(item, dict) and item.get("type") in {"output_text", "text"}:
                text = item.get("text")
                if isinstance(text, str):
                    chunks.append(text)
    result = "".join(chunks).strip()
    if not result:
        raise ProviderError("模型返回了空响应")
    return result


class ModelClient:
    def __init__(self, timeout: float = 120.0):
        self.timeout = timeout

    async def complete(self, config: ModelConfig, messages: list[dict[str, str]]) -> str:
        if config.protocol not in {"chat_completions", "responses"}:
            raise ProviderError("不支持的 API 协议")
        headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}
        if config.protocol == "chat_completions":
            url = _endpoint(config.base_url, "/chat/completions")
            body: dict[str, Any] = {"model": config.model, "messages": messages, "stream": False}
            extractor = extract_chat_text
        else:
            url = _endpoint(config.base_url, "/responses")
            body = {"model": config.model, "input": messages, "stream": False}
            extractor = extract_responses_text
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderError("连接模型超时") from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"模型服务返回 HTTP {exc.response.status_code}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("无法连接或解析模型服务") from exc
        return extractor(payload)

    async def discover_models(self, config: ModelConfig) -> list[str]:
        """Best-effort OpenAI-compatible /models discovery; caller preserves manual list on failure."""
        headers = {"Authorization": f"Bearer {config.api_key}"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(_endpoint(config.base_url, "/models"), headers=headers)
            response.raise_for_status()
            payload = response.json()
            models = payload.get("data", []) if isinstance(payload, dict) else []
            result = sorted({str(item["id"]) for item in models if isinstance(item, dict) and item.get("id")})
            if not result:
                raise ProviderError("模型服务未返回可用模型")
            return result
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError("读取模型列表超时") from exc
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise ProviderError("无法读取模型列表") from exc

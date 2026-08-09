from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    protocol: str
    model: str
    api_key: str

    def public(self) -> dict[str, object]:
        return {
            "base_url": self.base_url,
            "protocol": self.protocol,
            "model": self.model,
            "has_api_key": bool(self.api_key),
        }


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


class ProviderStore:
    """Concept Branch-owned provider registry; deliberately unrelated to Codex state."""

    def __init__(self, root: Path | None = None):
        root_env = os.environ.get("CONCEPT_BRANCH_CONFIG_DIR")
        self.root = Path(root or root_env or Path.home() / ".config" / "concept-branch")
        self.registry_path = self.root / "providers.json"
        self.secrets_dir = self.root / "provider-secrets"
        self.legacy = ConfigStore(self.root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self._migrate_legacy()

    def _read(self) -> dict[str, object]:
        if not self.registry_path.exists():
            return {"providers": [], "active_provider_id": None, "active_model": None}
        return json.loads(self.registry_path.read_text(encoding="utf-8"))

    def _write(self, registry: dict[str, object]) -> None:
        self._atomic_json(self.registry_path, registry, 0o600)

    def _migrate_legacy(self) -> None:
        if self.registry_path.exists():
            return
        old = self.legacy.load()
        if not old:
            self._write({"providers": [], "active_provider_id": None, "active_model": None})
            return
        provider_id = str(uuid.uuid4())
        provider = {"id": provider_id, "name": "旧版活动配置", "base_url": old.base_url, "protocol": old.protocol, "models": [old.model], "enabled": True, "created_at": _stamp(), "updated_at": _stamp()}
        self._write({"providers": [provider], "active_provider_id": provider_id, "active_model": old.model})
        self.save_secret(provider_id, old.api_key)

    def list_public(self) -> list[dict[str, object]]:
        return list(self._read().get("providers", []))

    def get_public(self, provider_id: str) -> dict[str, object] | None:
        return next((p for p in self.list_public() if p.get("id") == provider_id), None)

    def get_secret(self, provider_id: str) -> str:
        path = self.secrets_dir / f"{provider_id}.json"
        if not path.exists():
            return ""
        return str(json.loads(path.read_text(encoding="utf-8")).get("api_key", ""))

    def save_secret(self, provider_id: str, api_key: str) -> None:
        self.secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.secrets_dir, 0o700)
        self._atomic_json(self.secrets_dir / f"{provider_id}.json", {"api_key": api_key}, 0o600)

    def delete_secret(self, provider_id: str) -> None:
        path = self.secrets_dir / f"{provider_id}.json"
        if path.exists():
            path.unlink()

    def create(self, name: str, base_url: str, protocol: str, models: list[str], kind: str = "chat") -> dict[str, object]:
        provider_id = str(uuid.uuid4())
        stamp = _stamp()
        provider = {"id": provider_id, "name": name, "base_url": base_url.rstrip("/"), "protocol": protocol, "kind": kind, "models": sorted(set(models)), "enabled": True, "has_api_key": False, "created_at": stamp, "updated_at": stamp}
        registry = self._read(); registry.setdefault("providers", []).append(provider); self._write(registry)
        return provider

    def update(self, provider_id: str, **changes: object) -> dict[str, object] | None:
        registry = self._read()
        for provider in registry.get("providers", []):
            if provider.get("id") == provider_id:
                for key, value in changes.items():
                    if value is not None and key in {"name", "base_url", "protocol", "models", "enabled", "kind"}:
                        provider[key] = value.rstrip("/") if key == "base_url" and isinstance(value, str) else value
                provider["updated_at"] = _stamp()
                self._write(registry); return provider
        return None

    def delete(self, provider_id: str) -> bool:
        registry = self._read(); providers = registry.get("providers", [])
        kept = [p for p in providers if p.get("id") != provider_id]
        if len(kept) == len(providers): return False
        registry["providers"] = kept
        if registry.get("active_provider_id") == provider_id:
            registry["active_provider_id"] = kept[0]["id"] if kept else None
            registry["active_model"] = kept[0].get("models", [None])[0] if kept else None
        self._write(registry); self.delete_secret(provider_id); return True

    def set_active(self, provider_id: str, model: str) -> dict[str, object]:
        provider = self.get_public(provider_id)
        if not provider or not provider.get("enabled") or provider.get("kind", "chat") != "chat" or model not in provider.get("models", []):
            raise ValueError("provider 或模型不可用")
        registry = self._read(); registry["active_provider_id"] = provider_id; registry["active_model"] = model; self._write(registry)
        return {"provider_id": provider_id, "model": model}

    def active(self) -> tuple[ModelConfig, str, str] | None:
        registry = self._read(); provider_id = registry.get("active_provider_id"); model = registry.get("active_model")
        provider = self.get_public(str(provider_id)) if provider_id else None
        if not provider or not model: return None
        return ModelConfig(str(provider["base_url"]), str(provider["protocol"]), str(model), self.get_secret(str(provider_id))), str(provider_id), str(model)

    def active_public(self) -> dict[str, object]:
        registry = self._read(); provider = self.get_public(str(registry["active_provider_id"])) if registry.get("active_provider_id") else None
        return {"provider_id": provider.get("id") if provider else None, "provider_name": provider.get("name") if provider else None, "model": registry.get("active_model") if provider else None}

    @staticmethod
    def _atomic_json(path: Path, data: dict[str, object], mode: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
            os.replace(tmp_name, path); os.chmod(path, mode)
        except Exception:
            try: os.unlink(tmp_name)
            except FileNotFoundError: pass
            raise


class ConfigStore:
    def __init__(self, root: Path | None = None):
        root_env = os.environ.get("CONCEPT_BRANCH_CONFIG_DIR")
        self.root = Path(root or root_env or Path.home() / ".config" / "concept-branch")
        self.settings_path = self.root / "settings.json"
        self.secret_path = self.root / "secret.json"

    def load(self) -> ModelConfig | None:
        if not self.settings_path.exists():
            return None
        public = json.loads(self.settings_path.read_text(encoding="utf-8"))
        secret = {}
        if self.secret_path.exists():
            secret = json.loads(self.secret_path.read_text(encoding="utf-8"))
        return ModelConfig(
            base_url=str(public.get("base_url", "")),
            protocol=str(public.get("protocol", "chat_completions")),
            model=str(public.get("model", "")),
            api_key=str(secret.get("api_key", "")),
        )

    def save(self, config: ModelConfig) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        public = {k: v for k, v in asdict(config).items() if k != "api_key"}
        self._atomic_json(self.settings_path, public, 0o600)
        self._atomic_json(self.secret_path, {"api_key": config.api_key}, 0o600)

    @staticmethod
    def _atomic_json(path: Path, data: dict[str, object], mode: int) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
            os.chmod(path, mode)
        except Exception:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
            raise

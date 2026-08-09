import json
import stat

from concept_branch.config import ConfigStore, ModelConfig


def test_secret_is_separate_restricted_and_not_public(tmp_path):
    store = ConfigStore(tmp_path / "config")
    config = ModelConfig("http://local/v1", "responses", "model-x", "super-secret-value")
    store.save(config)
    assert store.load() == config
    assert "super-secret-value" not in store.settings_path.read_text()
    assert json.loads(store.secret_path.read_text()) == {"api_key": "super-secret-value"}
    assert stat.S_IMODE(store.secret_path.stat().st_mode) == 0o600
    assert config.public() == {"base_url": "http://local/v1", "protocol": "responses", "model": "model-x", "has_api_key": True}

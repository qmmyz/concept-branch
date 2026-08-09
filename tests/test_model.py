import json
from pathlib import Path

import pytest

from concept_branch.model import ProviderError, extract_chat_text, extract_responses_text


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def test_extract_chat_fixture():
    assert extract_chat_text(fixture("chat_success.json")) == "Chat fixture answer"


def test_extract_responses_fixture():
    assert extract_responses_text(fixture("responses_success.json")) == "Responses fixture answer"


@pytest.mark.parametrize("extractor,payload", [(extract_chat_text, {"choices": []}), (extract_responses_text, {"output": []})])
def test_empty_or_invalid_response_is_clear_error(extractor, payload):
    with pytest.raises(ProviderError, match="空响应|格式无效"):
        extractor(payload)

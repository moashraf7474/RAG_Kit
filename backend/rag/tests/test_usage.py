from types import SimpleNamespace
from unittest.mock import patch

from rag.usage import extract_token_usage, record_usage


def test_extract_token_usage_supports_langchain_usage_metadata():
    response = SimpleNamespace(
        usage_metadata={"input_tokens": 17, "output_tokens": 9}
    )

    assert extract_token_usage(response) == (17, 9)


def test_extract_token_usage_supports_openai_response_metadata():
    response = SimpleNamespace(
        response_metadata={"token_usage": {"prompt_tokens": 21, "completion_tokens": 13}}
    )

    assert extract_token_usage(response) == (21, 13)


def test_record_usage_is_scoped_to_an_organization():
    response = SimpleNamespace(usage_metadata={"input_tokens": 40, "output_tokens": 12})

    with patch("rag.usage.UsageRecord.objects.create") as create:
        record_usage(
            organization_id="org-123",
            operation="query",
            model="test-model",
            response=response,
        )

    create.assert_called_once_with(
        organization_id="org-123",
        operation="query",
        model="test-model",
        prompt_tokens=40,
        completion_tokens=12,
    )

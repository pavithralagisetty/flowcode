import pytest

from flowcode_agent.models import MergeGroups
from flowcode_agent.providers.llm_base import LLMError, extract_json
from tests.conftest import FakeLLM


def test_extract_json_strips_fences():
    assert extract_json('Here:\n```json\n{"a": 1}\n```') == '{"a": 1}'


def test_retries_once_with_error_message():
    replies = iter(['{"groups": "nope"}', '{"groups": [[0, 1]]}'])
    llm = FakeLLM({MergeGroups: lambda u: next(replies)})
    out = llm.complete_json("sys", "user", MergeGroups, "m")
    assert out.groups == [[0, 1]]
    assert "did not validate" in llm.calls[1][1]
    assert llm.input_tokens == 200 and llm.output_tokens == 100


def test_gives_up_after_one_retry():
    llm = FakeLLM({MergeGroups: lambda u: "not json"})
    with pytest.raises(LLMError):
        llm.complete_json("sys", "user", MergeGroups, "m")
    assert len(llm.calls) == 2

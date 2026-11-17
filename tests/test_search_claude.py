from types import SimpleNamespace as NS

from flowcode_agent.providers.search_claude import ClaudeSearchProvider


class FakeMessages:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)


def _response(stop, blocks, searches=1):
    return NS(stop_reason=stop, content=blocks, usage=NS(server_tool_use=NS(web_search_requests=searches),
                                                       input_tokens=1000, output_tokens=50))


def test_reads_only_search_result_links_and_resumes_pause_turn():
    hit = lambda url: NS(url=url, title="t", page_age="2 days ago")  # noqa: E731
    responses = [
        _response("pause_turn", [NS(type="web_search_tool_result", content=[hit("https://a.com"), hit("https://b.com")])]),
        _response("end_turn", [NS(type="web_search_tool_result", content=NS(error_code="max_uses_exceeded")),
                               NS(type="web_search_tool_result", content=[hit("https://a.com"), hit("https://c.com")]),
                               NS(type="text", text="Summary text that must be ignored https://ignored.com")]),
    ]
    provider = ClaudeSearchProvider.__new__(ClaudeSearchProvider)
    provider.model, provider.max_uses, provider.requests = "m", 3, 0
    provider.input_tokens = provider.output_tokens = 0
    provider.client = NS(messages=FakeMessages(responses))
    results = provider.search("acme campaign")
    assert [r.url for r in results] == ["https://a.com", "https://b.com", "https://c.com"]
    assert provider.requests == 2
    assert (provider.input_tokens, provider.output_tokens) == (2000, 100)
    assert provider.client.messages.calls[1]["messages"][1]["role"] == "assistant"

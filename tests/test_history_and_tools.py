"""长会话摘要与真实工具适配的回归。"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from suite.agent import Agent  # noqa: E402
from suite.core import Config, Provider, Store  # noqa: E402
from suite.rag import RAG  # noqa: E402
from suite.tools import HttpTools  # noqa: E402


class SummaryProvider(Provider):
    def __init__(self, config):
        super().__init__(config)
        self.events = []

    def chat(self, messages, tools=None, tool_choice="auto", json_mode=False):
        return {"role": "assistant", "content": "用户问过A001的支付状态；已确认需要人工建单。"}


@pytest.fixture
def agent(tmp_path):
    config = Config(data=tmp_path)
    store = Store(config)
    return Agent(store, RAG(store), provider_factory=SummaryProvider)


def test_long_history_is_summarized(agent):
    history = []
    for i in range(150):
        history.append({"role": "user", "content": f"问题{i}"})
        history.append({"role": "assistant", "content": f"回答{i}"})
    provider = SummaryProvider(agent.store.config)
    with patch.object(provider, "chat", wraps=provider.chat) as chat:
        trimmed = agent._trim_history(history, provider)
    chat.assert_not_called()
    assert trimmed[0]["role"] == "system"
    assert "摘要" in trimmed[0]["content"]
    assert len(trimmed) == 61  # 1 条摘要 + 最近 60 条


def test_short_history_untouched(agent):
    history = [{"role": "user", "content": "查A001"}]
    assert agent._trim_history(history, SummaryProvider(agent.store.config)) == history


class FakeResponse:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_http_tools_retries_once_on_timeout(monkeypatch):
    import requests

    calls = {"n": 0}

    def fake_get(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.Timeout("slow")
        return FakeResponse(200, {"order_id": "A001", "status": "已支付"})

    monkeypatch.setattr(requests, "get", fake_get)
    tools = HttpTools("https://example.invalid")
    result = tools.query_order("A001")
    assert result["found"] is True
    assert calls["n"] == 2


def test_http_tools_maps_404_to_not_found(monkeypatch):
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse(404))
    tools = HttpTools("https://example.invalid")
    assert tools.query_order("A999")["found"] is False

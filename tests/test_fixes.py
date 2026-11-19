# 本轮修复回归：知识检索重排接通、执行记录/反馈鉴权、真实工具选型。
# 全部离线运行：重排用记录调用的替身Provider，工具选型只断言工厂结果，不发外部请求。

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from suite.agent import Agent  # noqa: E402
from suite.app import create_app, select_tools  # noqa: E402
from suite.core import Config, Provider, Store  # noqa: E402
from suite.rag import RAG  # noqa: E402
from suite.tools import HttpTools, MockTools  # noqa: E402

GUIDE = """# 网络连接排查步骤
网络连接排查先检查网线、路由器、交换机、防火墙、网关与DNS解析。
网络连接排查还要确认物理连接和网络连接配置，网络连接排查按顺序逐项排除。

# 特殊处理
目标片段：网络连接异常时先重启光猫，再观察指示灯状态。
"""


class RecordingProvider(Provider):
    """替身：embed走真实demo哈希实现，rank记录调用并把目标片段打成最高分。"""

    def __init__(self, config):
        super().__init__(config)
        self.rank_calls = []

    def rank(self, question, texts):
        self.rank_calls.append((question, list(texts)))
        return [10.0 if "目标片段" in text else 0.0 for text in texts]


@pytest.fixture
def knowledge_agent(tmp_path):
    config = Config(data=tmp_path)
    store = Store(config)
    rag = RAG(store, provider_factory=RecordingProvider)
    rag.ingest("guide.md", GUIDE.encode())
    return Agent(store, rag, provider_factory=RecordingProvider), rag, config


# Agent知识工具必须走hybrid_rerank策略，且本地rank确实被调用并改变排序。
def test_agent_knowledge_tool_uses_local_rerank(knowledge_agent):
    agent, rag, config = knowledge_agent
    provider = RecordingProvider(config)
    with patch.object(rag, "retrieve", wraps=rag.retrieve) as spy:
        result = agent.read_tool("search_knowledge", {"question": "网络连接排查"}, provider=provider)
    assert spy.call_args[0][1] == "hybrid_rerank"
    assert len(provider.rank_calls) == 1
    assert len(provider.rank_calls[0][1]) == len(result["sources"])
    assert "目标片段" in result["sources"][0]["text"]


# 没有本地重排模型时rank返回None，hybrid_rerank应退回原有启发式而不是报错。
def test_rerank_falls_back_when_local_model_unavailable(tmp_path):
    config = Config(data=tmp_path)
    store = Store(config)
    rag = RAG(store)
    rag.ingest("guide.md", GUIDE.encode())
    sources = rag.retrieve("网络连接排查", "hybrid_rerank")
    assert sources


# 设置APP_TOKEN后，执行记录与反馈接口无口令应被拒，带正确口令可达业务逻辑。
def test_traces_and_feedback_require_token(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_TOKEN", "secret-x")
    client = TestClient(create_app(Config(data=tmp_path)))
    assert client.get("/api/traces").status_code == 401
    assert client.get("/api/traces/abc").status_code == 401
    assert client.post("/api/feedback", json={"trace_id": "x", "rating": 1, "comment": ""}).status_code == 401
    headers = {"X-API-Key": "secret-x"}
    assert client.get("/api/traces", headers=headers).status_code == 200
    # 带口令后进入业务校验：trace不存在返回404，证明不是被鉴权挡住
    assert client.post("/api/feedback", json={"trace_id": "x", "rating": 1, "comment": ""},
                       headers=headers).status_code == 404


# 未设置APP_TOKEN时保持本地开放行为。
def test_traces_open_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv("APP_TOKEN", raising=False)
    client = TestClient(create_app(Config(data=tmp_path)))
    assert client.get("/api/traces").status_code == 200


# 未配置TOOLS_API_BASE时默认模拟工具，保持原有行为。
def test_tools_default_to_mock(tmp_path):
    app = create_app(Config(data=tmp_path))
    assert isinstance(app.state.agent.tools, MockTools)
    assert isinstance(select_tools(Config(data=tmp_path)), MockTools)


# 环境变量配置了工具服务基地址时，工厂应注入HttpTools（只断言选型，不发请求）。
def test_tools_base_env_selects_http(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA", str(tmp_path))
    monkeypatch.setenv("TOOLS_API_BASE", "https://tools.example.invalid")
    app = create_app()
    assert isinstance(app.state.agent.tools, HttpTools)
    assert app.state.agent.tools.base_url == "https://tools.example.invalid"
    direct = select_tools(Config(data=tmp_path, tools_api_base="https://tools.example.invalid"))
    assert isinstance(direct, HttpTools)

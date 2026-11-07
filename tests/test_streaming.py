# 流式咨询回归：断言前端依赖的事件顺序与错误帧，避免接口契约被悄悄改掉。
# 这里只验证离线演示流程，不代表真实模型的选择质量。

import json

import pytest
from fastapi.testclient import TestClient

from suite.app import create_app
from suite.core import Config


@pytest.fixture
def client(tmp_path):
    # 明确关闭本地模型，测试走哈希向量，避免依赖本机模型目录。
    return TestClient(create_app(Config(data=tmp_path, embedding_path="", reranker_path="")))


def read_events(response):
    """把 SSE 文本帧还原成事件列表，同时验证每帧都是合法 JSON。"""
    frames = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            frames.append(json.loads(line[6:]))
    return frames


def test_stream_emits_status_tool_delta_done(client):
    sid = client.post("/api/agent/sessions").json()["session_id"]
    with client.stream("POST", f"/api/agent/sessions/{sid}/chat/stream", json={"text": "查询订单A001"}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = read_events(response)

    types = [frame["type"] for frame in frames]
    assert types[0] == "status"
    assert types[-1] == "done"
    # 工具事件必须出现在最终回答之前，前端才能边执行边显示进度。
    assert types.index("tool") < types.index("delta")

    tool = frames[types.index("tool")]
    assert tool["name"] == "query_order" and tool["status"] == "completed"
    assert tool["result"]["found"] is True

    result = frames[-1]["result"]
    assert result["partial"] is False
    assert result["steps"] and result["trace_id"]
    assert "A001" in result["answer"]


def test_stream_reports_error_frame_for_unknown_session(client):
    with client.stream("POST", "/api/agent/sessions/not-a-session/chat/stream", json={"text": "查询订单A001"}) as response:
        assert response.status_code == 200  # 响应头已发出，失败只能走 error 事件
        frames = read_events(response)

    assert frames and frames[-1]["type"] == "error"
    assert "会话" in frames[-1]["message"]

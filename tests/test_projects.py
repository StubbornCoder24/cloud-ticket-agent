# 离线回归：用临时存储与必要的模型替身验证边界；通过测试不等于真实模型准确率。

import concurrent.futures
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from suite.app import create_app
from suite.core import AppError, Config, Provider, Store
from suite.rag import RAG, extract

@pytest.fixture
def app(tmp_path):
    return create_app(Config(data=tmp_path))

def session(app):
    return app.state.agent.session()

def draft(app, sid, request='r1', text='订单A001云服务器无法连接'):
    return app.state.agent.draft(sid, request, text)

# 缺编号先追问，补充时继承同会话意图；会话历史需要隔离。
def test_agent_missing_id_followup_persist_isolation(app):
    agent = app.state.agent
    sid = session(app)
    assert '请提供订单编号' in agent.chat(sid, '查询订单支付状态')['answer']
    reopened = create_app(app.state.store.config).state.agent
    result = reopened.chat(sid, 'A001')
    assert result['steps'][0]['result']['order']['status'] == '已支付'
    other = reopened.session()
    assert '请提供' in reopened.chat(other, '查订单')['answer']
    assert not json.loads(reopened.get_session(other)['messages'])[0]['content'] == 'A001'

# 缺字段草稿、编辑新版本、过期确认及取消终态的状态边界。
def test_draft_missing_edit_stale_cancel(app):
    agent, sid = (app.state.agent, session(app))
    task = draft(app, sid, text='服务器无法连接')
    assert task['state'] == 'collecting'
    updated = agent.edit_task(sid, task['id'], task['version'], 'A001', '云服务器无法连接')
    with pytest.raises(AppError, match='版本'):
        agent.decide(sid, task['id'], task['version'], '确认')
    cancelled = agent.decide(sid, task['id'], updated['version'], '取消')
    assert cancelled['ticket'] is None
    assert agent.decide(sid, task['id'], updated['version'], '确认')['task']['state'] == 'cancelled'

# 同请求复用、不同内容冲突、会话归属和重开数据库后的幂等行为。
def test_idempotency_conflict_owner_and_restart(app):
    agent, sid = (app.state.agent, session(app))
    task = draft(app, sid)
    assert draft(app, sid)['id'] == task['id']
    with pytest.raises(AppError, match='更换'):
        draft(app, sid, text='订单A001无法上传')
    with pytest.raises(AppError, match='没有此任务'):
        agent.task(session(app), task['id'])
    reopened = create_app(app.state.store.config).state.agent
    result = reopened.decide(sid, task['id'], task['version'], '确认')
    assert result['created']
    assert reopened.decide(sid, task['id'], task['version'], '确认')['reused']
    assert reopened.decide(sid, task['id'], task['version'], '取消')['task']['state'] == 'created'
    assert reopened.read_tool('query_ticket', {'ticket_id': result['ticket']['id']})['found']

# 并发确认同一任务最终只能落一张工单，而不是仅检查顺序调用。
def test_concurrent_confirm_one_ticket(app):
    agent, sid = (app.state.agent, session(app))
    task = draft(app, sid)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: agent.decide(sid, task['id'], task['version'], '确认'), range(4)))
    assert sum((row['created'] for row in rows)) == 1
    assert len({row['ticket']['id'] for row in rows}) == 1

# 故意制造事务内写入失败，确认不会只建工单却漏改任务状态。
def test_confirmation_transaction_rolls_back(app):
    agent, sid = (app.state.agent, session(app))
    task = draft(app, sid)
    with app.state.store.db() as db:
        # 创建缺失表结构；IF NOT EXISTS不会清空已有数据
        db.execute("CREATE TRIGGER reject_update BEFORE UPDATE OF state ON tasks BEGIN SELECT RAISE(ABORT,'test rollback'); END")
    with pytest.raises(sqlite3.Error):
        agent.decide(sid, task['id'], task['version'], '确认')
    with app.state.store.db() as db:
        assert db.execute('SELECT count(*) FROM tickets').fetchone()[0] == 0
    assert agent.task(sid, task['id'])['state'] == 'awaiting_confirmation'

# 工具名称与参数白名单校验，同时覆盖知识工具的本地检索结果。
def test_tool_schema_unknown_and_knowledge(app):
    agent = app.state.agent
    with pytest.raises(AppError):
        agent.read_tool('query_ticket', {'order_id': 'A001'})
    with pytest.raises(AppError):
        agent.read_tool('delete_all', {})
    assert agent.read_tool('query_order', {'order_id': 'A999'})['found'] is False
    assert agent.read_tool('query_service', {'service_id': 'P001'})['service']['price'] is None
    # 按当前配置解析并建立该文档的检索索引
    app.state.rag.ingest('操作.md', '# 连接排查\n无法连接时先检查客户端网络。'.encode())
    assert agent.chat(session(app), '连接排查操作指南')['steps'][0]['result']['sources']

class InvalidProvider(Provider):

    def chat(self, messages, tools=None, tool_choice='auto', json_mode=False):
        return {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'bad', 'type': 'function', 'function': {'name': 'query_order', 'arguments': '{"order_id":"A001"}'}}]}

# 模型猜出的编号不是用户提供的编号，不能据此查询。
def test_live_guard_blocks_guessed_identifier(tmp_path):
    app = create_app(Config(data=tmp_path, mode='live'), InvalidProvider)
    agent, sid = (app.state.agent, session(app))
    with pytest.raises(AppError, match='未提供'):
        agent.chat(sid, '查订单支付情况')
    assert agent.get_session(sid)['busy_until'] == 0

# 用超时替身验证单次失败停止与事件记录，不产生真实网络费用。
def test_request_failure_no_retry_and_trace(app):
    config = Config(data=app.state.store.config.data, mode='live', api_key='offline-test-placeholder')
    provider = Provider(config)
    import requests
    with patch('suite.core.requests.post', side_effect=requests.Timeout('offline')) as mocked:
        with pytest.raises(AppError, match='超时'):
            provider.chat([{'role': 'user', 'content': '测试'}])
        # 新实现对超时重试一次，因此这里是2次调用
        assert mocked.call_count == 2
    assert provider.events[0]['status'] == 'failed'

# 通过HTTP验证任务版本/决定接口及执行详情的返回契约。
def test_http_task_and_trace_contract(app):
    client = TestClient(app)
    assert client.get('/rag').status_code == 404
    assert client.get('/agent').status_code == 200
    assert client.get('/static/app.js').status_code == 200
    sid = client.post('/api/agent/sessions').json()['session_id']
    prefix = '/api/agent/sessions/' + sid + '/tasks'
    task = client.post(prefix, json={'text': 'A001云服务器无法连接', 'request_id': 'http1'}).json()
    result = client.post(prefix + '/' + task['id'] + '/decision', json={'version': task['version'], 'decision': '确认'}).json()
    assert result['ticket']['status'] == '待处理'
    trace = client.get('/api/traces/' + result['trace_id']).json()
    assert trace['project'] == 'agent.decide' and trace['elapsed_ms'] >= 0
    assert client.post(prefix, json={'text': 'test', 'request_id': 'x', 'confirmed': True}).status_code == 422

# 编号必须有完整边界，不能把更长字符串里的子串当作授权标识。
def test_identifier_substring_is_not_authorization(tmp_path):
    app = create_app(Config(data=tmp_path, mode='live'), InvalidProvider)
    with pytest.raises(AppError, match='未提供'):
        app.state.agent.chat(session(app), '查订单A0011')

# 末轮的工具提议不执行，SSE 仍完成并持久保存可继续对话的结果。
def test_model_loop_limit_returns_partial_and_releases_lock(tmp_path):
    app = create_app(Config(data=tmp_path, mode='live'), InvalidProvider)
    sid = session(app)
    client = TestClient(app)
    response = client.post(f'/api/agent/sessions/{sid}/chat/stream', json={'text': '查询订单A001'})
    assert response.status_code == 200
    events = [json.loads(line.removeprefix('data: ')) for line in response.text.splitlines() if line.startswith('data: ')]
    assert [event['type'] for event in events] == ['status', 'tool', 'tool', 'tool', 'delta', 'done']
    result = events[-1]['result']
    assert result['partial'] and '已支付' in result['answer']
    stored = app.state.agent.get_session(sid)
    assert stored['busy_until'] == 0
    assert json.loads(stored['messages'])[-1] == {'role': 'assistant', 'content': result['answer']}
    with app.state.store.db() as db:
        trace = json.loads(db.execute("SELECT payload FROM traces WHERE project='agent.chat'").fetchone()[0])
    assert trace['partial'] and len(trace['steps']) == 3


def test_partial_knowledge_answer_keeps_verified_citation(tmp_path):
    class RepeatingKnowledgeProvider(Provider):
        def chat(self, messages, tools=None, tool_choice='auto', json_mode=False):
            return {'role': 'assistant', 'tool_calls': [{'id': 'knowledge', 'type': 'function',
                    'function': {'name': 'search_knowledge', 'arguments': '{"question":"如何排查连接"}'}}]}

    app = create_app(Config(data=tmp_path, mode='live'), RepeatingKnowledgeProvider)
    agent = app.state.agent
    with patch.object(agent, 'read_tool', return_value={'sources': [{'id': 'a' * 16, 'text': '先检查网络。'}]}) as read:
        result = agent.chat(session(app), '如何排查连接')
    assert read.call_count == 3
    assert result['partial'] and ('先检查网络。 [' + 'a' * 16 + ']') in result['answer']


def test_close_chat_after_first_event_releases_lock(app):
    sid = session(app)
    stream = app.state.agent.chat_events(sid, '查询订单A001')
    assert next(stream)['type'] == 'status'
    stream.close()
    assert app.state.agent.get_session(sid)['busy_until'] == 0

# 没有实际search_knowledge不能声称已依据知识库作答。
def test_knowledge_claim_without_search_blocked(tmp_path):

    class Unsupported(Provider):

        def chat(self, *args, **kwargs):
            return {'role': 'assistant', 'content': '根据知识库说明，系统正常。'}
    app = create_app(Config(data=tmp_path, mode='live'), Unsupported)
    with pytest.raises(AppError, match='没有检索'):
        app.state.agent.chat(session(app), '服务说明')

# 不存在和数据库读取失败是两种情况，失败不能伪装成未找到。
def test_unknown_ticket_distinct_from_read_error(app):
    value = 'T-' + '0' * 32
    assert app.state.agent.read_tool('query_ticket', {'ticket_id': value})['found'] is False
    with app.state.store.db() as db:
        # 执行数据库语句，具体字段和约束见下方多行SQL
        db.execute('ALTER TABLE tickets RENAME TO tickets_unavailable')
    with pytest.raises(sqlite3.Error):
        app.state.agent.read_tool('query_ticket', {'ticket_id': value})

# 通过独立解释器恢复持久任务，避免只验证同进程内存。
def test_external_process_task_recovery(app):
    import subprocess
    agent, sid = (app.state.agent, session(app))
    task = draft(app, sid)
    code = 'from suite.app import create_app; from suite.core import Config; from pathlib import Path; import sys,json; a=create_app(Config(data=Path(sys.argv[1]))); print(json.dumps(a.state.agent.task(sys.argv[2],sys.argv[3])))'
    completed = subprocess.run([sys.executable, '-c', code, str(app.state.store.config.data), sid, task['id']], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
    recovered = json.loads(completed.stdout)
    assert recovered['version'] == task['version'] and recovered['state'] == 'awaiting_confirmation'

# 有明确编号的业务结论必须取得本轮工具依据，不能让模型直接猜。
def test_business_answer_without_tool_rejected(tmp_path):
    class Ungrounded(Provider):
        def chat(self, *args, **kwargs):
            return {'role': 'assistant', 'content': '订单A001已支付。'}
    instance = create_app(Config(data=tmp_path, mode='live'), Ungrounded)
    with pytest.raises(AppError, match='查询依据'):
        instance.state.agent.chat(session(instance), '查询订单A001的支付状态')

# 缺编号时也不能编造已支付等状态，只能追问所需信息。
def test_missing_identifier_fabricated_status_rejected(tmp_path):
    class Ungrounded(Provider):
        def chat(self, *args, **kwargs):
            return {'role': 'assistant', 'content': '订单已支付。'}
    instance = create_app(Config(data=tmp_path, mode='live'), Ungrounded)
    with pytest.raises(AppError, match='查询依据'):
        instance.state.agent.chat(session(instance), '帮我查订单支付状态')

# 离线回归：用临时存储与必要的模型替身验证边界；通过测试不等于真实模型准确率。

import json
import pytest
from suite.app import create_app
from suite.core import Config, Provider, AppError

# 真实模式替身覆盖有效引用格式、缺失引用和未知来源ID。
@pytest.mark.parametrize('kind', ['prefixed', 'missing', 'unknown'])
def test_knowledge_reference_format_and_evidence_guard(tmp_path, kind):
    class Fake(Provider):
        def embed(self, texts):
            return Provider(Config()).embed(texts)
        def chat(self, messages, tools=None, tool_choice='auto', json_mode=False):
            if messages[-1]['role'] != 'tool':
                return {'role':'assistant','content':'','tool_calls':[{'id':'call_test','type':'function',
                    'function':{'name':'search_knowledge','arguments':json.dumps({'question':'连接排查'})}}]}
            source = json.loads(messages[-1]['content'])['sources'][0]
            ref = 'ffffffffffffffff' if kind == 'unknown' else source['id']
            return {'role':'assistant','content':'检查客户端网络。' + ('' if kind == 'missing' else '[ id: ' + ref + ' ]')}
    app = create_app(Config(data=tmp_path,mode='live'),Fake)
    # 按当前配置解析并建立该文档的检索索引
    app.state.rag.ingest('操作.md','连接排查：检查客户端网络。'.encode())
    agent=app.state.agent;sid=agent.session()
    if kind != 'prefixed':
        with pytest.raises(AppError,match='引用'): agent.chat(sid,'如何连接排查')
    else:
        value=agent.chat(sid,'如何连接排查')
        assert '[ id:' not in value['answer']
        assert value['messages'][-1]['content'] == value['answer']

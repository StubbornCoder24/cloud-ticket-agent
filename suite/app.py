# HTTP接口层：校验网页输入、调用业务对象并把结果返回前端，不在接口里重复实现业务流程。
# create_app可注入测试配置和模型替身，因此离线测试不必使用真实密钥或业务库。

import json
import hmac
import os
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Request, Header, Depends
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from .core import Config, Store, AppError, ROOT
from .rag import RAG
from .agent import Agent

# 写接口鉴权：设置了APP_TOKEN才启用；未设置时保持本地开发的开放行为。
def require_token(x_api_key: str = Header(default='')):
    expected = os.getenv('APP_TOKEN', '')
    if expected and not hmac.compare_digest(x_api_key or '', expected):
        raise AppError('未授权：写操作需要正确的 X-API-Key', 401)
    return True

# 请求字段白名单：拒绝多余字段，避免把客户端未约定数据直接带进业务执行。
class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')

# 咨询文本入参；客户端不能直接指定模型工具执行结果。
class Chat(Body):
    text: str = Field(min_length=1, max_length=4000)

# 草稿请求额外携带request_id，用于重复提交识别。
class Draft(Chat):
    request_id: str = Field(min_length=1, max_length=100)

# 只接收当前版本及可编辑字段，状态不能由前端随意指定。
class Edit(Body):
    version: str
    order_id: str = Field(max_length=20)
    issue: str = Field(max_length=4000)

# 确认/取消提交版本号，后端以数据库草稿为准，不信任另传报障内容。
class Decision(Body):
    version: str
    decision: str

# 回答反馈入参：关联执行记录，而非直接改写原回答。
class Feedback(Body):
    trace_id: str
    rating: int = Field(ge=0, le=1)
    comment: str = Field(default='', max_length=1000)

# 应用工厂：为当前配置构造存储、检索和可选Agent对象，再注册路由与静态页面。
def create_app(config=None, provider_factory=None):
    config = config or Config.env()
    store = Store(config)
    kwargs = {'provider_factory': provider_factory} if provider_factory else {}
    rag = RAG(store, **kwargs)
    agent = Agent(store, rag, **kwargs)
    app = FastAPI(title='云服务工单Agent', version='1.0.0')
    app.state.store, app.state.rag, app.state.agent = (store, rag, agent)

    # 把已知业务错误变成可读响应；不把错误当成正常“查无记录”。
    @app.exception_handler(AppError)
    def application_error(request, error):
        return JSONResponse({'error': str(error)}, status_code=error.status)

    # 数据库异常保留类型/路径追踪，向页面提示操作未完成，不泄露底层SQL细节。
    @app.exception_handler(sqlite3.Error)
    def database_error(request, error):
        trace_id = store.trace('api.database', None, 'failed', time.perf_counter(), {'path': request.url.path, 'type': type(error).__name__})
        return JSONResponse({'error': '数据库操作未完成，请核对已有任务后再操作', 'trace_id': trace_id}, status_code=503)

    # 给前端显示真实运行模式与模型信息；只返回密钥是否配置，不返回密钥本身。
    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'mode': config.mode, 'embedding_backend': rag.backend, 'model': config.model if config.mode == 'live' else '离线确定性演示（非大模型）', 'model_key_configured': bool(config.api_key), 'data_type': '自编模拟业务'}

    # 列出当前模式可管理的文档，具体查询逻辑交给RAG。
    @app.get('/api/rag/documents')
    def documents():
        return rag.documents()

    # 仅读取大小上限加1字节以检测超限；无论入库成败都关闭上传文件句柄。
    @app.post('/api/rag/documents', dependencies=[Depends(require_token)])
    def upload(file: UploadFile=File(...)):
        try:
            content = file.file.read(15 * 1024 * 1024 + 1)
            return rag.ingest(file.filename, content)
        finally:
            # 关闭资源，释放连接或文件占用
            file.file.close()

    # 删除指定文档及其索引；不按模型回答中的名字猜测删除对象。
    @app.delete('/api/rag/documents/{doc_id}', dependencies=[Depends(require_token)])
    def delete(doc_id: str):
        rag.delete(doc_id)
        return {'deleted': True}

    # 新建持久会话，旧会话仍可按其编号恢复。
    @app.post('/api/agent/sessions')
    def new_session():
        return {'session_id': agent.session()}

    # 读取该会话消息和任务列表，给前端恢复上下文。
    @app.get('/api/agent/sessions/{session_id}')
    def get_session(session_id: str):
        session = agent.get_session(session_id)
        with store.db() as db:
            tasks = [dict(r) for r in db.execute('SELECT * FROM tasks WHERE session_id=? ORDER BY created DESC', (session_id,))]
        return {'session_id': session_id, 'messages': json.loads(session['messages']), 'tasks': tasks}

    # 咨询入口只进入工具读取循环，不绕过人工确认直接写工单。
    @app.post('/api/agent/sessions/{session_id}/chat')
    def chat(session_id: str, body: Chat):
        return agent.chat(session_id, body.text)

    # 流式咨询：边执行工具边推送进度，前端不必等整轮结束才有反馈。
    @app.post('/api/agent/sessions/{session_id}/chat/stream')
    def chat_stream(session_id: str, body: Chat):
        def events():
            try:
                with closing(agent.chat_events(session_id, body.text)) as stream:
                    for event in stream:
                        yield 'data: ' + json.dumps(event, ensure_ascii=False) + '\n\n'
            except Exception as error:
                # 响应头已发出，失败只能用 error 事件通知前端
                message = str(error) if isinstance(error, AppError) else '本次处理未完成，请查看运行记录'
                yield 'data: ' + json.dumps({'type': 'error', 'message': message}, ensure_ascii=False) + '\n\n'

        return StreamingResponse(events(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    # 提取并保存任务草稿；本接口成功不代表已经创建工单。
    @app.post('/api/agent/sessions/{session_id}/tasks', dependencies=[Depends(require_token)])
    def draft(session_id: str, body: Draft):
        return agent.draft(session_id, body.request_id, body.text)

    # 读取指定会话的最新任务状态与版本。
    @app.get('/api/agent/sessions/{session_id}/tasks/{task_id}')
    def task(session_id: str, task_id: str):
        return agent.task(session_id, task_id)

    # 保存草稿新版本，修改与状态转移由Agent统一检查。
    @app.patch('/api/agent/sessions/{session_id}/tasks/{task_id}', dependencies=[Depends(require_token)])
    def edit(session_id: str, task_id: str, body: Edit):
        return agent.edit_task(session_id, task_id, body.version, body.order_id, body.issue)

    # 用户确认/取消的唯一业务入口，数据库事务与幂等逻辑在Agent中。
    @app.post('/api/agent/sessions/{session_id}/tasks/{task_id}/decision', dependencies=[Depends(require_token)])
    def decide(session_id: str, task_id: str, body: Decision):
        return agent.decide(session_id, task_id, body.version, body.decision)

    # 列出最近执行摘要，供页面定位耗时与失败；原始payload由详情接口按ID读取。
    @app.get('/api/traces')
    def traces():
        with store.db() as db:
            return [dict(row) for row in db.execute('SELECT id,project,session_id,created,status,elapsed_ms FROM traces ORDER BY created DESC LIMIT 100')]

    # 按明确trace_id读取原始证据，用于核对参数、工具结果、来源和usage。
    @app.get('/api/traces/{trace_id}')
    def trace(trace_id: str):
        with store.db() as db:
            row = db.execute('SELECT * FROM traces WHERE id=?', (trace_id,)).fetchone()
        if not row:
            raise AppError('记录不存在', 404)
        result = dict(row)
        result['payload'] = json.loads(result['payload'])
        return result

    # 确认执行记录存在再保存评分，保持反馈与实际回答可追溯。
    @app.post('/api/feedback')
    def feedback(body: Feedback):
        with store.db() as db:
            if not db.execute('SELECT id FROM traces WHERE id=?', (body.trace_id,)).fetchone():
                raise AppError('记录不存在', 404)
            db.execute('INSERT INTO feedback VALUES(?,?,?,?,?)', (uuid.uuid4().hex, body.trace_id, body.rating, body.comment, time.time()))
        return {'saved': True}
    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')

    # 返回本项目静态首页，脚本通过API与后端交互，不需要把密钥放到浏览器。
    @app.get('/')
    @app.get('/agent')
    def page():
        return FileResponse(ROOT / 'static' / 'index.html')
    return app

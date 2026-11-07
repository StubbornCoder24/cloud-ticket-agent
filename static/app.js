// 页面业务编排：响应用户操作、更新当前视图；数据库和最终校验以后端结果为准。

let session = localStorage.getItem('cloud-agent-session') || '', task = null;
let draftRequest = null, draftText = null;
const welcomeContent = $('chat-welcome').cloneNode(true);
const stateLabels = {collecting:'待补充信息', awaiting_confirmation:'等待人工确认', created:'已创建工单', cancelled:'已取消'};
// 统一处理按钮忙碌和异常；操作结束后恢复可用状态，Agent还需重新应用任务终态限制。
async function guard(button, action) {button.disabled = true; try {await action();} catch(error) {notice(error.message);} finally {button.disabled = false; if(task) syncTaskButtons();}}
// 以服务端任务状态决定可操作按钮，created/cancelled不能被通用解禁逻辑重新启用。
function syncTaskButtons() {
  if(!task) return;
  const terminal = ['created','cancelled'].includes(task.state);
  // 设置按钮/输入框能否操作：$('confirm-task').disabled
  $('confirm-task').disabled = terminal || task.state !== 'awaiting_confirmation';
  // 设置按钮/输入框能否操作：$('cancel-task').disabled
  $('cancel-task').disabled = terminal;
  // 设置按钮/输入框能否操作：$('save-task').disabled
  $('save-task').disabled = terminal;
}
// 创建一条会话消息及工具依据；引用定位只在该消息的来源范围内查找。
function message(role, content, steps = []) {
  if (!content) return null;
  $('chat-welcome')?.remove();
  const row = textNode('div', '', 'message ' + role), avatar = textNode('span', '', 'message-avatar');
  avatar.append(icon('cloud')); const block = textNode('div', '', 'message-content');
  const rich = textNode('div', '', 'rich-text');
  const messageId = 'message-' + crypto.randomUUID();
  block.append(textNode('div', role === 'user' ? '你' : '云序助手', 'message-label'), rich);
  richText(rich, content, id => {
    const target = document.getElementById(messageId + '-' + id);
    if (target) {
      target.closest('.message-tools').open = true;
      target.open = true;
      target.scrollIntoView({behavior:'smooth', block:'nearest'});
    }
    else notice('可在本次运行记录中核对引用片段 ' + id);
  });
  if (steps.length) {
    const tools = textNode('details', '', 'message-tools');
    tools.append(textNode('summary', '本次依据 · ' + steps.length + ' 次工具读取'));
    for (const step of steps) {
      const name = step.call?.function?.name;
      const names = {query_order:'订单查询', query_service:'服务查询', query_ticket:'工单查询', search_knowledge:'操作知识检索'};
      tools.append(textNode('div', names[name] || name || '工具读取', 'tool-label'));
      for (const source of step.result?.sources || []) {
        const detail = document.createElement('details'); detail.className = 'inline-source'; detail.id = messageId + '-' + source.id;
        detail.append(textNode('summary', source.name + ' · ' + (source.page ? '第'+source.page+'页' : source.section || '正文')),
          textNode('div', source.text, 'source-text'));
        tools.append(detail);
      }
    }
    block.append(tools);
  }
  row.append(avatar, block); $('messages').append(row); $('messages').scrollTop = $('messages').scrollHeight;
  return row;
}
function demoAnswer(result) {
  if (!(result.mode === 'demo' && result.answer.startsWith('离线演示：工具实际返回'))) return result.answer;
  const step = result.steps.at(-1), data = step?.result;
  if (!data) return result.answer;
  if (data.found === false) return '没有找到对应的记录。请核对编号后再查询。';
  if (data.order) return '**订单 ' + data.order.order_id + '**\n支付状态：**' + data.order.status + '**\n关联服务：' + data.order.service_id + '\n\n以上为本地模拟订单查询结果。';
  if (data.service) return '**' + data.service.name + '**\n服务编号：' + data.service.service_id + '\n价格：' + (data.service.price == null ? '资料未提供，无法确定。' : data.service.price) + '\n\n本项目支持的演示操作：\n' + (data.service.demo_support || []).map(item => '- ' + item).join('\n');
  if (data.ticket) return '**工单查询结果**\n工单编号：`' + data.ticket.id + '`\n处理状态：**' + data.ticket.status + '**\n关联订单：' + data.ticket.order_id + '\n问题描述：' + data.ticket.issue;
  return result.answer;
}
// 完整session_id用于恢复，短编号只供界面展示，不是登录认证。
function updateSessionLabel() {
  $('session-id').value = session; $('session-short').textContent = session ? session.slice(0, 8) + '…' : '尚未创建';
}
// 清空当前编辑视图，不删除数据库中的历史任务。
function resetTaskEditor() {
  task = null; $('task-editor').hidden = true;
  document.querySelectorAll('[data-step]').forEach(step => {step.classList.remove('done','current'); if(step.dataset.step==='1') step.classList.add('current');});
}
function renderTask(value) {
  task = value; $('task-editor').hidden = false;
  $('task-state').textContent = stateLabels[task.state] || task.state; $('task-state').dataset.state = task.state;
  $('task-meta').textContent = '任务 ' + task.id + '\n版本 ' + task.version;
  $('task-order').value = task.order_id; $('task-issue').value = task.issue;
  const terminal = ['created','cancelled'].includes(task.state);
  // 设置按钮/输入框能否操作：$('task-order').disabled
  $('task-order').disabled = terminal; $('task-issue').disabled = terminal;
  const progress = task.state === 'created' ? 4 : task.state === 'awaiting_confirmation' ? 2 : 1;
  document.querySelectorAll('[data-step]').forEach(step => {
    const value = Number(step.dataset.step); step.classList.toggle('done', value < progress || task.state === 'created');
    step.classList.toggle('current', value === progress && !terminal);
  });
  // 替换容器现有内容，避免保留上次结果（$('task-result').replaceChildren）
  $('task-result').replaceChildren();
  if (task.ticket_id) {
    const card = textNode('div', '', 'ticket-card');
    card.append(textNode('strong', '工单已保存'), textNode('span', '关联工单编号'), textNode('code', task.ticket_id), textNode('span', '本地模拟工单；实际状态请在左侧查询。'));
    const copy = textNode('button', '', 'btn ghost'); copy.type = 'button'; copy.append(icon('copy','small'), document.createTextNode('复制编号'));
    copy.onclick = () => copyText(task.ticket_id, '工单编号已复制。'); card.append(copy); $('task-result').append(card);
  } else if (task.state === 'cancelled') {
    $('task-result').append(textNode('div', '本次任务已取消，没有创建工单。', 'status-box'));
  }
  syncTaskButtons();
}
// 点击历史项重新从后端读取，避免用过期列表快照确认；会话切换后不覆盖新视图。
async function loadTask(taskId) {
  if (!session) return;
  const activeSession = session;
  const value = await api(`/api/agent/sessions/${activeSession}/tasks/${taskId}`);
  if (session === activeSession) renderTask(value);
}
// 历史列表只负责导航，实际编辑内容由loadTask读取最新版本。
function renderTaskList(rows) {
  // 替换容器现有内容，避免保留上次结果（$('task-list').replaceChildren）
  $('task-list').replaceChildren(); $('task-count').textContent = rows.length;
  if (!rows.length) $('task-list').append(textNode('div', '当前会话还没有已保存任务。', 'empty-small'));
  for (const value of rows) {
    const button = textNode('button', '', 'task-history-button'); button.type = 'button';
    button.append(textNode('span', stateLabels[value.state] || value.state), textNode('code', value.id.slice(0, 10)));
    button.onclick = () => guard(button, () => loadTask(value.id)); $('task-list').append(button);
  }
}
// 异步请求绑定发起时的会话，防止旧结果写入新会话界面。
async function refreshTaskList() {
  if (!session) return;
  const activeSession = session, value = await api('/api/agent/sessions/' + activeSession);
  if (session === activeSession) renderTaskList(value.tasks || []);
}
// 业务已完成但列表刷新失败时只提示恢复方法，不能因此宣称建单失败。
async function safeRefreshTasks() {try {await refreshTaskList();} catch (_) {notice('任务操作已完成，历史列表暂时无法刷新；可稍后恢复会话查看。');}}
// 新建后端会话，保存恢复编号并重置当前草稿视图；不删除旧会话。
async function newSession() {
  const value = await post('/api/agent/sessions', {}); session = value.session_id;
  localStorage.setItem('cloud-agent-session', session); updateSessionLabel();
  draftRequest = null; draftText = null; resetTaskEditor(); renderTaskList([]);
  showWelcome();
}
// 首次咨询或草稿操作前创建会话，避免把任务保存到空会话。
async function ensureSession() {if (!session) await newSession();}
// 恢复欢迎区并重新绑定建议按钮；建议只填输入，不自动发送请求。
function showWelcome() {
  // 替换容器现有内容，避免保留上次结果（$('messages').replaceChildren）
  $('messages').replaceChildren(welcomeContent.cloneNode(true)); bindPromptButtons();
}
// 按tool_call_id重建模型调用与实际结果的对应关系，再显示历史回答依据。
function renderHistory(items) {
  const calls = new Map(); let steps = [];
  for (const item of items) {
    if (item.role === 'user') {steps = []; message('user', item.content);}
    if (item.role === 'assistant') {
      for (const call of item.tool_calls || []) calls.set(call.id, call);
      if (item.content) {message('assistant', demoAnswer({mode:uiMode, answer:item.content, steps}), steps); steps = [];}
    }
    if (item.role === 'tool') {
      try {steps.push({call:calls.get(item.tool_call_id), result:JSON.parse(item.content)});} catch (_) { /* 原始异常内容仍可在执行记录中查看。 */ }
    }
  }
}
// 按明确编号加载持久会话、消息和任务；旧浏览器编号可能属于另一运行数据目录。
async function restore() {
  const requested = $('session-id').value.trim();
  if (!requested) return notice('请先填写需要恢复的会话编号。');
  const value = await api('/api/agent/sessions/' + requested);
  session = requested; localStorage.setItem('cloud-agent-session', session); updateSessionLabel();
  // 替换容器现有内容，避免保留上次结果（$('messages').replaceChildren）
  $('messages').replaceChildren();
  renderHistory(value.messages);
  if (!$('messages').children.length) showWelcome();
  renderTaskList(value.tasks || []); draftRequest = null; draftText = null;
  if (value.tasks?.length) renderTask(value.tasks[0]); else resetTaskEditor();
  $('messages').scrollTop = $('messages').scrollHeight;
}
$('new-session').onclick = () => guard($('new-session'), newSession);
$('session-form').onsubmit = event => {event.preventDefault(); guard($('restore'), restore);};
$('copy-session').onclick = () => copyText(session, '会话编号已复制，可用于重启后恢复。');
// 咨询发送绑定当前会话；响应晚到且已切会话时，不把旧结果塞进新会话。
$('chat-form').onsubmit = event => {
  // 阻止表单默认跳转，由脚本处理提交（event.preventDefault）
  event.preventDefault(); const text = $('chat-text').value.trim(); if (!text) return notice('先输入你想咨询的问题。');
  guard($('chat-submit'), async () => {
    await ensureSession(); const activeSession = session;
    message('user', text); $('chat-text').value = ''; $('chat-text').dispatchEvent(new Event('input'));
    const pending = textNode('div', '', 'message pending'); const block = textNode('div', '', 'message-content');
    loading(block, '正在处理你的问题…'); pending.append(block); $('messages').append(pending); $('messages').scrollTop = $('messages').scrollHeight;
    setButtonLabel($('chat-submit'), 'refresh', '处理中…');
    try {
      // 流式请求：工具执行时就能看到进度，不必等整轮结束
      const response = await fetch('/api/agent/sessions/' + activeSession + '/chat/stream', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text}),
      });
      if (!response.ok || !response.body) throw new Error('流式接口不可用，请刷新重试');
      const reader = response.body.getReader(); const decoder = new TextDecoder();
      let buffer = '', result = null, failure = '';
      // 按空行切 SSE 帧，逐帧处理，避免半包被误解析
      while (true) {
        const {value, done} = await reader.read(); if (done) break;
        buffer += decoder.decode(value, {stream: true});
        const frames = buffer.split('\n\n'); buffer = frames.pop();
        for (const frame of frames) {
          if (!frame.startsWith('data: ')) continue;
          const event = JSON.parse(frame.slice(6));
          if (event.type === 'status') loading(block, event.message);
          else if (event.type === 'tool') loading(block, '已完成工具查询：' + event.name);
          else if (event.type === 'delta') loading(block, '正在整理回答…');
          else if (event.type === 'error') failure = event.message;
          else if (event.type === 'done') result = event.result;
        }
      }
      if (failure) throw new Error(failure);
      if (session === activeSession && result) message('assistant', demoAnswer(result), result.steps || []);
      else if (!result) throw new Error('本次处理未完成，请查看执行记录');
      else notice('该请求已保存在原会话，恢复原会话后可查看。');
    } catch (error) {
      if (session === activeSession) {const row = message('assistant', '本次查询未完成。'); errorBox(row.querySelector('.rich-text'), error.message);}
      notice(error.message);
    } finally {pending.remove(); setButtonLabel($('chat-submit'), 'send', '发送'); await safeRefreshTraces();}
  });
};
// 相同文本重试复用request_id，文本改变才新建请求；页面草稿仍需人工决定。
$('draft-form').onsubmit = event => {
  // 阻止表单默认跳转，由脚本处理提交（event.preventDefault）
  event.preventDefault(); const text = $('draft-text').value.trim(); if (!text) return notice('请描述遇到的故障。');
  guard($('draft-submit'), async () => {
    await ensureSession(); const activeSession = session;
    if (text !== draftText || !draftRequest) {draftText = text; draftRequest = crypto.randomUUID();}
    setButtonLabel($('draft-submit'), 'refresh', '正在整理…');
    try {
      const value = await post('/api/agent/sessions/' + activeSession + '/tasks', {text, request_id:draftRequest});
      if (session === activeSession) {renderTask(value); notice(value.state === 'collecting' ? '草稿已保存，请补充缺失信息。' : '草稿已保存，请核对后决定是否建单。'); await safeRefreshTasks();}
      else notice('草稿已保存在原会话，请恢复原会话查看。');
      await safeRefreshTraces();
    } finally {setButtonLabel($('draft-submit'), 'file', '整理报障草稿');}
  });
};
$('new-draft').onclick = () => {
  draftRequest = null; draftText = null; resetTaskEditor(); $('draft-text').value = ''; $('draft-text').focus();
  notice('可以整理新的草稿。已有任务仍保存在会话中。');
};
// 提交版本号与编辑字段；后端保存会递增版本，用户须重新核对后确认。
$('save-task').onclick = () => guard($('save-task'), async () => {
  if (!task) return;
  const activeSession = session, taskId = task.id;
  const value = await post(`/api/agent/sessions/${activeSession}/tasks/${taskId}`,
    {version:task.version, order_id:$('task-order').value, issue:$('task-issue').value}, 'PATCH');
  if (session === activeSession && task?.id === taskId) renderTask(value);
  notice('新版本已保存，请重新核对后确认。'); await safeRefreshTasks();
});
for (const [id, decision] of [['confirm-task','确认'],['cancel-task','取消']]) $(id).onclick = () => guard($(id), async () => {
  if (!task) return;
  if ($('task-order').value !== task.order_id || $('task-issue').value !== task.issue) throw new Error('有尚未保存的修改，请先保存并重新核对。');
  const activeSession = session, taskId = task.id;
  const result = await post(`/api/agent/sessions/${activeSession}/tasks/${taskId}/decision`, {version:task.version, decision});
  if (session === activeSession && task?.id === taskId) renderTask(result.task);
  notice(result.created ? '工单已创建，可复制编号查询状态。' : result.reused ? '已返回原工单，没有重复创建。' : '本次任务已取消，没有创建工单。');
  await safeRefreshTasks(); await safeRefreshTraces();
});
// 快捷问题只填入输入框，用户发送后才触发模型调用。
function bindPromptButtons() {
  document.querySelectorAll('[data-prompt]').forEach(button => {button.onclick = () => {
    $('chat-text').value = button.dataset.prompt; $('chat-text').dispatchEvent(new Event('input')); $('chat-text').focus();
  };});
}
bindPromptButtons();
bindComposer('chat-text','chat-count','chat-form');
(async () => {
  updateSessionLabel(); await commonInit();
  if (session) {
    try {await restore();}
    catch (error) {
      // 网络故障时保留编号；只有明确会话不存在时才提示用户自行新建。
      notice('原会话暂时未恢复：' + error.message + ' 可展开当前会话后重试。');
    }
  }
})().catch(error => notice(error.message));

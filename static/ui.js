// 界面公共工具：安全文本展示、资料上传、运行记录与交互状态，不执行模型HTML。

const $ = id => document.getElementById(id);
const show = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
let noticeTimer = null, uiMode = 'demo';
// 所有模型/文件内容先作为文本写入，避免把它们当成可信HTML执行。
function textNode(tag, text, cls) {
  const node = document.createElement(tag); node.textContent = text;
  if (cls) node.className = cls; return node;
}
// 引用本地SVG符号，图标不依赖外部资源或模型输出。
function icon(name, cls = '') {
  const node = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  // 设置DOM属性（node.setAttribute）
  node.setAttribute('class', 'icon ' + cls); node.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  // 设置DOM属性（use.setAttribute）
  use.setAttribute('href', '/static/icons.svg#' + name); node.append(use); return node;
}
// 页面轻提示，超时后清空；原始执行证据仍保存在后端轨迹中。
function notice(text) {
  clearTimeout(noticeTimer); $('notice').textContent = text;
  noticeTimer = setTimeout(() => {$('notice').textContent = '';}, 7000);
}
// 服务端启用 APP_TOKEN 后，写操作必须带 X-API-Key；口令只存在本机浏览器，不写进代码。
const apiKey = () => localStorage.getItem('app-token') || '';
function withKey(options = {}) {
  const headers = new Headers(options.headers || {});
  const key = apiKey();
  if (key) headers.set('X-API-Key', key);
  return {...options, headers};
}
// 访问口令控件：设置后每个请求都带上；未设置时保持本地开放行为。
function bindToken() {
  const input = $('app-token'), state = $('token-state');
  if (!input) return;
  const sync = () => {input.value = apiKey(); if (state) state.textContent = apiKey() ? '已设置' : '未设置';};
  sync();
  $('token-form').onsubmit = event => {
    event.preventDefault();
    localStorage.setItem('app-token', input.value.trim());
    sync();
    notice(apiKey() ? '访问口令已保存，后续请求会携带。' : '访问口令已清除。');
  };
}
// 统一JSON接口错误；HTTP失败或非JSON都变成页面可处理的异常。
async function api(path, options = {}) {
  let response;
  try {response = await fetch(path, withKey(options));} catch (_) {throw new Error('连接失败，请确认本地项目服务正在运行。');}
  let value;
  try {value = await response.json();} catch (_) {throw new Error('服务未返回有效结果，请稍后查看运行记录。');}
  if (!response.ok) {
    let detail = value.error || value.detail;
    if (Array.isArray(detail)) detail = detail.map(item => item.msg).join('；');
    // 抛出异常，由外层捕获并显示/记录失败，不继续当前正常流程
    throw new Error(typeof detail === 'string' ? detail : '本次操作未完成（HTTP ' + response.status + '）');
  }
  return value;
}
const post = (path, value, method = 'POST') => api(path, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(value)});
// 浏览器允许时复制，否则提示手动选取；没有有效结果时不复制空内容。
async function copyText(value, success = '已复制') {
  if (!value) return notice('当前没有可复制的内容。');
  try {await navigator.clipboard.writeText(value); notice(success);} catch (_) {notice('浏览器未允许复制，请手动选中文本复制。');}
}
// 替换按钮图标与文字，维持加载态和结束态的一致展示。
function setButtonLabel(button, name, label) {button.replaceChildren(icon(name, 'small'), document.createTextNode(label));}
// 有限的加粗/代码/引用解析，用DOM节点构造内容，不执行原始HTML。
function inlineText(container, text, onCitation) {
  const pattern = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[a-f0-9]{16}\])/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    container.append(document.createTextNode(text.slice(last, match.index)));
    const token = match[0];
    if (token.startsWith('**')) container.append(textNode('strong', token.slice(2, -2)));
    else if (token.startsWith('`')) container.append(textNode('code', token.slice(1, -1)));
    else {
      const id = token.slice(1, -1), button = textNode('button', id.slice(0, 4), 'citation');
      button.type = 'button'; button.title = '查看原文片段 ' + id; button.setAttribute('aria-label', '查看引用片段 ' + id);
      button.onclick = () => onCitation ? onCitation(id) : notice('可在本次执行记录中核对片段 ' + id);
      container.append(button);
    }
    last = match.index + token.length;
  }
  container.append(document.createTextNode(text.slice(last)));
}
// 逐行渲染有限Markdown；不是全语法解析器，完整引用可回到对应来源。
function richText(container, text, onCitation) {
  // 替换容器现有内容，避免保留上次结果（container.replaceChildren）
  container.replaceChildren(); let list = null, code = null;
  for (const raw of String(text || '').split('\n')) {
    const line = raw.trim();
    if (line.startsWith('```')) {
      if (code) code = null;
      else {const pre=textNode('pre','','code-block'); code=document.createElement('code'); pre.append(code);container.append(pre);}
      list=null;continue;
    }
    if(code){code.textContent+=raw+'\n';continue;}
    if (!line) {list = null; continue;}
    const bullet = line.match(/^(?:[-*•]\s+|\d+[.、]\s*)(.*)$/);
    if (bullet) {
      if (!list) {list = document.createElement('ul'); container.append(list);}
      const item = document.createElement('li'); inlineText(item, bullet[1], onCitation); list.append(item);
    } else {
      list = null; const heading = line.match(/^#{1,6}\s+(.*)$/);
      const block = document.createElement(heading ? 'h3' : 'p');
      inlineText(block, heading ? heading[1] : line, onCitation); container.append(block);
    }
  }
}
// 真实异步操作进行时显示等待提示，不伪造模型生成文字。
function loading(container, label) {
  // 替换容器现有内容，避免保留上次结果（container.replaceChildren）
  container.replaceChildren(); const row = textNode('div', '', 'loading');
  row.append(textNode('span', '', 'spinner'), document.createTextNode(label)); container.append(row);
}
// 错误展示与正文分开，避免把异常信息当作一段正常模型结论。
function errorBox(container, text) {
  // 替换容器现有内容，避免保留上次结果（container.replaceChildren）
  container.replaceChildren(); const row = textNode('div', '', 'status-box error');
  row.append(icon('info'), textNode('span', text)); container.append(row);
}
// 以约定上海时区显示时间；数据库中仍保存时间戳。
function dateLabel(seconds, time = false) {
  if (!Number.isFinite(seconds)) return '—';
  return new Date(seconds * 1000).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', month:'2-digit', day:'2-digit', ...(time ? {hour:'2-digit', minute:'2-digit'} : {})});
}
function durationLabel(ms) {return typeof ms === 'number' ? (ms >= 1000 ? (ms / 1000).toFixed(2) + ' s' : Math.round(ms) + ' ms') : '—';}
// 读取实际文档/片段统计并提供删除入口，数字不使用静态占位值。
async function documents() {
  const rows = await api('/api/rag/documents'); $('documents').replaceChildren();
  $('document-count').textContent = rows.length;
  $('doc-total').textContent = rows.length;
  $('chunk-total').textContent = rows.reduce((sum, row) => sum + Number(row.chunks || 0), 0);
  if (!rows.length) $('documents').append(textNode('div', '还没有资料，添加第一份吧。', 'empty-small'));
  for (const row of rows) {
    const card = textNode('div', '', 'document-card');
    const extension = row.name.split('.').pop().toUpperCase();
    const file = textNode('span', extension.slice(0, 3), 'file-icon');
    const content = textNode('div', '', 'document-content');
    content.append(textNode('div', row.name, 'document-name'), textNode('div', `v${row.version} · ${row.chunks} 个片段 · ${dateLabel(row.updated)}`, 'document-meta'));
    const button = textNode('button', '', 'icon-button danger'); button.type = 'button';
    button.title = '删除 ' + row.name; button.setAttribute('aria-label', '删除资料 ' + row.name); button.append(icon('trash'));
    button.onclick = () => guard(button, async () => {
      if (!confirm('删除“' + row.name + '”及其检索片段？')) return;
      await api('/api/rag/documents/' + row.id, {method:'DELETE'}); await documents();
      notice('资料及其索引已删除。'); await safeRefreshTraces();
    });
    card.append(file, content, button); $('documents').append(card);
  }
}
// 限制格式和大小、处理拖入，再提交multipart文件；后端仍会再次校验。
function bindUpload() {
  const input = $('file'), button = $('upload-submit'), drop = $('dropzone');
  // 文件选择只更新页面状态，点击上传后才解析与向量化。
  function updateFile() {
    const file = input.files[0];
    if (file && (file.size > 15 * 1024 * 1024 || !/\.(pdf|md|txt)$/i.test(file.name))) {
      input.value = ''; notice('请选择15 MB以内的PDF、Markdown或TXT文件。');
    }
    const selected = input.files[0]; button.disabled = !selected;
    // 切换区域的显示或隐藏：$('selected-file').hidden
    $('selected-file').hidden = !selected;
    $('selected-file').textContent = selected ? selected.name + ' · ' + (selected.size / 1024).toFixed(1) + ' KB' : '';
  }
  // 绑定事件处理函数，事件发生后才运行回调（input.addEventListener）
  input.addEventListener('change', updateFile);
  // 绑定事件处理函数，事件发生后才运行回调（drop.addEventListener）
  drop.addEventListener('dragover', event => {event.preventDefault(); drop.classList.add('dragging');});
  // 绑定事件处理函数，事件发生后才运行回调（drop.addEventListener）
  drop.addEventListener('dragleave', () => drop.classList.remove('dragging'));
  // 绑定事件处理函数，事件发生后才运行回调（drop.addEventListener）
  drop.addEventListener('drop', event => {
    // 阻止表单默认跳转，由脚本处理提交（event.preventDefault）
    event.preventDefault(); drop.classList.remove('dragging');
    const file = event.dataTransfer.files[0];
    if (file) {const transfer = new DataTransfer(); transfer.items.add(file); input.files = transfer.files; updateFile();}
  });
  $('upload').onsubmit = event => {
    // 阻止表单默认跳转，由脚本处理提交（event.preventDefault）
    event.preventDefault(); if (!input.files[0]) return;
    guard(button, async () => {
      setButtonLabel(button, 'refresh', '正在建立索引…');
      try {
        const form = new FormData(); form.append('file', input.files[0]);
        const result = await api('/api/rag/documents', {method:'POST', body:form});
        let info = result.unchanged ? '资料内容一致，无需重复处理。' : '资料已保存，当前版本 v' + result.version + '。';
        if (result.empty_pages?.length) info += ' 第' + result.empty_pages.join('、') + '页未提取到文字。';
        input.value = ''; updateFile(); await documents(); notice(info); await safeRefreshTraces();
      } finally {setButtonLabel(button, 'plus', '添加到知识库');}
    }).finally(() => {button.disabled = !input.files.length;});
  };
}
const traceLabels = {'rag.ingest':'资料入库', 'rag.answer':'知识问答', 'agent.chat':'服务咨询', 'agent.draft':'整理草稿', 'agent.decide':'确认 / 取消任务', 'api.database':'数据库操作'};
// 显示最近执行摘要，点击后读取完整详情；原始payload只按文本呈现。
async function refreshTraces() {
  const rows = await api('/api/traces'); $('traces').replaceChildren();
  if (!rows.length) $('traces').append(textNode('div', '完成一次提问或资料操作后，记录会出现在这里。', 'empty-small'));
  for (const row of rows) {
    const button = textNode('button', '', 'trace-row'); button.type = 'button';
    const title = textNode('span', traceLabels[row.project] || row.project);
    const status = textNode('span', row.status === 'completed' ? '已完成' : '未完成', 'trace-status ' + row.status);
    button.append(title, status, textNode('small', durationLabel(row.elapsed_ms) + ' · ' + dateLabel(row.created, true)));
    button.onclick = () => guard(button, async () => {
      const value = await api('/api/traces/' + row.id);
      $('trace-detail').textContent = show(value); $('trace-detail-wrap').hidden = false;
      $('trace-detail-wrap').open = true; $('trace-detail-wrap').scrollIntoView({behavior:'smooth', block:'nearest'});
    });
    $('traces').append(button);
  }
}
// 刷新轨迹失败不推翻已完成的业务结果，提示用户稍后重试查看。
async function safeRefreshTraces() {try {await refreshTraces();} catch (_) {notice('操作已完成，但暂时无法刷新运行记录。');}}
// 维护字数和Ctrl/Command+Enter快捷键，受提交按钮忙碌状态限制。
function bindComposer(id, countId, formId) {
  const input = $(id);
  // 绑定事件处理函数，事件发生后才运行回调（input.addEventListener）
  input.addEventListener('input', () => {$(countId).textContent = input.value.length + ' / 4000';});
  // 绑定事件处理函数，事件发生后才运行回调（input.addEventListener）
  input.addEventListener('keydown', event => {if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {event.preventDefault(); if (!$(formId).querySelector('button[type=submit]').disabled) $(formId).requestSubmit();}});
}
// 页面内导航与刷新绑定，不发送任何实际云服务业务请求。
function bindNavigation() {
  document.querySelectorAll('.nav-item').forEach(link => link.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active')); link.classList.add('active');
    const target = document.querySelector(link.getAttribute('href')); if (target?.tagName === 'DETAILS') target.open = true;
  }));
  $('refresh-traces').onclick = () => guard($('refresh-traces'), refreshTraces);
}
// 页面初始化独立读取健康状态、资料与轨迹，单个面板失败不阻止其他面板显示。
async function commonInit() {
  bindNavigation(); bindUpload(); bindToken();
  try {
    const value = await api('/api/health'); uiMode = value.mode;
    $('mode').textContent = value.mode === 'live' ? '真实模型模式' : '离线演示模式'; $('mode').dataset.state = value.mode;
    $('mode').title = value.mode === 'live' ? '本次使用真实模型接口' : '展示本地流程，不代表真实模型效果';
  } catch (error) {$('mode').textContent = '服务连接异常'; $('mode').dataset.state = 'error'; notice(error.message);}
  await Promise.allSettled([
    documents().catch(error => {$('documents').replaceChildren(textNode('div', '资料暂时无法读取', 'empty-small'));notice(error.message);}),
    refreshTraces().catch(() => {$('traces').replaceChildren(textNode('div', '运行记录暂时无法读取，请点击刷新。', 'empty-small'));})
  ]);
}

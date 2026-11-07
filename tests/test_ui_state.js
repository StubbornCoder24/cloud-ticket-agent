// 前端单元检查：模拟必要对象验证客户端行为，不启动服务或调用真实模型。

// 纯函数/按钮状态单元测试；不启动或控制浏览器，不替代页面点击验收。
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const functions = source.slice(source.indexOf('async function guard('), source.indexOf('function message('));
const buttons = Object.fromEntries(['confirm-task', 'cancel-task', 'save-task'].map(id => [id, {disabled:false}]));
const context = {task:{state:'awaiting_confirmation'}, $:id => buttons[id], notice:() => {}};
vm.createContext(context);
vm.runInContext(functions, context);
(async () => {
  for (const state of ['created', 'cancelled']) {
    context.task = {state:'awaiting_confirmation'};
    await context.guard(buttons['confirm-task'], async () => {context.task.state=state;context.syncTaskButtons();});
    // 断言本测试的预期行为：assert.equal(buttons['confirm-task'].disabled, true)
    assert.equal(buttons['confirm-task'].disabled, true);
    // 断言本测试的预期行为：assert.equal(buttons['cancel-task'].disabled, true)
    assert.equal(buttons['cancel-task'].disabled, true);
    // 断言本测试的预期行为：assert.equal(buttons['save-task'].disabled, true)
    assert.equal(buttons['save-task'].disabled, true);
  }
  context.task={state:'awaiting_confirmation'};
  await context.guard(buttons['save-task'], async () => {});
  // 断言本测试的预期行为：assert.equal(buttons['confirm-task'].disabled, false)
  assert.equal(buttons['confirm-task'].disabled, false);
  context.task={state:'collecting'};
  context.syncTaskButtons();
  // 断言本测试的预期行为：assert.equal(buttons['confirm-task'].disabled, true)
  assert.equal(buttons['confirm-task'].disabled, true);
  // 断言本测试的预期行为：assert.equal(buttons['save-task'].disabled, false)
  assert.equal(buttons['save-task'].disabled, false);
  console.log('按钮状态单元检查通过：已创建/已取消保持禁用，待确认和缺字段分别按状态启用。');
})().catch(error => {console.error(error);process.exitCode=1;});

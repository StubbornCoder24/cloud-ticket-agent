# 真实模式验证

本目录保存自编测试语料对应的真实模型评测与运行证据。所有示例输入均为模拟数据。

- 运行环境为 Python 3.12，依赖版本见仓库 requirements.txt。
- 模式为 live，向量模型为 text-embedding-v4，1024 维。
- 聊天与候选重排使用 qwen-plus，存储为 SQLite。
- 密钥由本机环境注入，数据库保存在本机独立 runtime 目录。
- 场景验证通过本机 HTTP API 和 SSE 执行，证据包含实际响应、模型 usage 和详细 traces。
- 工具调用记录可核对执行过程，引用 ID 校验不能代替逐句语义评审。

## 复现场景

设置真实模式环境并运行 python seed_demo.py 和 python serve.py --mode live 后，通过工作台或 API 执行以下流程。

1. 查询订单支付状态，补充 A001，经 SSE 返回已支付及查询工具结果。
2. 咨询云服务器连接排查，核对实际检索片段和回答引用。
3. 整理订单 A001 的报障草稿，明确确认后建单，再次确认应返回原工单。

订单与工单为本地模拟业务数据，真实模型负责工具选择与回复。TOOLS_API_BASE 保持空值。

live_traces_agent.json 包含上述场景的 API 响应、SSE 事件和详细 traces。调试过程中被拦截的失败尝试也保留，status 字段用于区分成功与失败。

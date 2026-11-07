"""离线功能演示：临时库跑通「查订单 → 整理草稿 → 确认建单 → 重复确认幂等 → 知识检索」。

不改动 runtime 里的演示数据；每一步都打印可核对的业务事实。
用法：python -X utf8 -m scripts.demo
"""

import tempfile
import time
from pathlib import Path

from suite.app import create_app
from suite.core import Config, ROOT


def step(title, text):
    print(f"\n▶ {title}")
    for line in str(text).splitlines():
        print("   " + line)


def main():
    with tempfile.TemporaryDirectory(prefix="demo-") as tmp:
        app = create_app(Config(data=Path(tmp)))
        # 用随包样例建立内部知识库，保证演示可复现
        for path in sorted((ROOT / "samples").glob("*.md")):
            app.state.rag.ingest(path.name, path.read_bytes())

        agent = app.state.agent
        session = agent.session()
        started = time.perf_counter()
        print(f"临时库已就绪（{tmp}），会话 {session[:8]}…")

        first = agent.chat(session, "查询订单支付状态")
        step("① 缺编号时先追问，不编造结果", first["answer"])

        second = agent.chat(session, "A001")
        step("② 补上编号后查订单", second["answer"])
        step("工具调用", ", ".join(item["call"]["function"]["name"] for item in second["steps"]))

        task = agent.draft(session, "demo-request-1", "订单A001云服务器无法连接")
        step("③ 只生成草稿，不建单", f"state={task['state']}\norder_id={task['order_id']}\nissue={task['issue']}")

        created = agent.decide(session, task["id"], task["version"], "确认")
        step("④ 人工确认后才创建工单", f"ticket={created['ticket']['id']}\ncreated={created['created']}")

        again = agent.decide(session, task["id"], task["version"], "确认")
        step("⑤ 重复确认保持幂等", f"reused={again['reused']}\n新增工单={again['created']}\n与首张相同={again['ticket']['id'] == created['ticket']['id']}")

        knowledge = agent.chat(session, "云服务器无法连接应该如何排查")
        step("⑥ 知识检索并附带片段引用", knowledge["answer"][:220])

        print(f"\n全部步骤用时 {time.perf_counter() - started:.2f}s；demo 模式不调用真实模型。")


if __name__ == "__main__":
    main()

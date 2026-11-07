"""Agent 行为评测：用 10 个场景检查工具调用、缺参追问、人工确认与幂等。

用法：
    python evaluation/eval_agent.py            # demo 模式，确定性流程
    APP_MODE=live python evaluation/eval_agent.py   # 真实模型，结果才有参考价值

每个场景独立会话，跑完输出通过率与失败明细。
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("APP_MODE", "demo")
os.environ.setdefault("APP_DATA", str(ROOT / "runtime_eval"))

from fastapi.testclient import TestClient  # noqa: E402
from suite.app import create_app  # noqa: E402

TOKEN_HEADER = {"X-API-Key": os.getenv("APP_TOKEN", "")} if os.getenv("APP_TOKEN") else {}


def ask(client, sid, text):
    return client.post(f"/api/agent/sessions/{sid}/chat", json={"text": text}).json()


def draft(client, sid, text, rid="r1"):
    return client.post(f"/api/agent/sessions/{sid}/tasks", json={"text": text, "request_id": rid}, headers=TOKEN_HEADER).json()


def decide(client, sid, task_id, version, decision="确认"):
    return client.post(f"/api/agent/sessions/{sid}/tasks/{task_id}/decision",
                       json={"version": version, "decision": decision}, headers=TOKEN_HEADER).json()


def run():
    client = TestClient(create_app())
    results = []

    def check(name, condition, detail=""):
        results.append({"name": name, "pass": bool(condition), "detail": detail})

    # 1. 完整编号能查到订单状态
    sid = client.post("/api/agent/sessions").json()["session_id"]
    out = ask(client, sid, "查订单支付状态，编号A001")
    tools = [s["call"]["function"]["name"] for s in out.get("steps", [])]
    check("完整编号查订单", "query_order" in tools, str(tools))

    # 2. 未知订单不能编造为已支付
    sid = client.post("/api/agent/sessions").json()["session_id"]
    out = ask(client, sid, "查订单A999的状态")
    check("未知订单不编造", "已支付" not in out.get("answer", ""), out.get("answer", "")[:40])

    # 3. 缺编号时追问而不是直接查询
    sid = client.post("/api/agent/sessions").json()["session_id"]
    out = ask(client, sid, "帮我查订单支付状态")
    check("缺编号先追问", not out.get("steps") and "请提供" in out.get("answer", ""), out.get("answer", "")[:40])

    # 4. 服务价格未知不能猜
    sid = client.post("/api/agent/sessions").json()["session_id"]
    out = ask(client, sid, "P001多少钱")
    check("价格未知不猜测", "免费" not in out.get("answer", ""), out.get("answer", "")[:40])

    # 5. 缺字段草稿停在 collecting
    sid = client.post("/api/agent/sessions").json()["session_id"]
    task = draft(client, sid, "服务器无法连接")
    check("缺字段草稿 collecting", task.get("state") == "collecting", task.get("state", ""))

    # 6. 完整草稿进入待确认
    sid = client.post("/api/agent/sessions").json()["session_id"]
    task = draft(client, sid, "订单A001云服务器无法连接")
    check("完整草稿待确认", task.get("state") == "awaiting_confirmation", task.get("state", ""))

    # 7. 确认后建单成功
    sid = client.post("/api/agent/sessions").json()["session_id"]
    task = draft(client, sid, "订单A001云服务器无法连接")
    out = decide(client, sid, task["id"], task["version"])
    check("确认后建单", out.get("created") is True and out.get("ticket"), str(out.get("created")))

    # 8. 重复确认不新增工单
    out2 = decide(client, sid, task["id"], task["version"])
    check("重复确认幂等", out2.get("created") is False, str(out2.get("created")))

    # 9. 取消不建单
    sid = client.post("/api/agent/sessions").json()["session_id"]
    task = draft(client, sid, "订单A001云服务器无法连接")
    out = decide(client, sid, task["id"], task["version"], "取消")
    check("取消不建单", out.get("ticket") is None, str(out.get("ticket")))

    # 10. 陈旧版本不能确认
    sid = client.post("/api/agent/sessions").json()["session_id"]
    task = draft(client, sid, "订单A001云服务器无法连接")
    client.patch(f"/api/agent/sessions/{sid}/tasks/{task['id']}",
                 json={"version": task["version"], "order_id": "A002", "issue": "无法上传"}, headers=TOKEN_HEADER)
    out = decide(client, sid, task["id"], task["version"])
    check("陈旧版本被拒", "error" in out, str(out)[:40])

    passed = sum(1 for r in results if r["pass"])
    print(f"\n场景数: {len(results)} | 通过: {passed} | 通过率: {passed / len(results):.0%}\n")
    for r in results:
        print(("✅" if r["pass"] else "❌") + " " + r["name"] + ("" if r["pass"] else " -> " + r["detail"]))

    out_path = ROOT / "evaluation" / "agent_report.json"
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump({"mode": os.environ.get("APP_MODE"), "passed": passed, "total": len(results), "results": results},
                  handle, ensure_ascii=False, indent=2)
    print(f"\n已写入 {out_path}")
    return passed == len(results)

    # 有场景失败时返回非零，CI 才能真的拦住回退。
    raise SystemExit(0 if passed == len(results) else 1)

if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)

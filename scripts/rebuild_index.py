"""换 embedding 模型后重建 Agent 内部知识库的向量索引。

片段文本仍在库里，不需要原始文件；重新算向量并更新 backend 标识。

用法：
    python -X utf8 -m scripts.rebuild_index --data runtime_live
"""

import argparse
import json
import time
from pathlib import Path

from suite.core import Config, Store
from suite.rag import RAG


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=None, help="数据目录，默认用 APP_DATA 或 runtime")
    args = parser.parse_args()

    config = Config.env()
    if args.data:
        config.data = Path(args.data)
    store = Store(config)
    rag = RAG(store)
    target = rag.backend
    provider = rag.provider_factory(config)

    with store.db() as db:
        documents = [dict(row) for row in db.execute("SELECT id,name FROM documents")]
    if not documents:
        print("库里没有文档。")
        return

    started = time.time()
    total = 0
    for document in documents:
        with store.db() as db:
            rows = [dict(row) for row in db.execute("SELECT id,text FROM chunks WHERE doc_id=?", (document["id"],))]
        if not rows:
            continue
        vectors = provider.embed([row["text"] for row in rows])
        with store.db() as db:
            for row, vector in zip(rows, vectors):
                db.execute("UPDATE chunks SET vector=? WHERE id=?", (json.dumps(vector), row["id"]))
            db.execute("UPDATE documents SET backend=?, version=version+1 WHERE id=?", (target, document["id"]))
        total += len(rows)
        print(f"  {document['name']}: {len(rows)} 块")

    print(f"\n完成：{len(documents)} 篇 / {total} 块，用时 {time.time() - started:.1f}s")
    print(f"当前 backend：{target}")


if __name__ == "__main__":
    main()

# 将本项目samples中的自编资料建立索引；可选PDF由命令行显式指定。
# live会把文本送至已配置模型服务；不会自动迁移其他项目的数据。

import argparse
from pathlib import Path

from suite.core import Config, Store, ROOT
from suite.rag import RAG

# 沿用当前模式和数据目录，逐份入库样例；同内容复用由ingest负责。
def main():
    parser = argparse.ArgumentParser()
    # 声明一个命令行选项及其默认值/限制，后面统一解析
    parser.add_argument("--pdf", type=Path, help="可选：读取本地真实文本PDF，保留物理页码")
    args = parser.parse_args()
    config = Config.env()
    rag = RAG(Store(config))
    print("索引模式：", config.mode, "；live会向模型服务发送文档片段。")
    existing = {document["name"] for document in rag.documents()}
    for path in (ROOT / "samples").glob("*.md"):
        # 已初始化的数据库继续使用原样例名，避免升级时重复索引。
        name = path.name
        if name == "cloud_support_guide.md" and name not in existing and "云服务操作说明.md" in existing:
            name = "云服务操作说明.md"
        print(rag.ingest(name, path.read_bytes()))
    if args.pdf:
        print(rag.ingest(args.pdf.name, args.pdf.read_bytes()))

# 仅当此文件作为入口直接运行时执行下面代码，被import时不启动
if __name__ == "__main__":
    main()

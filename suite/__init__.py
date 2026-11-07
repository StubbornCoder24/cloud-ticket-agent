# 包初始化：让本项目随带的PDF依赖可被导入；不启动服务，也不调用外部模型。

"""云服务工单Agent独立成品，携带内部知识检索组件。"""
import sys
from pathlib import Path

private_deps = Path(__file__).resolve().parent.parent / ".deps"
if private_deps.is_dir():
    sys.path.insert(0, str(private_deps))

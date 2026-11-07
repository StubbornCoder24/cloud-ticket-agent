# 命令行启动入口：解析模式/端口/地址，交给FastAPI与Uvicorn提供本地页面及接口。
# 默认demo；live会产生真实模型调用。仅运行此入口本身不会自动初始化知识样例。

import argparse
import os

# 解析启动参数后再导入应用，确保环境变量先于配置加载生效。
def main():
    parser = argparse.ArgumentParser(description="云服务工单Agent独立启动入口")
    # 声明一个命令行选项及其默认值/限制，后面统一解析
    parser.add_argument("--mode", choices=["demo", "live"], default=os.getenv("APP_MODE", "demo"))
    # 声明一个命令行选项及其默认值/限制，后面统一解析
    parser.add_argument("--port", type=int, default=8002)
    # 声明一个命令行选项及其默认值/限制，后面统一解析
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    os.environ["APP_MODE"] = args.mode
    from suite.app import create_app
    import uvicorn
    print("Agent页面：http://" + args.host + ":" + str(args.port) + "/agent")
    print("模式：", args.mode, "；运行数据保存在配置指定的目录。")
    # 启动HTTP服务处理页面/API请求，直到进程被停止
    uvicorn.run(create_app(), host=args.host, port=args.port)

# 仅当此文件作为入口直接运行时执行下面代码，被import时不启动
if __name__ == "__main__":
    main()

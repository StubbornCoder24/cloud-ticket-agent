# 交付回归入口：默认使用临时库跑离线测试并保存报告，避免污染运行数据。
# --live只针对自编样例真实调用；自动化中的确认是测试输入，不代表真实用户业务确认。

import argparse
import json
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from suite.app import create_app
from suite.core import Config, ROOT

PROJECT = 'agent'

# 建立唯一报告目录，先跑回归，再用临时库检查完整链路。
def main():
    parser = argparse.ArgumentParser()
    # 声明一个命令行选项及其默认值/限制，后面统一解析
    parser.add_argument('--live', action='store_true', help='使用本项目自编样例进行真实调用，有接口消耗')
    args = parser.parse_args()
    output = ROOT / 'evaluation' / ('standalone_' + uuid.uuid4().hex[:12])
    # 按需建立目录，已存在时按给定选项处理
    output.mkdir(parents=True)
    report = {'project': PROJECT, 'mode': 'live' if args.live else 'demo', 'started': time.time()}
    # 在成功或异常路径都保存本批回归报告，避免失败过程只留在控制台。
    def save():
        # 保存当前结果到指定文件，不修改原始输入文件
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        if not args.live:
            with tempfile.TemporaryDirectory(prefix='independent-check-') as temp:
                result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'pytest', 'tests', '-q', '--tb=short',
                    '-p', 'no:cacheprovider', '--basetemp', str(Path(temp) / 'pytest')], cwd=ROOT,
                    capture_output=True, text=True, encoding='utf-8')
            # 保存当前结果到指定文件，不修改原始输入文件
            (output / 'pytest.txt').write_text(result.stdout + result.stderr, encoding='utf-8')
            report['pytest_exit_code'] = result.returncode
            if result.returncode:
                raise RuntimeError('自动化检查未通过，见本批pytest.txt')
        with tempfile.TemporaryDirectory() as temp:
            config = Config.env()
            config.data, config.mode = Path(temp), 'live' if args.live else 'demo'
            app = create_app(config)
            for path in (ROOT / 'samples').glob('*.md'):
                # 按当前配置解析并建立该文档的检索索引
                app.state.rag.ingest(path.name, path.read_bytes())
            agent = app.state.agent
            sid = agent.session()
            report['chat'] = [agent.chat(sid, text) for text in ('查询订单支付状态', 'A001', '服务P001价格是多少')]
            task = agent.draft(sid, 'standalone-check', '订单A001云服务器无法连接')
            confirmed = agent.decide(sid, task['id'], task['version'], '确认')
            repeated = agent.decide(sid, task['id'], task['version'], '确认')
            assert confirmed['created'] and repeated['reused'] and confirmed['ticket']['id'] == repeated['ticket']['id']
            report['task_check'] = {'input_type': '临时库自动化模拟确认', 'task': task, 'first': confirmed, 'repeated': repeated}
            with app.state.store.db() as db:
                report['traces'] = [dict(row) for row in db.execute('SELECT * FROM traces ORDER BY created')]
        report['temporary_database_removed'] = True
    except Exception as error:
        report['error'] = str(error)
        save()
        raise
    report['finished'] = time.time()
    save()
    print('本项目独立检查完成：', output / 'report.json')
    print('demo结果是离线流程验证，不代表真实模型准确率；未修改运行业务库。')

# 仅当此文件作为入口直接运行时执行下面代码，被import时不启动
if __name__ == '__main__':
    main()

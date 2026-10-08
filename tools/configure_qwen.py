"""Create a Qwen reviewer profile without API calls or changes to the judge."""
import argparse
import getpass
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
JUDGE_KEYS = ('JUDGE_BASE_URL', 'JUDGE_API_KEY', 'JUDGE_MODEL')


def main():
    parser = argparse.ArgumentParser(description='新增 Qwen 检视配置，保留原裁判；不调用模型')
    parser.add_argument('--environment', choices=('aliyun', 'internal'), default='aliyun')
    parser.add_argument('--model', help='实际可调用的模型 ID；省略时交互输入')
    parser.add_argument('--base-url', help='OpenAI 兼容接口根地址；省略时交互输入')
    parser.add_argument('--source', default='model.local.json', help='用于复制裁判配置')
    parser.add_argument('--output', help='默认 model.qwen.<environment>.local.json')
    args = parser.parse_args()
    root = Path(os.environ.get('BENCH_CONFIG_ROOT', str(ROOT))).expanduser().resolve()
    source = Path(args.source).expanduser()
    output = Path(args.output or f'model.qwen.{args.environment}.local.json').expanduser()
    source = source if source.is_absolute() else root / source
    output = output if output.is_absolute() else root / output
    if output.exists() or output.is_symlink():
        parser.error('目标已存在，请编辑原文件或用 --output 指定新文件；不会覆盖')
    if not (output.name.startswith('model') and output.name.endswith('.local.json')):
        parser.error('文件名须为 model*.local.json，才能被工作台发现并排除出 Git')
    model = (args.model or input('Qwen 模型 ID（按服务端实际名称填写）：')).strip()
    base = (args.base_url or input('OpenAI 兼容 Base URL（不含 /chat/completions）：')).strip().rstrip('/')
    if not model or any(ch.isspace() for ch in model):
        parser.error('模型 ID 不能为空或含空白字符')
    try:
        url = urlsplit(base)
        valid = (url.scheme in ('http', 'https') and url.hostname and not
                 (url.username or url.password or url.query or url.fragment)
                 and not any(ch.isspace() for ch in base) and not any(ch in base for ch in '{}<>'))
        url.port
    except ValueError:
        valid = False
    if not valid or url.path.endswith('/chat/completions'):
        parser.error('请填写真实接口根地址；不能含密码、查询参数、占位符或 /chat/completions')
    if source.exists():
        try:
            original = json.loads(source.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            parser.error('原配置无法读取或 JSON 格式无效')
        if not isinstance(original, dict):
            parser.error('原配置必须是 JSON 对象')
    else:
        original = os.environ
    judge = {key: original.get(key, '') for key in JUDGE_KEYS}
    token = getpass.getpass('Qwen API Key（隐藏输入，仅保存到本机）：').strip()
    if not token:
        parser.error('API Key 不能为空')
    config = {'LCR_LLM_BASE_URL': base, 'LCR_LLM_TOKEN': token, 'LCR_LLM_MODEL': model,
              **judge, 'JUDGE_USE_MOCK': 'false'}
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print('已创建：', output)
    print('未调用接口。刷新工作台，在新建测评中选择此配置；请先验证 1 个案例。')
    if not all(judge.values()):
        print('评分前还需补齐 JUDGE_BASE_URL、JUDGE_API_KEY、JUDGE_MODEL。')
    print('兼容性尚待实测：LCR 使用非流式工具调用；不自动调整 Qwen 思考或 Token 参数。')


if __name__ == '__main__':
    main()

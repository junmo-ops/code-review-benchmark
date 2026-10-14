"""Create an OpenAI review profile; preserve the configured judge. No API calls."""
from pathlib import Path
import argparse,getpass,json,os
from urllib.parse import urlsplit
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "backend/src"))

def main():
 p=argparse.ArgumentParser(description='新增 OpenAI 检视配置，裁判继续使用原配置')
 p.add_argument('--model',default='gpt-5.4',help='账户有权限的 Chat Completions 模型名；默认 gpt-5.4')
 p.add_argument('--base-url',default='https://api.openai.com/v1')
 p.add_argument('--source',default='model.local.json');p.add_argument('--output',default='model.openai.local.json')
 args=p.parse_args()
 source=Path(args.source);output=Path(args.output)
 if not source.is_absolute():source=ROOT/source
 if not output.is_absolute():output=ROOT/output
 if output.exists():p.error('目标配置已存在，直接编辑它或使用新的 --output；停止覆盖')
 url=urlsplit(args.base_url)
 if url.scheme not in ('http','https') or not url.netloc or url.username or url.password or url.query or url.fragment:p.error('接口地址不能包含令牌、查询参数或账号密码')
 if source.exists():
  try:original=json.loads(source.read_text(encoding='utf-8'))
  except (ValueError,OSError):p.error('原配置无法读取或 JSON 格式无效')
  if not isinstance(original,dict):p.error('原配置必须为JSON对象')
 else:
  original=dict(os.environ)
 judge={key:original.get(key,'') for key in ('JUDGE_BASE_URL','JUDGE_API_KEY','JUDGE_MODEL')}
 token=getpass.getpass('输入 OpenAI API Key（不会显示，不会调用接口）：').strip()
 if not token:p.error('令牌不能为空')
 cfg={'LCR_LLM_BASE_URL':args.base_url.rstrip('/'),'LCR_LLM_TOKEN':token,'LCR_LLM_MODEL':args.model,**judge,'JUDGE_USE_MOCK':'false'}
 output.parent.mkdir(parents=True,exist_ok=True)
 fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(cfg,f,ensure_ascii=False,indent=2);f.write('\n')
 print('已创建：',output,'；未调用接口。')
 if not all(judge.values()):print('裁判配置尚不完整：评分前补齐 JUDGE_BASE_URL / JUDGE_API_KEY / JUDGE_MODEL。')
if __name__=='__main__':main()

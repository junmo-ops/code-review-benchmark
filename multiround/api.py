import json,os,time
from openai import OpenAI
from runner.storage import save

def call(messages,role,cfg,folder):
    folder.mkdir(parents=True,exist_ok=False)
    key='LCR_LLM_TOKEN' if role=='review' else 'JUDGE_API_KEY'
    base='LCR_LLM_BASE_URL' if role=='review' else 'JUDGE_BASE_URL'
    model=cfg[role+'_model']
    request={'model':model,'messages':messages,'temperature':cfg['parameters']['temperature'],'max_tokens':cfg['parameters']['max_tokens']}
    save(folder/'request.json',request);start=time.monotonic()
    save(folder/'execution.json',{'status':'running','role':role})
    try:
        with OpenAI(api_key=os.environ[key],base_url=os.environ[base],timeout=cfg['parameters']['request_timeout_seconds'],max_retries=cfg['parameters']['max_retries']) as client:
            response=client.chat.completions.create(**request)
        save(folder/'response.json',response.model_dump())
        if not response.choices or response.choices[0].finish_reason!='stop':raise ValueError('模型输出截断或未正常结束')
        text=response.choices[0].message.content or ''
        value=json.loads(text)
        if not isinstance(value,dict):raise ValueError('模型输出必须为 JSON 对象')
        save(folder/'execution.json',{'status':'complete','role':role,'elapsed_seconds':time.monotonic()-start})
        return value
    except BaseException as exc:
        save(folder/'execution.json',{'status':'failed','role':role,'error_type':type(exc).__name__,'elapsed_seconds':time.monotonic()-start})
        raise

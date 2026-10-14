import argparse,importlib.util,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]

def module(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/('tools' if name=='configure_openai' else '')/(name+'.py'));out=importlib.util.module_from_spec(spec);spec.loader.exec_module(out);return out
local=module('local');configure=module('configure_openai')
class ProfileTests(unittest.TestCase):
 def test_profile_preserves_judge_and_existing_source(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);source=root/'model.local.json';target=root/'model.openai.local.json'
   original={'LCR_LLM_TOKEN':'old-test','JUDGE_API_KEY':'judge-test','JUDGE_BASE_URL':'https://judge.example/v1','JUDGE_MODEL':'judge-model'}
   source.write_text(json.dumps(original));before=source.read_bytes()
   with patch.object(configure,'ROOT',root),patch.object(sys,'argv',['configure_openai.py','--model','test-model']),patch.object(configure.getpass,'getpass',return_value='test-new-token'):
    configure.main()
   result=json.loads(target.read_text());self.assertEqual(result['JUDGE_API_KEY'],'judge-test');self.assertEqual(result['LCR_LLM_TOKEN'],'test-new-token')
   self.assertEqual(source.read_bytes(),before);self.assertEqual(target.stat().st_mode&0o777,0o600)
 def test_selected_config_is_injected_in_fresh_process(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'local-runtime.json').write_text('{}')
   (root/'selected.local.json').write_text(json.dumps({'LCR_LLM_MODEL':'test-openai','LCR_LLM_TOKEN':'test-key','JUDGE_API_KEY':'test-judge'}))
   argv=['local.py','--config','selected.local.json','--timeout-seconds','60','init','--run-id','test','--dataset','data/test.jsonl']
   with patch.object(local,'ROOT',root),patch.object(sys,'argv',argv),patch.object(local,'execute',return_value=0) as execute:
    with self.assertRaises(SystemExit) as exit:local.main()
   self.assertEqual(exit.exception.code,0)
   command,env,limit,record=execute.call_args.args
   self.assertEqual(env['LCR_LLM_TOKEN'],'test-key');self.assertEqual(command[-2:],['--model','test-openai'])
 def test_missing_explicit_config_does_not_fall_back(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'local-runtime.json').write_text('{}')
   argv=['local.py','--config','missing.local.json','--timeout-seconds','60','status','--run-id','test']
   with patch.object(local,'ROOT',root),patch.object(sys,'argv',argv),patch.object(local,'execute') as execute:
    with self.assertRaises(SystemExit):local.main()
    execute.assert_not_called()
if __name__=='__main__':unittest.main()

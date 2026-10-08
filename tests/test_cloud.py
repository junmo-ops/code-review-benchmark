import sys,tempfile,json,unittest,argparse
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cloud
class CloudTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  self.items=cloud.checked_instances(cloud.ROOT/'data/pilot5.jsonl')
 def tearDown(self):self.temp.cleanup()
 def test_freeze_101_and_lock(self):
  raw=json.loads((cloud.ROOT/'data/pilot5.jsonl').read_text().splitlines()[0]);rows=[]
  for n in range(101):rows.append(dict(raw,instance_id='case'+str(n)))
  data=self.root/'data.jsonl';data.write_text('\n'.join(map(json.dumps,rows)))
  run=self.root/'run';run.mkdir()
  args=argparse.Namespace(dataset=str(data),model='test',repos_root=None,timeout_minutes=30)
  cloud.init_run(args,run)
  self.assertEqual(len(json.loads((run/'run-config.json').read_text())['selected_instances']),101)
  with cloud.exclusive(run):
   with self.assertRaises(ValueError):
    with cloud.exclusive(run):pass
 def test_states_preserve_unknown_and_complete(self):
  item=self.items[0];raw=self.root/'raw';raw.mkdir()
  (raw/(item.safe_id+'.execution.json')).write_text('{"status":"running"}')
  self.assertEqual(cloud.states([item],self.root)[0]['status'],'unfinished_check_required')
  result=self.root/'results';result.mkdir()
  (result/(item.safe_id+'.json')).write_text(json.dumps(dict(instance_id=item.instance_id,base_commit=item.base_commit,head_commit=item.head_commit,review=dict(status='complete',comments=[]))))
  self.assertEqual(cloud.states([item],self.root)[0]['status'],'complete')
 def test_timeout_guard_does_not_start_review(self):
  run=self.root/'test';run.mkdir()
  args=argparse.Namespace(dataset=str(cloud.ROOT/'data/pilot5.jsonl'),model='test',repos_root=None,timeout_minutes=30)
  cloud.init_run(args,run)
  argv=['cloud.py','review','--run-id','test','--runs-root',str(self.root),'--instance',self.items[0].instance_id,'--external-timeout-seconds','360']
  with patch.object(sys,'argv',argv),patch.object(cloud.adapter,'check_repos'),patch.object(cloud,'require_model'),patch.object(cloud.adapter,'process_timeout',return_value=1920),patch.object(cloud.adapter,'_stage_review') as review:
   with self.assertRaisesRegex(ValueError,'时限不足'):cloud.main()
   review.assert_not_called()
if __name__=='__main__':unittest.main()

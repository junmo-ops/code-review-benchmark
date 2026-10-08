import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from webui.service import Dashboard, save
from executable.dashboard import FILES, identity, verify

class CcrabDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.app=Dashboard(self.root)
        for f in FILES:
            p=self.root/f;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture')
        self.env=dict(LCR_LLM_MODEL='deepseek-flash',LCR_LLM_BASE_URL='https://api.deepseek.com',LCR_LLM_TOKEN='private-key')
        save(self.root/'model.local.json',self.env)
        self.cfg=dict(identity(self.root,self.env),ui_managed=True,instance_id='case',repo='networkx/networkx')
        save(self.root/'runs/ccrab/test/run-config.json',self.cfg)
    def tearDown(self):
        self.app.shutdown();self.tmp.cleanup()
    def test_model_and_source_changes_rejected(self):
        verify(self.root,self.cfg,self.env)
        with self.assertRaises(ValueError):verify(self.root,self.cfg,dict(self.env,LCR_LLM_MODEL='deepseek-other'))
        (self.root/FILES[0]).write_text('modified')
        with self.assertRaises(ValueError):verify(self.root,self.cfg,self.env)
    def test_empty_findings_do_not_become_test_pass_or_f1(self):
        save(self.root/'runs/ccrab/test/status.json',dict(status='complete_no_findings',review_executed=True,repair_executed=False,test_pass_rate=0))
        value=self.app.describe('ccrab/test')
        self.assertEqual(value['status'],'complete_no_findings');self.assertEqual(value['metrics'],{})
        self.assertFalse(value['can_run']);self.assertEqual(value['completed'],1)
        with self.assertRaises(ValueError):self.app.submit(dict(action='workflow',key='ccrab/test'))
    def test_history_readonly_and_visible(self):
        save(self.root/'runs/ccrab/old/config.json',dict(instance_id='old-case',review_model='deepseek-flash'))
        save(self.root/'runs/ccrab/old/status.json',dict(status='complete',review_executed=True,repair_executed=True,test_pass_rate=1))
        # Dataset fixture is intentionally not JSON; only exercise run enumeration.
        with patch.object(self.app,'catalog',return_value=[]):snapshot=self.app.snapshot()
        old=next(r for r in snapshot['runs'] if r['key']=='ccrab/old')
        self.assertFalse(old['can_run']);self.assertEqual(old['metrics'],{'test_pass_rate':1})
        self.assertTrue(old['compatibility_warning'])
    def test_command_does_not_leak_keys_and_plan_is_separate(self):
        cmd,env,limit=self.app.command('ccrab/test','run','model.local.json')
        self.assertIn('executable.dashboard',cmd);self.assertNotIn('private-key',' '.join(cmd));self.assertEqual(limit,2700)
        steps=self.app.plan(dict(key='ccrab/test',action='workflow'),{},None)
        self.assertEqual([s[0] for s in steps],['check','run'])
        with self.assertRaises(ValueError):self.app.submit(dict(action='eval',key='ccrab/test'))
        with self.assertRaises(ValueError):self.app.submit(dict(action='workflow',key='ccrab/test',retry=True))
    def test_failure_does_not_show_score(self):
        save(self.root/'runs/ccrab/test/status.json',dict(status='failed',review_executed=True,repair_executed=True,test_pass_rate=1))
        self.assertEqual(self.app.describe('ccrab/test')['metrics'],{})

if __name__=='__main__':unittest.main()

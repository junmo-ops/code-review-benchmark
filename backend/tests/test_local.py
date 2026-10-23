import importlib.util,json,os,signal,subprocess,sys,tempfile,time,unittest
from pathlib import Path
SPEC=importlib.util.spec_from_file_location('local_runner',Path(__file__).resolve().parents[1]/'src/local.py')
local=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(local)
class LifecycleTests(unittest.TestCase):
 def test_timeout_cleans_detached_child(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);pidfile=root/'pid'
   child="import time; time.sleep(60)"
   parent="import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c',"+repr(child)+"],start_new_session=True); pathlib.Path("+repr(str(pidfile))+").write_text(str(p.pid)); time.sleep(60)"
   result=local.execute([sys.executable,'-c',parent],dict(os.environ),1,root/'record.json',cwd=root)
   self.assertEqual(result,124)
   self.assertEqual(json.loads((root/'record.json').read_text())['status'],'platform_timeout')
   pid=int(pidfile.read_text())
   status=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
   self.assertTrue(not status or status.startswith('Z'),status)
 def test_interrupt_cleans_child_and_records(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);pidfile=root/'pid';record=root/'record.json'
   inner="import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); pathlib.Path("+repr(str(pidfile))+").write_text(str(p.pid)); time.sleep(60)"
   code="import sys,os; sys.path.insert(0,"+repr(str(Path(__file__).resolve().parents[1]/'src'))+ "); import local; from pathlib import Path; sys.exit(local.execute([sys.executable,'-c',"+repr(inner)+"],dict(os.environ),60,Path("+repr(str(record))+")))"
   process=subprocess.Popen([sys.executable,'-c',code])
   deadline=time.monotonic()+5
   while not pidfile.exists() and time.monotonic()<deadline:time.sleep(.05)
   self.assertTrue(pidfile.exists())
   process.send_signal(signal.SIGINT)
   self.assertEqual(process.wait(timeout=10),130)
   self.assertEqual(json.loads(record.read_text())['status'],'interrupted')
   status=subprocess.run(['ps','-p',pidfile.read_text(),'-o','stat='],capture_output=True,text=True).stdout.strip()
   self.assertTrue(not status or status.startswith('Z'),status)
 def test_normal_exit_record(self):
  with tempfile.TemporaryDirectory() as tmp:
   record=Path(tmp)/'record.json'
   self.assertEqual(local.execute([sys.executable,'-c','raise SystemExit(7)'],dict(os.environ),5,record),7)
   self.assertEqual(json.loads(record.read_text())['exit_code'],7)
if __name__=='__main__':unittest.main()

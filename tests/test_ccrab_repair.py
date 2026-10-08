import tempfile
from pathlib import Path
import unittest
from executable.deepseek_repair import safe_path,tool

class RepairTests(unittest.TestCase):
    def test_path_boundaries(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/'repo';root.mkdir();(root/'a.py').write_text('old')
            (root/'escape.py').symlink_to(Path(t)/'outside')
            for name in ('../outside','/etc/passwd','.git/config','escape.py'):
                with self.assertRaises(ValueError):safe_path(root,name)
            self.assertEqual(tool(root,'read_file',{'path':'a.py'}),'old')
    def test_unique_replacement_only(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);(root/'a.py').write_text('old old')
            with self.assertRaises(ValueError):tool(root,'replace_text',{'path':'a.py','old':'old','new':'new'})
            tool(root,'replace_text',{'path':'a.py','old':'old old','new':'new'})
            self.assertEqual((root/'a.py').read_text(),'new')
    def test_no_shell(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ValueError):tool(Path(t),'shell',{'command':'echo unsafe'})

if __name__=='__main__':unittest.main()

import io,tarfile,tempfile,unittest
from pathlib import Path
from tools.repo_snapshot import restore_snapshot

class SnapshotTests(unittest.TestCase):
    def test_rejects_traversal_links_and_existing_destination(self):
        for name,kind in [('../bad',tarfile.REGTYPE),('.git/link',tarfile.SYMTYPE),('/bad',tarfile.REGTYPE)]:
            with self.subTest(name=name),tempfile.TemporaryDirectory() as d:
                p=Path(d);archive=p/'repo.tar.gz'
                with tarfile.open(archive,'w:gz') as t:
                    m=tarfile.TarInfo(name);m.type=kind;m.size=0;t.addfile(m,io.BytesIO())
                with self.assertRaises(ValueError):restore_snapshot(archive,p/'repo')
                self.assertFalse((p/'repo').exists())
                with self.assertRaises(ValueError):restore_snapshot(archive,p)
    def test_restores_regular_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);archive=p/'repo.tar.gz'
            with tarfile.open(archive,'w:gz') as t:
                data=b'ref: refs/heads/main\n';m=tarfile.TarInfo('.git/HEAD');m.size=len(data);t.addfile(m,io.BytesIO(data))
            restore_snapshot(archive,p/'repo')
            self.assertEqual((p/'repo/.git/HEAD').read_bytes(),data)
if __name__=='__main__':unittest.main()

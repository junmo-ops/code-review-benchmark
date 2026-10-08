"""Portable Git metadata snapshot, including shallow boundaries (unlike bundles)."""
import shutil,tarfile,tempfile
from pathlib import Path,PurePosixPath

def restore_snapshot(archive,repo):
    repo=Path(repo)
    if repo.exists():raise ValueError('仓库目录已存在，拒绝覆盖')
    repo.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.restore-',dir=repo.parent) as directory:
        target=Path(directory)/'repo';target.mkdir()
        with tarfile.open(archive,'r:gz') as tar:
            for member in tar:
                path=PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0]!='.git' or not member.isfile():
                    raise ValueError('Git 快照包含不允许的路径或类型')
                dest=target.joinpath(*path.parts);dest.parent.mkdir(parents=True,exist_ok=True)
                with tar.extractfile(member) as source,dest.open('wb') as output:shutil.copyfileobj(source,output)
        if not (target/'.git/HEAD').is_file():raise ValueError('快照缺少 Git HEAD')
        target.rename(repo)

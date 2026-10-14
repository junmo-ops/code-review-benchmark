"""Bounded evidence traversal; never descend into repository workspaces."""
import os
from pathlib import Path

EXCLUDED = {'workspaces', 'repair-workspace', '.git', 'node_modules'}


def evidence_files(root):
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = sorted(name for name in folders
                            if name not in EXCLUDED and not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if not path.is_symlink():
                yield path

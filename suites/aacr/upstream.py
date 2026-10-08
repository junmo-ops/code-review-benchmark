"""Boundary for inherited top-level imports; grading runs in its own interpreter."""
import sys
from runner.storage import ROOT

def activate():
    directory = str(ROOT / "vendor/aacr/evaluation")
    if directory not in sys.path:
        sys.path.insert(0, directory)

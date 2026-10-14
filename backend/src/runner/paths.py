"""Source and resource roots shared by API, workers, CLIs and installed tools."""
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(os.environ.get('BENCH_PROJECT_ROOT', Path(__file__).resolve().parents[3])).resolve()
SOURCE_ROOT = PROJECT_ROOT / 'backend' / 'src'
# Vendored benchmarks and deployment modules are repository resources.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

os.environ.setdefault('BENCH_PROJECT_ROOT', str(PROJECT_ROOT))
_python_paths = [str(SOURCE_ROOT), str(PROJECT_ROOT), os.environ.get('PYTHONPATH', '')]
os.environ['PYTHONPATH'] = os.pathsep.join(path for path in _python_paths if path)

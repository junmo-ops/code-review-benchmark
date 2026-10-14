"""Restart the same isolated data directory after a real container termination."""
import sys
from pathlib import Path
from server import main

args = sys.argv[1:]
data = Path(args[args.index('--data') + 1])
if (data / 'config').exists():
    sys.argv.append('--reuse')
main()

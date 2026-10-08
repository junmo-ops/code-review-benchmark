"""Stable CLI/import entry point. Implementation lives in runner.cloud."""
import sys
from runner import cloud as implementation
if __name__ == "__main__":
    try:
        implementation.main()
    except KeyboardInterrupt:
        print("已中断，已写入的结果保留；运行 status 查看状态。",file=sys.stderr)
        raise SystemExit(130)
else:
    sys.modules[__name__] = implementation

"""Legacy import bridge. New execution is through local.py or cloud.py."""
import sys
from reviewers.lcr import adapter
if __name__ == "__main__":
    raise SystemExit("请使用根目录 local.py / cloud.py；案例集通过 init --suite 指定。")
sys.modules[__name__] = adapter

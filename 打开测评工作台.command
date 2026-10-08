#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  echo '未找到项目 Python 环境，请先按照 docs/Mac本地验证.md 完成环境准备。'
  read -r '?按回车关闭…'
  exit 1
fi
exec ./.venv/bin/python ui.py

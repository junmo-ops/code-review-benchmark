#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
if ! command -v npm >/dev/null; then
  echo '请安装 Node 22。首次使用请执行 npm run setup 和 npm run build。'
  read -r '?按回车关闭…'
  exit 1
fi
npm run start
read -r '?按回车关闭…'

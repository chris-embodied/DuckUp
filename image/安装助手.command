#!/bin/zsh
cd -- "${0:A:h}" || exit 1
for py in /usr/bin/python3 python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if "$py" -c 'import sys, check_calibration; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    exec "$py" migrate.py
  fi
done
echo '请安装 Xcode 命令行工具，或使用 Python 3.11 及以上版本后重新打开安装助手。'
exit 1

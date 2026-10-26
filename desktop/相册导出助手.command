#!/bin/bash
# 双击启动相册导出助手（macOS）
cd "$(dirname "$0")" || exit 1
exec python3 -m album_export gui

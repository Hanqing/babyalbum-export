@echo off
rem 双击启动相册导出助手（Windows）
cd /d "%~dp0"
py -3 -m album_export gui 2>nul || python -m album_export gui
pause

@echo off
chcp 65001 >nul
cd /d "L:\0.patent data\サーチャーヘルパー\patent-compare"
start /MIN cmd /c "timeout /t 3 >nul && start http://localhost:5000"
title PatentCompare Server
python web.py
pause

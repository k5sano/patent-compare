@echo off
chcp 65001 >nul
cd /d "L:\0.patent data\サーチャーヘルパー\patent-compare"
rem 設定は .env で調整してください (PATENT_COMPARE_HOST / _PORT / _DEBUG)
rem 既定はローカルホスト 127.0.0.1:5000 / debug=off
start /MIN cmd /c "timeout /t 3 >nul && start http://127.0.0.1:5000"
title PatentCompare Server
python web.py
pause

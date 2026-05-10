@echo off
chcp 65001 >nul
cd /d "L:\0.patent data\サーチャーヘルパー\patent-compare"
rem 設定は .env で調整してください (PATENT_COMPARE_HOST / _PORT / _DEBUG)
rem 既定はローカルホスト 127.0.0.1:5000 / debug=off
rem Chrome があれば、アドレスバー/タブを出さないアプリ風フルスクリーンで開く
set "APP_URL=http://127.0.0.1:5000"
set "CHROME_EXE="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined CHROME_EXE if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined CHROME_EXE if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME_EXE=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if defined CHROME_EXE (
  start /MIN cmd /c "timeout /t 3 >nul && start "" "%CHROME_EXE%" --new-window --start-fullscreen --app=%APP_URL%"
) else (
  start /MIN cmd /c "timeout /t 3 >nul && start %APP_URL%"
)
title PatentCompare Server
python web.py
pause

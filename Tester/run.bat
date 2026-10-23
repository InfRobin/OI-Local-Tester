@echo off
setlocal
cd /d "%~dp0"

if exist "Tester.exe" (
  start "" "Tester.exe" %*
  exit /b 0
)

if exist "runtime\pythonw.exe" (
  start "" "runtime\pythonw.exe" "main.py" %*
  exit /b 0
)

where pythonw >nul 2>nul
if errorlevel 1 (
  echo [错误] 没有找到 Python。请安装 Python 3.9+ 并勾选 Add Python to PATH，
  echo        或者把便携版 Python 放到 runtime\ 目录下。
  pause
  exit /b 1
)

start "" pythonw "main.py" %*

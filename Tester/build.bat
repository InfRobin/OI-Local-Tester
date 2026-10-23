@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   OI 对拍器 - 构建 Tester.exe
echo ============================================

where g++ >nul 2>nul
if errorlevel 1 (
  echo [错误] 没有找到 g++，请安装 MinGW-w64 并加入 PATH。
  pause
  exit /b 1
)
where windres >nul 2>nul
if errorlevel 1 (
  echo [错误] 没有找到 windres（MinGW-w64 自带）。
  pause
  exit /b 1
)

if not exist "assets\icon.ico" (
  echo [提示] 正在生成图标...
  python tools\make_icon.py
)

echo [1/2] 编译资源...
windres "launcher\launcher.rc" -O coff -o "launcher\launcher.res"
if errorlevel 1 (
  echo [错误] 资源编译失败，跳过图标继续构建。
  set "RES="
) else (
  set "RES=launcher\launcher.res"
)

echo [2/2] 编译启动器...
g++ -O2 -std=c++17 -municode -mwindows "launcher\launcher.cpp" %RES% -o "Tester.exe" -lshlwapi -static -static-libgcc -static-libstdc++
if errorlevel 1 (
  echo [错误] 编译失败。
  pause
  exit /b 1
)

echo.
echo 构建成功：Tester.exe
echo 双击 Tester.exe 即可启动对拍器。
pause

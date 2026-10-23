# 生成 GitHub Release 用的发布包
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File tools\make_release.ps1
#   powershell -ExecutionPolicy Bypass -File tools\make_release.ps1 -OutDir dist -PythonRoot "C:\Python312"
#
# 产物：
#   dist/OI-Tester-<ver>-portable.zip   程序 + 内置 Python 运行时
#   dist/OI-Tester-<ver>-lite.zip       程序本体（需要本机 Python 3.9+）
#   dist/SHA256SUMS.txt                  校验和
#   dist/RELEASE_NOTES.md                可从 CHANGELOG 提取的发布说明

param(
    [string]$OutDir = "dist",
    [string]$PythonRoot = "",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$init = Get-Content "app\__init__.py" -Raw -Encoding UTF8
if ($init -match '__version__\s*=\s*"([^"]+)"') {
    $version = $matches[1]
} else {
    throw "无法从 app\__init__.py 读取版本号"
}
$name = "OI-Tester-$version"
Write-Host "==> 版本 $version"

# ---------------------------------------------------------------- 构建启动器
$exe = Join-Path $root "Tester.exe"
if (-not $SkipBuild) {
    $gpp = Get-Command g++ -ErrorAction SilentlyContinue
    $windres = Get-Command windres -ErrorAction SilentlyContinue
    if ($gpp -and $windres) {
        Write-Host "==> 编译 Tester.exe"
        & $windres.Source "launcher\launcher.rc" -O coff -o "launcher\launcher.res"
        if ($LASTEXITCODE -ne 0) { throw "windres 编译资源失败" }
        & $gpp.Source -O2 -std=c++17 -municode -mwindows "launcher\launcher.cpp" "launcher\launcher.res" -o "Tester.exe" -lshlwapi -static -static-libgcc -static-libstdc++
        if ($LASTEXITCODE -ne 0) { throw "g++ 编译启动器失败" }
        Remove-Item "launcher\launcher.res" -Force -ErrorAction SilentlyContinue
    } elseif (Test-Path $exe) {
        Write-Warning "没有找到 g++ / windres，直接使用现有的 Tester.exe"
    } else {
        throw "缺少 Tester.exe，且当前环境无法构建（需要 MinGW 的 g++ 与 windres）"
    }
}
if (-not (Test-Path $exe)) { throw "找不到 Tester.exe" }

$rc = Get-Content "launcher\launcher.rc" -Raw -Encoding UTF8
if ($rc -match 'FILEVERSION\s+(\d+),(\d+),(\d+),(\d+)') {
    $rcVersion = "$($matches[1]).$($matches[2]).$($matches[3])"
    if ($rcVersion -ne $version) {
        Write-Warning "launcher.rc 里的版本是 $rcVersion，与 app 版本 $version 不一致，建议同步后再发布"
    }
}

# ---------------------------------------------------------------- 准备目录
if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }
$stage = Join-Path $OutDir ".stage"
$liteDir = Join-Path $stage "$name-lite"
$portableDir = Join-Path $stage "$name-portable"
New-Item -ItemType Directory -Force -Path $liteDir, $portableDir | Out-Null

function Copy-Common([string]$dest) {
    foreach ($file in @("main.py", "README.md", "LICENSE", "CHANGELOG.md", "run.bat", "build.bat")) {
        if (Test-Path $file) { Copy-Item $file $dest }
    }
    foreach ($dir in @("app", "web", "examples", "docs", "launcher")) {
        if (Test-Path $dir) { Copy-Item $dir $dest -Recurse }
    }
    Copy-Item $exe (Join-Path $dest "Tester.exe")
    Get-ChildItem $dest -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem $dest -Recurse -File -Filter "*.pyc" -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

Write-Host "==> 复制程序文件"
Copy-Common $liteDir
Copy-Common $portableDir

# ---------------------------------------------------------------- 复制 Python
if (-not $PythonRoot) {
    $PythonRoot = (& python -c "import sys; print(sys.base_prefix)").Trim()
}
if (-not (Test-Path (Join-Path $PythonRoot "pythonw.exe"))) {
    throw "PythonRoot 无效：$PythonRoot（缺少 pythonw.exe）"
}
Write-Host "==> 打包 Python 运行时：$PythonRoot"

$runtime = Join-Path $portableDir "runtime"
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
Get-ChildItem $PythonRoot -File | Copy-Item -Destination $runtime
foreach ($dir in @("DLLs", "Lib", "tcl")) {
    $source = Join-Path $PythonRoot $dir
    if (Test-Path $source) { Copy-Item $source $runtime -Recurse }
}

# 只保留运行所需的最小集合
foreach ($unused in @("site-packages", "test", "idlelib", "ensurepip", "venv", "turtledemo")) {
    $target = Join-Path $runtime "Lib\$unused"
    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
}
Get-ChildItem $runtime -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $runtime -Recurse -File -Filter "*.pyc" -ErrorAction SilentlyContinue |
    Remove-Item -Force -ErrorAction SilentlyContinue

$pythonLicense = Join-Path $runtime "LICENSE.txt"
if (Test-Path $pythonLicense) {
    Move-Item $pythonLicense (Join-Path $runtime "PYTHON-LICENSE.txt") -Force
}
@"
这个 runtime/ 目录是 Python 的便携运行时，仅用于运行 OI 对拍器。
Python 使用 PSF 许可证，详见 PYTHON-LICENSE.txt；Tcl/Tk 的许可证在 tcl/ 目录中。
"@ | Set-Content (Join-Path $runtime "README.txt") -Encoding UTF8

# ---------------------------------------------------------------- 压缩
Write-Host "==> 生成压缩包"
$portableZip = Join-Path $OutDir "$name-portable.zip"
$liteZip = Join-Path $OutDir "$name-lite.zip"
Compress-Archive -Path $portableDir -DestinationPath $portableZip -CompressionLevel Optimal
Compress-Archive -Path $liteDir -DestinationPath $liteZip -CompressionLevel Optimal

# ---------------------------------------------------------------- 校验和
$hashLines = @()
foreach ($zip in @($portableZip, $liteZip)) {
    $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    $hashLines += "$hash  $(Split-Path $zip -Leaf)"
}
$hashLines | Set-Content (Join-Path $OutDir "SHA256SUMS.txt") -Encoding ASCII

# ---------------------------------------------------------------- 发布说明
$changelog = Get-Content "CHANGELOG.md" -Raw -Encoding UTF8
$pattern = "(?ms)^## v" + [regex]::Escape($version) + ".*?(?=^## v|\z)"
$match = [regex]::Match($changelog, $pattern)
$section = if ($match.Success) {
    $match.Value.Trim() -replace "^## v$([regex]::Escape($version))\s*[—-]\s*", "## v$version · "
} else {
    "## v$version"
}
$portableMb = [math]::Round((Get-Item $portableZip).Length / 1MB, 1)
$liteMb = [math]::Round((Get-Item $liteZip).Length / 1MB, 2)
$notes = @"
$section

## 下载

| 文件 | 大小 | 说明 |
| --- | --- | --- |
| ``${name}-portable.zip`` | $portableMb MB | 内置 Python 运行时，解压后双击 Tester.exe 即可 |
| ``${name}-lite.zip`` | $liteMb MB | 不含 Python，需要本机已有 Python 3.9+ |

## 运行要求

- Windows 10 / 11（x64）
- Microsoft Edge 或 Chrome（界面以独立应用窗口打开）
- 编译 C/C++ 需要 MinGW-w64 的 g++（便携包只内置 Python，不包含 g++）
- 只评测 Python 程序或已有 ``.exe`` 时，无需安装 g++

校验下载：``SHA256SUMS.txt``
"@
$notes | Set-Content (Join-Path $OutDir "RELEASE_NOTES.md") -Encoding UTF8

Remove-Item $stage -Recurse -Force

# ---------------------------------------------------------------- 汇总
Write-Host ""
Write-Host "=== 发布包已生成 ==="
Get-ChildItem $OutDir | Where-Object { $_.Name -ne ".stage" } |
    ForEach-Object { "{0,-42} {1,8:N2} MB" -f $_.Name, ($_.Length / 1MB) }
Write-Host ""
Write-Host "下一步："
Write-Host "  1. 解压 portable.zip，双击 Tester.exe 实测一次"
Write-Host "  2. 把 portable.zip / lite.zip / SHA256SUMS.txt 上传到 GitHub Release"
Write-Host "  3. 用 dist\RELEASE_NOTES.md 作为 Release 说明"

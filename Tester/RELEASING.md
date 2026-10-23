# 发布流程

这份文档说明如何把 OI 对拍器发布到 GitHub，以及 Release 里应该放什么。

## 1. 发版前检查

- [ ] 更新版本号：
  - `app/__init__.py` 里的 `__version__`
  - `launcher/launcher.rc` 里的 `FILEVERSION` / `PRODUCTVERSION` / `FileVersion` / `ProductVersion`
- [ ] 更新 `CHANGELOG.md`
- [ ] 运行单元测试：

  ```bat
  python -m unittest tests.test_judge -v
  ```

- [ ] 确认 `docs/` 里的截图是最新界面。

## 2. 生成发布包

```powershell
powershell -ExecutionPolicy Bypass -File tools\make_release.ps1
```

脚本会自动：

1. 读取版本号，必要时重新构建 `Tester.exe`（需要 MinGW 的 `g++` 与 `windres`）；
2. 生成两个目录和两个压缩包：

   | 文件 | 内容 | 适用场景 |
   | --- | --- | --- |
   | `dist/OI-Tester-<ver>-portable.zip` | 程序 + 内置 Python 运行时 | 其他电脑解压即用，不需要安装 Python |
   | `dist/OI-Tester-<ver>-lite.zip` | 程序本体（不含 Python） | 已装 Python 3.9+ 的电脑，体积小 |
   | `dist/SHA256SUMS.txt` | 两个压缩包的 SHA-256 | 校验下载完整性 |
   | `dist/RELEASE_NOTES.md` | 从 CHANGELOG 提取的当前版本说明 | 直接粘贴到 GitHub Release |

3. 校验发布包（脚本会打印大小，可手动解压试运行）。

> 便携包内置的 Python 来自当前机器上的 Python 安装（已去掉 `site-packages`、测试与文档），
> 并保留 Python 的 PSF 许可证文件。请勿删除 `runtime/PYTHON-LICENSE.txt`。

## 3. 初始化并推送仓库

```bat
git init
git add .
git commit -m "release: v1.1.0"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

仓库不会提交 `Tester.exe` 与 `dist/`（见 `.gitignore`），它们只作为 Release 附件分发。
如果你希望仓库里也带可执行文件，删除 `.gitignore` 中的 `/Tester.exe` 后提交即可。

## 4. 创建 GitHub Release

### 网页操作

1. 打开仓库 → Releases → **Draft a new release**；
2. Tag 填 `v1.1.0`，Target 选 `main`；
3. 标题：`OI 对拍器 v1.1.0`；
4. 描述直接粘贴 `dist/RELEASE_NOTES.md` 的内容；
5. 上传 `dist/` 下的两个 zip 与 `SHA256SUMS.txt`；
6. 勾选 **Set as the latest release**，发布。

### 或使用 GitHub CLI

```bat
gh release create v1.1.0 ^
  "dist/OI-Tester-1.1.0-portable.zip" ^
  "dist/OI-Tester-1.1.0-lite.zip" ^
  "dist/SHA256SUMS.txt" ^
  --title "OI 对拍器 v1.1.0" ^
  --notes-file "dist/RELEASE_NOTES.md"
```

## 5. Release 说明建议结构

```markdown
## 亮点
- Special Judge（testlib）支持 …

## 下载
- **便携版**（推荐，内置 Python）：OI-Tester-1.1.0-portable.zip
- **精简版**（需自备 Python 3.9+）：OI-Tester-1.1.0-lite.zip

## 运行要求
- Windows 10 / 11 x64
- Microsoft Edge 或 Chrome（界面窗口）
- 编译 C/C++ 需要 MinGW-w64 g++；便携版自带 Python，但没有自带 g++

## 已知问题 / 升级说明
- …
```

## 6. 小提示

- `Tester.exe` 没有代码签名，首次运行可能被 SmartScreen 拦截：点「更多信息 → 仍要运行」即可。
- 想避免拦截可以购买代码签名证书，用 `signtool` 对 `Tester.exe` 和压缩包签名。
- 发布前建议在另一台没有开发环境的 Windows 上解压便携包，双击 `Tester.exe` 实测一次。

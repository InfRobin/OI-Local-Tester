## v1.1.0 · 2026-10-23

### 新增

- **Special Judge（testlib）**：支持导入 checker（`checker.cpp` / `spj.cpp` / `chk.cpp` / `judge.cpp` 自动识别），以 `input output answer` 方式运行，按 testlib 退出码给出通过、答案错误、格式错误、部分分或 Checker 异常。
- 自动定位 MinGW 自带的 `testlib.h` 并追加 `-I`；checker 编译结果独立缓存。
- 详情抽屉新增「Checker」标签页，展示判定信息、退出码、用时、分数与 stderr。
- 每道题的 checker 选择与开关状态会被记住。
- `examples/SPJP1001` Special Judge 示例与首页「Special Judge 示例」入口。
- checker 与选手程序的编译临时文件改到数据目录 `tmp/`，不再依赖系统 `%TEMP%` 的权限。

### 改进

- 评测前若 checker 不可用，会把测试点标记为「Checker 异常」并显示编译错误，避免误判成选手的错误。
- 新增 `PE`（格式错误）、`PC`（部分分）、`CJF`（Checker 异常）三种状态与配色。
- 单元测试从 17 个增加到 25 个，覆盖 checker 判定、部分分解析与整套 SPJ 评测。

## 下载

| 文件 | 大小 | 说明 |
| --- | --- | --- |
| `OI-Tester-1.1.0-portable.zip` | 15.4 MB | 内置 Python 运行时，解压后双击 Tester.exe 即可 |
| `OI-Tester-1.1.0-lite.zip` | 0.84 MB | 不含 Python，需要本机已有 Python 3.9+ |

## 运行要求

- Windows 10 / 11（x64）
- Microsoft Edge 或 Chrome（界面以独立应用窗口打开）
- 编译 C/C++ 需要 MinGW-w64 的 g++（便携包只内置 Python，不包含 g++）
- 只评测 Python 程序或已有 `.exe` 时，无需安装 g++

校验下载：`SHA256SUMS.txt`

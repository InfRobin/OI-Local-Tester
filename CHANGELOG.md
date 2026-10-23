# 更新日志

## v1.1.0 — 2026-10-23

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

## v1.0.0 — 2026-10-22

### 新增

- 打开题目文件夹自动识别题目名称、测试数据（`.in` / `.out` / `.ans`）与选手程序（`.cpp` / `.py` / `.exe`）。
- 导入选手程序与测试数据（支持多选文件或文件夹，自动配对）。
- 自定义时间、内存、输出空间、栈空间限制；三档预设。
- 基于 Windows Job Object 的精确限时、限内存与 `MLE` 判定。
- 按测试点展示 `AC / WA / TLE / MLE / RE / OLE / CE`、用时、内存与退出码。
- 勾选评测、全部评测、遇错即停、随时停止。
- 逐行差异对比、三种答案比较模式、深色 / 浅色主题、快捷键。
- C++ 启动器 `Tester.exe`、示例题目与 17 个单元测试。

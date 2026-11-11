# ArkDPS 项目规则（对所有 agent 生效）

本文件是硬规则，优先级高于 README 的叙述性说明。与 Ticket 冲突时先停下来说明，不得自行恢复 Tk。

## UI：只有 WPF

- 产品 UI 只有 `desktop/ArkDPS.Desktop`（C# / .NET 10 / WPF）。Python 只做采集、统计、历史与 worker 协议。
- **Tk 面板已封存**（ARK-27）：源码归档在 `archive/legacy-tk/`，依据见 `docs/adr/0005-seal-legacy-tk.md`。
- 禁止新增 Tk 代码、Tk 测试、Tk 验收脚本、Tk 文档或 `--legacy-tk` / `--acceptance-output` / Tk `--self-test` 入口。
- 禁止导入 `tkinter`，禁止从 `archive/legacy-tk/` 反向导入任何模块。
- 禁止用 Tk 截图、Tk 回执或旧 ARK-14 Tk 结论作为当前 WPF 的验收证据。

## 验收路径

- UI 改动必须走 `python acceptance/verify_wpf.py`。
- 打包必须走 `python tools/package_windows.py` + `python acceptance/verify_wpf_package.py <zip>`。
- 回归基线：`python -m pytest tests -q`，`tests/test_no_legacy_tk.py` 必须通过。
- 不得通过删除测试、放宽断言、跳过 `archive/` 之外的用例来制造通过。

## 归档目录

- `archive/legacy-tk/` 不进入 pytest 收集、运行包、PyInstaller/打包清单和 CI（根目录 `conftest.py` 排除）。
- 归档内容只增不改，不得被活跃代码引用；确需历史引用时在文档里写明"已封存"并链接归档 README。

## 遇到必须改 Tk 的任务

先停止实现，在工单中说明原因与替代方案（通常是改 WPF 或新增 WPF 验收），不要恢复 Tk 作为过渡。

# ADR 0005：封存旧 Tk 面板，WPF 为唯一 UI

- 状态：已接受
- 日期：2026-11-10（ARK-27）
- 相关：[ADR 0003](0003-wpf-python-worker.md)、[归档说明](../../archive/legacy-tk/README.md)

## 背景

ARK-19 已把产品 UI 换成 C# / .NET 10 / WPF（`desktop/ArkDPS.Desktop`），但仓库仍保留 Tk 的运行入口
（`--legacy-tk`、Tk `--self-test`、`--acceptance-output`）、Tk 模块、Tk 测试、Tk 验收脚本、
PyInstaller 构建链和对应文档。结果是后续 agent 与测试仍可能启动旧面板，产出与 WPF 候选不一致的
截图/回执；"有些测试开老面板、有些开新面板"会污染验收证据链。

## 决定

1. 产品 UI 只有 WPF。Tk 实现、Tk 专用测试/验收脚本与旧构建工具整体归档到 `archive/legacy-tk/`，
   保留 git 历史，不做不可追溯的物理删除。
2. `app.py` 只保留两条路径：默认 `desktop_launcher.launch` 启动 WPF，`--capture-seconds` 走无界面采集。
   `--legacy-tk`、`--acceptance-output`、`--acceptance-seconds`、`--acceptance-panels`、`--self-test`
   由 argparse 明确报错退出（退出码 2），错误文案指向 `acceptance/verify_wpf.py`，绝不静默回退。
3. 归档目录不进入 pytest 收集（根 `conftest.py`）、不进入运行包（`tools/package_windows.py` 的 `BACKEND`
   白名单），打包前后都断言包内无 `tkinter` / Tcl / Tk 文件。
4. `tests/test_no_legacy_tk.py` 是硬守卫：活跃代码不得导入封存模块，`app.py` 导入后 `tkinter` 不得出现在
   `sys.modules`，被移除的参数必须以非零码退出。
5. 验收路径统一为 `acceptance/verify_wpf.py` 与 `acceptance/verify_wpf_package.py`；CI 只产出 WPF 便携包。

## 影响

- Tk 截图、Tk 回执与 ARK-14 旧候选结论不再作为当前证据；历史记录保留在归档与文档的"已封存"段落里。
- 任何"必须改 Tk"的需求先停下来说明，改走 WPF 或新增 WPF 验收，不得把 Tk 作为过渡保留。
- 共享逻辑（`layout_store.py`、`panel_model.py`、`battle_share.py`、`settings.py`、`connection.py` 等）
  不受影响，继续被 WPF worker 与 CLI 使用。
- 若发现某个 Tk 独有能力在 WPF 没有替代，另开后续票；本 ADR 不放宽"不得恢复 Tk"。

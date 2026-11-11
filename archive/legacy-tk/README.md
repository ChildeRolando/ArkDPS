# 旧 Tk 面板归档（已封存）

- **封存日期**：2026-11-10
- **封存工单**：ARK-27
- **封存前最后活跃提交**：`6df4e0f`（ARK-22 合入后的 `main`，Tk 仍可运行的最后一个基线）
- **封存动作**：本目录所在 commit（ARK-27 的 PR）；无 tag，未对外发布
- **决定**：[ADR 0005](../../docs/adr/0005-seal-legacy-tk.md)

> **不再支持、不得运行、不得作为验收证据。**
> 本目录只是历史存档：不进入 pytest 收集、运行包、PyInstaller/打包清单与 CI，不得被活跃代码导入。
> Tk 截图、Tk 自检回执与旧 ARK-14 候选结论都不能作为当前 WPF 候选的验收证据。
> 需要 UI 证据时使用 `acceptance/verify_wpf.py`；需要打包证据时使用 `acceptance/verify_wpf_package.py`。

## 为什么封存

ARK-19 已把产品 UI 换成 C# / .NET 10 / WPF，但仓库仍保留 Tk 入口（`--legacy-tk`、Tk `--self-test`、
`--acceptance-output`）、Tk 模块、Tk 测试、Tk 验收脚本与 PyInstaller 构建链。后续 agent 与测试仍可能
打开旧面板，产出与 WPF 候选不一致的截图/回执，污染验收证据链。ARK-27 因此把 Tk 整体归档。

## 归档清单

### 运行入口

| 文件 | 原职责 | 当前替代 |
| --- | --- | --- |
| `app_tk.py` | 旧 `app.py`：Tk `App`、托盘、HUD、设置窗、测量入口 | `app.py`（WPF 启动 + 无界面采集） |
| `gui_panel.py` | Tk `StatisticPanel` 绘制 | `desktop/ArkDPS.Desktop/StatisticPanel.xaml.cs` |
| `gui_history.py` | Tk 历史窗口 | `MainWindow.xaml.cs` 历史页 |
| `hud.py` | Tk `HudWindow` | `desktop/ArkDPS.Desktop/HudWindow.cs` |
| `settings_window.py` | Tk 连接设置窗 | `MainWindow.xaml.cs` 设置页 |
| `tray.py` | Win32 托盘图标线程 | `MainWindow.xaml.cs` 的 `NotifyIcon` |
| `ui_theme.py` | Tk 双主题配色 | `App.xaml.cs` 主题资源 |
| `statistics_screenshot.py` | Win32 GDI + 标准库 PNG | `desktop/ArkDPS.Desktop/Screenshot.cs` |
| `release_smoke.py` | 旧 Tk 便携包自检 | `acceptance/verify_wpf.py` + `verify_wpf_package.py` |
| `release_probe.py` | Tk GUI 测量采样 | 无（测量入口随 Tk 移除；实机性能另行验收） |
| `release.json` | 旧 PyInstaller 候选版本/平台配置 | 包内生成的 `release.json`（`tools/package_windows.py`） |

### 构建工具

- `tools/build_windows.py`：PyInstaller + Tk 便携候选构建。当前构建为 `tools/package_windows.py`（WPF 自包含 + Python worker）。

### 测试

`tests/` 下整体归档：`test_gui.py`、`test_gui_history.py`、`test_hud.py`、`test_tray.py`、`test_ui_theme.py`、
`test_window_restore.py`、`test_settings.py`、`test_settings_lifecycle_regressions.py`、
`test_settings_review_regressions.py`。

迁移说明：
- 合成样本 `sample()` 迁到 `tests/sample_data.py`，供仍活跃的 `tests/test_desktop_bridge.py`、`tests/test_stage_names.py` 使用。
- 会话清理报告断言迁到 `tests/test_shutdown_failures.py`（`collection_session.shutdown_report`）。
- 备份失败保护原文件的断言迁到 `tests/test_layout_store.py`。
- 其余 Tk 窗口/托盘断言由 WPF `Verification.cs` 与 `tests/test_wpf_contract.py` 覆盖。

### 验收脚本

`acceptance/` 下整体归档：`verify_gui.py`、`verify_history.py`、`verify_hud.py`、`verify_settings.py`、
`verify_ui_themes.py`。对应能力现由 `acceptance/verify_wpf.py` 与 `desktop/ArkDPS.Desktop/Verification.cs`
覆盖（历史页自动刷新、HUD 几何/锁定/透明度恢复、双主题、托盘回调、PNG 导出、重启恢复）。

### 资源与文档

- `assets/Tcl.txt`：Tcl 8.6 许可原文（旧 PyInstaller 构建复制进 `licenses/`）。
- `docs/windows-release-ark14.md`：旧 ARK-14 Tk 候选构建/自检/实机矩阵与历史验收记录。

## 使用规则

1. 不得运行本目录任何脚本或模块；它们依赖已移除的 Tk 运行路径与旧构建工具。
2. 不得从活跃代码（`app.py`、worker、tests、acceptance、tools）导入本目录模块。
3. 不得把本目录的历史回执、截图或结论当作当前候选证据。

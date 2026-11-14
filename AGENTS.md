# ArkDPS 开发代理指南

本文件是仓库硬规则。任务与规则冲突时，先指出具体冲突。[面向用户的 README](README.md)提供下载、启动和使用说明。

## 实现边界

- 产品 UI 统一使用 [desktop/ArkDPS.Desktop](desktop/ArkDPS.Desktop)（C# / .NET 10 / WPF）。采集、统计、历史与 worker 协议由 Python 负责。修改跨进程数据流时，按[桌面运行时](docs/desktop-runtime.md)的所有权边界追到数据源。
- 维护 WPF 入口。旧 Tk 面板已封存于 [archive/legacy-tk](archive/legacy-tk)，只用于追溯。不得新增或恢复 Tk 代码、测试、文档、构建或旧启动参数，不得导入 `tkinter` 或反向引用归档模块。归档内容只增不改，排除在测试、CI 和分发之外。涉及旧 UI 的需求转到 WPF 实现或验收。[封存决定](docs/adr/0005-seal-legacy-tk.md)

## 按任务查阅

| 任务 | 入口 |
| --- | --- |
| 产品行为、界面、HUD 和设置 | [V1 设计基线](docs/v1-design.md)、[面板模型](docs/panel-model.md)、[设置与布局](docs/settings-and-layout.md) |
| Worker 协议、进程与断连恢复 | [桌面运行时](docs/desktop-runtime.md)、[桌面桥接](desktop_bridge.py)、[采集会话](collection_session.py) |
| MuMu 定位、版本校验、事件观察 | [连接配置](docs/connection.md)、[事件采集与验收](docs/event-capture.md)、[原生采集](collector.py) |
| 伤害口径、来源归属、战局切换、归档 | [统计核心](docs/statistics-core.md)、[累计与战绩契约](docs/snapshot-adapter.md)、[战局生命周期](battle_lifecycle.py) |
| 实机复现与事件对账 | [实机实验手册](docs/experiment-playbook.md)、[MAA 作业目录](acceptance/maa/README.md) |
| Windows 构建、验收与分发 | [便携包构建与发布验收](docs/windows-release.md)、[Windows CI](.github/workflows/windows.yml) |

其他设计决定、研究记录和历史证据见[文档索引](docs/README.md)。新增、移动或废弃文档时更新该索引。

## 运行与验收

从仓库根目录执行。测试依赖见 [requirements-test.txt](requirements-test.txt)，Windows 事件客户端依赖见 [requirements-windows.txt](requirements-windows.txt)。

| 目的 | 命令 |
| --- | --- |
| 源码运行 | `start.cmd` 或 `python app.py` |
| 无界面采集 | `python app.py --capture-seconds <秒>`。连接和观察参数见 `python app.py --help` |
| Python 专项回归 | `python -m pytest <相关测试文件或用例> -q`。本机插件冲突时设置 `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'` 并加 `--assert=plain` |
| WPF 构建 | `dotnet build desktop/ArkDPS.Desktop/ArkDPS.Desktop.csproj --nologo` |
| 便携包构建 | `python tools/package_windows.py --output runtime/packages` |
| 交互式 WPF 验收与发布门禁 | [Windows 验收](docs/windows-release.md#验收) |

- 日常改动按影响范围运行最小相关回归。采集、统计和 worker 改动优先用相关测试、事件检查或日志回放，无关 UI 时跳过交互式 WPF 验收。全量回归留给用户明确要求、影响范围确实需要或隔离 CI 的发布门禁。
- `verify_wpf.py`、`verify_wpf_package.py` 及调用它们的脚本会移动鼠标并发送点击或拖动。优先在隔离 CI 运行。只有用户明确安排本机交互验收并确认桌面可以交给测试时，才启动或重跑这些脚本。发现指针被用户操作时停止本轮交互检查，记录桌面输入冲突。
- 保持 `tests/test_no_legacy_tk.py` 通过。不得删除测试、放宽断言或跳过归档范围之外的用例来制造通过。
- 当前候选的 WPF 验收使用对应提交的 WPF 回执和截图。旧 Tk 截图、回执及 ARK-14 结论仅作历史材料，引用时注明已封存。离线合成数据与实机证据分别登记。
- 实机结论记录游戏版本、候选提交、日期、证据路径和未覆盖机制。关卡数据记录来源及 SHA-256，MAA 坐标使用 `[列, 行]` 并说明原始坐标转换。

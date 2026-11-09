# 设置、托盘与窗口配置恢复（ARK-11）

实现于 `app.py`、`settings_window.py`、`layout_store.py`、`hud.py` 和 `tray.py`。本页固定 V1 的配置格式、恢复规则、托盘生命周期与事件观测开关语义；战斗数值、历史记录和实时状态不进入布局文件。

## 布局文件与版本

`LayoutStore` 默认读写 `runtime/layout.json`（不提交），原子替换写入。

```json
{
  "version": 2,
  "event_observation": true,
  "gui": {"x": 60, "y": 70, "width": 1000, "height": 760},
  "panels": [
    {"panel_id": "default", "category": "damage", "ranking": "total",
     "rate_basis": "recent_5s", "title": null}
  ],
  "huds": [
    {"panel_id": "default", "x": 120, "y": 140, "width": 410, "height": 360,
     "opacity": 0.75, "locked": true}
  ]
}
```

- 版本 1 是早期只含 `panels` 的 `PanelCollection` 文档；读取时迁移为版本 2（无 GUI/HUD、事件观测默认开启），下次保存写回版本 2。
- 无 `version` 但含 `panels` 的文档按版本 1 接受。未知或更高版本不静默覆盖：原文件移动为 `layout.json.corrupt-<时间戳>`，本次使用默认布局并显示诊断。
- 无法解析的 JSON、空面板列表或没有可用面板同样视为损坏：备份、诊断、默认布局。单个无效/重复面板或 HUD 条目会跳过并累计警告；HUD 必须引用仍存在的面板。
- 首次启动只创建“伤害 / 本局总量 / 近 5 秒 DPS”面板，且不打开 HUD。
- 透明度限制在 0.7–1.0；坐标绝对值上限 100000，尺寸上限 20000；`bool` 不作为坐标。

## 可达位置恢复

保存的是绝对虚拟桌面坐标（可为负）。恢复时 `clamp_geometry()` 选择与保存矩形重叠最大（无重叠时最近）的显示器工作区：

- 完全可见的窗口保持原位置。
- 显示器移除、分辨率或 DPI 改变导致窗口不再放得下时，宽高缩到工作区大小，位置推回工作区内；HUD 不小于当前字体缩放下的最小尺寸，GUI 不小于 `root.minsize()`。
- 无法读取显示器列表时保持保存值，不阻塞启动。

GUI 位置在窗口正常状态时由 `<Configure>` 记录；隐藏/最小化期间不覆盖最后有效值。HUD 在拖动、缩放、透明度和锁定变化后通知应用保存，写盘有 600 ms 防抖。

## 托盘与关闭/退出

- 托盘使用 Win32 `Shell_NotifyIcon`，仅标准库 `ctypes`，图标为 `assets/arkdps.ico`；菜单为“打开主窗口 / 退出 ArkDPS”。双击图标恢复 GUI。
- 托盘可用时，关闭 GUI 只 `withdraw` 主窗；已打开的 HUD 和共享采集会话继续运行，随后保存布局。托盘回调或双击恢复主窗。
- 托盘不可用（无桌面 shell、创建失败）时记录诊断，关闭 GUI 退回原行为：有 HUD 时最小化到任务栏，没有 HUD 时结束应用。
- 只有显式“退出应用”或托盘“退出 ArkDPS”才停止应用：保存布局、停止采集线程、销毁 HUD、删除托盘图标、释放本工具创建的 ADB 转发。退出时 `verify_shutdown()` 核对采集会话已关闭、观察器 script/session 已释放、`ForwardRegistry.owned` 为空，结果写入 `runtime/app-*.log` 与 `App.shutdown_report`。
- 关闭 HUD 仅关闭该显示；删除源面板前关闭其 HUD，再保存布局。关闭最后一个 HUD 且 GUI 处于隐藏/最小化时恢复 GUI，避免只剩托盘图标。

托盘图标消息循环位于独立线程；菜单命令通过应用事件队列回到 Tk 主线程，绝不从托盘线程直接操作 Tk。

## 连接设置与多实例

设置页（主窗“设置”）编辑 `ConnectionConfig` 并写入 `runtime/connection.json`（格式见 [连接配置](connection.md)）：

- 自动检测开启时按 `adb_path` 扫描实例；唯一已认证实例自动连接。多实例时列出候选；已保存地址若仍在候选中则优先使用，否则要求玩家选择。
- 自动检测关闭时必须填写实例地址，作为手动目标；缺失时拒绝应用并提示。
- “检测实例”在后台线程执行只读 adb 检测，结果回到主线程；失败只显示诊断，不关闭设置窗。
- 命令行 `--adb` / `--serial` 只覆盖本次启动，不写回设置文件。命令行采集模式仍使用默认 adb 与 `127.0.0.1:16384`。

## 事件观测开关

设置中的开关默认开启（认证环境），写入布局文件的 `event_observation`；`--no-events` 仍可单次关闭，`--frida-remote` 可覆盖端点。布局的自动保存与退出保存只写回该长期偏好，不会把本次启动的命令行覆盖结果写回；只有设置页的“应用并重新连接”会更新它。设置页用 `event_setting_status()` 区分：

| 状态 | 显示 |
| --- | --- |
| `disabled` | 已关闭 · 仅使用伤害/治疗快照；护盾与接敌区间未知，不记零 |
| `dependency_missing` | Frida 依赖或端点不可用 · 快照降级继续 |
| `interrupted` | 事件中断/结束 · 保留已覆盖区间，快照继续 |
| `unsupported_version` | 游戏版本不支持/未安装 · 拒绝发布任何实时数值 |
| `active` | 事件观测运行中 · 护盾与接敌区间只含已观察区间 |

未覆盖区间始终显示为未知；快照降级提示只说明伤害/治疗累计继续，不把事件指标写成零。

## 验证

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests/test_layout_store.py tests/test_settings.py tests/test_tray.py tests/test_window_restore.py -q --assert=plain
python -m pytest tests -q --assert=plain
python acceptance/verify_settings.py --output runtime/settings-validation
```

验收脚本用合成统计和合成多实例载荷驱动真实 Tk 窗口与系统托盘，再做一次**子进程重启**核对面板、HUD 位置/尺寸/透明度/锁定和 GUI 几何；移除显示器与 DPI 变化以注入工作区模拟，另外真实创建并释放一个本工具自己的 adb 转发。

## 未覆盖边界

- 物理右键菜单点击、物理拔插显示器、运行中修改 Windows 缩放仍需宿主机人工验收；脚本已明确记录。
- 本次验收未启动游戏采集会话，因此真实 Frida 观察器与真机会话的退出清理仍需在实机显式退出时复核；清理核对逻辑本身在退出路径运行并有单元测试。
- 恢复窗口配置不等于恢复此前未观测的战斗事件；布局文件不包含任何战斗数据。

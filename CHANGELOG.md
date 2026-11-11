# Changelog

## 1.0.0-rc.1 — Windows / MuMu candidate

- 实时 GUI 多面板和置顶 HUD，共用伤害、治疗、有效护盾和接敌速率口径。
- MuMu 自动检测、版本门禁、事件不可用时的快照降级；不支持版本拒绝实时数值。
- 自动换局和最近 100 局本地战绩、JSON 与统计 PNG 导出。
- 连接设置、托盘生命周期和 GUI/HUD 布局恢复。
- Windows x64 WPF 便携 ZIP、精确提交/逐文件摘要/产物 SHA-256、WPF 离线验收及 Windows CI。
- 新增敌情页：已入场敌人属性、敌方总血量与「总血量 / 我方累计伤害 / 剩余血量」游戏时间曲线；新增干员明细的技能/非技能伤害拆分。两项均以未知代替补零，且不改变既有干员统计口径。
- Tk 面板已封存（ARK-27）：产品 UI 只有 WPF，旧 Tk 入口、测试、验收脚本与构建工具归档到 `archive/legacy-tk`，不再产出 `ArkDPS.exe`。

这是待独立验收的候选，不是已授权对外发布。认证与证据边界见
[Windows 候选构建和验收](docs/windows-release.md)，统计机制范围仍由
[开发备忘](docs/development-notes.md)和[实测报告](docs/capability-validation.md)定义。

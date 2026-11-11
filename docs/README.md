# ArkDPS 文档导航

根目录 [README](../README.md) 是项目入口；本页按用途索引项目文档。新增、移动或废弃文档时同步更新本页。

## 先读

- [V1 设计基线](v1-design.md) — 已确认的产品规则、GUI/HUD、统计口径与验收边界。
- [产品设计访谈](design-interview.md) — 设计问题与取舍过程。
- [开发备忘](development-notes.md) — 范围决定、已知限制和阶段结论。

## 实验与实证

- [单局战斗事件与计算要素挖掘](battle-event-research.md) — 当前采集边界、公开线索、事件矩阵和验证顺序。
- [实机实验手册：MAA 作业、关卡路线与守门](experiment-playbook.md) — 怎么选图、查路线、写作业、守门和判定。
- [底层能力实测](capability-validation.md) — 护盾、召唤归属、历史恢复的首轮实测。
- [累计恢复与召唤归属补充实验](recovery-validation.md) — 设备端观察器、并发召唤、技能配置纠错。
- [OF-2 只读采集实机测试站位](of2-test-plan.md) — OF-2 路线、时序与具体站位。
- [重连后的历史数据](reconnect-research.md) — 已知证据与未决能力。
- [MAA 实验作业目录](../acceptance/maa/README.md) — 可执行作业、回调和失败记录。

## 调研与对标

- [同类 DPS 工具架构调研](dps-tool-prior-art.md) — Details!/Skada/ACT/Deucalion/ArcDPS 的可借鉴设计与 ArkDPS 差距。

## 发布

- [Windows WPF 便携包构建与发布验收](windows-release.md) — 当前唯一分发入口：构建、验收、CI 与许可边界。
- [旧 ARK-14 Tk 候选归档](archive/legacy-tk/windows-release-ark14.md) — 已封存的 PyInstaller/Tk 候选记录，只作历史追溯。
- [变更日志](../CHANGELOG.md) — 候选版本的变化与交付状态。

## 实现契约

- [WPF 桌面运行时与同包分发](desktop-runtime.md) — C#/Python 所有权、唯一 stdio v1 协议、运行时与可选依赖打包、离线验收边界。

- [连接配置与 MuMu 自动检测](connection.md) — 连接目标、版本门禁和诊断。
- [统计核心输入契约](statistics-core.md) — 离线统计核心的输入与口径。
- [累计、生命周期与本地战绩](snapshot-adapter.md#本地战绩ark-8) — 冻结交接、版本化 BattleRecord、最近 100 局读取及故障诊断。
- [实时事件采集入口与验收](event-capture.md) — 可选观察器、共享采集入口、覆盖语义和宿主机验收命令。
- [GUI/HUD 共用面板模型](panel-model.md) — 面板配置、排序、柱长与占比口径（Tk 实现已封存）。
- [设置、托盘与窗口配置恢复](settings-and-layout.md) — 布局版本与迁移、可达位置、托盘生命周期、事件观测开关与退出清理（Tk 历史章节已封存）。
- [ADR 0001：总伤害包含过量部分](adr/0001-damage-totals-include-overkill.md)
- [ADR 0004：过量伤害作为独立事件派生指标](adr/0004-overkill-as-separate-event-derived-metric.md)
- [ADR 0003：WPF 展示与 Python 子进程](adr/0003-wpf-python-worker.md)
- [ADR 0002：接敌伤害与治疗使用独立活跃区间](adr/0002-separate-engagement-windows.md)

## 维护约定

- 实测结论写清版本、日期、证据路径和未覆盖机制；不把计划或作业文件存在说成验收通过。
- 引用关卡数据时写来源和 SHA-256；坐标统一用 MAA `[列, 行]`，并说明原始坐标转换。

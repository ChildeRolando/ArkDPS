# ArkDPS 文档导航

从 [README](../README.md) 下载和使用 ArkDPS。开发前按任务查阅 [AGENTS.md](../AGENTS.md)。

## 先读

- [V1 设计基线](v1-design.md) — 已确认的产品规则、GUI/HUD、统计口径与验收边界。
- [产品设计访谈](design-interview.md) — 设计问题与取舍过程。
- [开发备忘](development-notes.md) — 范围决定、已知限制和阶段结论。

## 实验与实证

- [手动对局性能计时](performance-timings.md) — 低开销开关、原生/宿主/WPF 阶段、日志解释与 rc.43 私有计时包验收。
- [PC 原生现场采集与单消费者](native-capture-worker.md) — 现场快照、队列、共享归约、采样调度、单一事件存储及重构收口变更控制。
- [敌方 HP 轻量采集合同](native-hp-lightweight-contract.md) — 按产品需求取消精确敌方回血指标，保留结算、关键事件与快照刷新；计划边界及严格水位。
- [按业务消费者裁剪原生伤害取证](native-capture-demand.md) — 全伤害/公式钩子字段账、逐边取证合同、计数与混合负载验证。
- [HP 叶子事实组研究合同](native-hp-leaf-facts-contract.md) — 已移出产品装配的研究原型、原断言与未完成边界。

- [官服 PC Frida 生命周期与恢复约束](native-pc-frida-state-machine.md) — 状态转换、资源所有权、请求世代与覆盖验证。

- [单局战斗事件与计算要素挖掘](battle-event-research.md) — 当前采集边界、公开线索、事件矩阵和验证顺序。
- [实机实验手册：MAA 作业、关卡路线与守门](experiment-playbook.md) — 怎么选图、查路线、写作业、守门和判定。
- [底层能力实测](capability-validation.md) — 护盾、召唤归属、历史恢复的首轮实测。
- [累计恢复与召唤归属补充实验](recovery-validation.md) — 设备端观察器、并发召唤、技能配置纠错。
- [OF-2 只读采集实机测试站位](of2-test-plan.md) — OF-2 路线、时序与具体站位。
- [重连后的历史数据](reconnect-research.md) — 已知证据与未决能力。
- [MAA 实验作业目录](../acceptance/maa/README.md) — 可执行作业、回调和失败记录。

## 调研与对标

- [官服 PC 采集预研](native-pc-research.md) — CE-5 演习的日志与只读访问结果、复现命令和剩余验证项。
- [官服 PC 只读适配可行性报告](native-pc-feasibility.md) — 与 MuMu 的能力差距、实机证据与验收条件。
- [官服 PC 实机验收手册](native-pc-player-test-guide.md) — 玩家操作、完整采样命令及待准备的实验。
- [同类 DPS 工具架构调研](dps-tool-prior-art.md) — Details!/Skada/ACT/Deucalion/ArcDPS 的可借鉴设计与 ArkDPS 差距。

## 发布

- [rc.42 重构版发布说明](release-rc42.md) — 共用采集与统计链路收敛、小局交接、历史兼容及测13手动迁移说明。
- [rc.14 发布说明与实机覆盖](release-rc14.md) — PC/MuMu 整合、始终管理员启动、一次迁移与实机验证边界。
- [Windows WPF 便携包构建与发布验收](windows-release.md) — 当前唯一分发入口：构建、验收、CI 与许可边界。
- [旧 ARK-14 Tk 候选归档](archive/legacy-tk/windows-release-ark14.md) — 已封存的 PyInstaller/Tk 候选记录，只作历史追溯。
- [变更日志](../CHANGELOG.md) — 候选版本的变化与交付状态。

## 实现契约

- [从采集钩子到展示的重构规划](collection-refactor-plan.md) — 基于测12源码冻结的并行实现清单、状态机、唯一归属与删除次序。
- [采集链重构实施记录](collection-refactor-implementation.md) — D01–D26 的代码收敛、阶段回归、私测交付与仍未完成的实机性能验收。

- [WPF 桌面运行时与同包分发](desktop-runtime.md) — C#/Python 所有权、唯一 stdio v1 协议、运行时与可选依赖打包、离线验收边界。

- [连接配置与 MuMu 自动检测](connection.md) — 连接目标、版本门禁和诊断。
- [统计核心输入契约](statistics-core.md) — 离线统计核心的输入与口径。
- [累计、生命周期与本地战绩](snapshot-adapter.md#本地战绩ark-8) — 冻结交接、版本化 BattleRecord、最近 100 局读取及故障诊断。
- [实时事件采集入口与验收](event-capture.md) — 可选观察器、共享采集入口、覆盖语义和宿主机验收命令。
- [事件探针与共享统计接口](event-probe-interface.md) — MuMu/PC 探针契约、质量声明、共享统计口径和离线一致性回归。
- [PC 自研事件后端内部候选](internal-event-backend.md) — 合作生产者接入、受控验证及候选包。
- [PC ArkPie 事件后端](native-pc-native-hooks.md) — 设置与启动、标准便携包集成、同局对账及实机验收记录。
- [探针复用审计与修复边界](event-probe-reuse.md) — 实际 MuMu 基线、技能与嵌套结算断点、局部缺口及共同业务回归。
- [GUI/HUD 共用面板模型](panel-model.md) — 面板配置、排序、柱长与占比口径（Tk 实现已封存）。
- [设置、托盘与窗口配置恢复](settings-and-layout.md) — 布局版本与迁移、可达位置、托盘生命周期、事件观察开关与退出清理（Tk 历史章节已封存）。
- [ADR 0001：总伤害包含过量部分](adr/0001-damage-totals-include-overkill.md)
- [ADR 0004：过量伤害作为独立事件派生指标](adr/0004-overkill-as-separate-event-derived-metric.md)
- [ADR 0003：WPF 展示与 Python 子进程](adr/0003-wpf-python-worker.md)
- [ADR 0002：接敌伤害与治疗使用独立活跃区间](adr/0002-separate-engagement-windows.md)
- [ADR 0006：元素爆条伤害按最后元素损伤来源归属](adr/0006-elemental-burst-attribution.md)

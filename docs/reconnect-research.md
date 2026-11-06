# 重连后的历史数据：已知证据与未决能力

2026-11-07。只读调查；没有更改游戏状态或启用额外日志。

## 结论

已验证可重新读取本局累计伤害，尚未验证能恢复每次输出的发生时间。客户端执行战斗，即使只在结算时通信，也不必保留每一步的全部结果；网络通信时机本次未核实。累计状态、可重放的输入记录和直接可读的事件历史是三种不同能力。

产品仍以调查完整恢复为目标，没有将“仅已观察时段”降级方案视为用户已批准，也没有断言完整恢复不可能。

## 类型证据

来源：[上游固定版本 PC 类型转储](https://github.com/Tim23333/Arknights_timer/blob/5734c30bf54780056ff758505380322c0cf519fe/game_data/reference/pc_x64/dump.cs)。PC 类型用于研究语义，不能直接作为 Android 内存布局。

- CharAdvancedStats.snapshots 是 List<CharacterSnapshot>（397984–397993）。CharacterSnapshot 属于 AntiCheat，记录时间戳、属性、天赋、技能与特性（541909–541936）；MAX_SNAPSHOTS_FOR_SINGLE_CHARACTER 为 1（398172）。这条快照不是逐次输出历史。
- BattleLogger.Journal 包含 metadata、squad、操作 logs、randomSeed、runeList（397914–397921）。这些是重放输入的线索，不等于无需重新模拟便可读取完整伤害时间线。
- BattleVerboseRecorder 的 LogItem 包含 frameCnt、source、target、modifier，modifier 含 realDelta、afterValue；类型包括 MODIFY_HP、MODIFY_EP，存在 m_detailLogs（398388–398484）。这条结构有恢复逐事件历史的可能，但类型存在不能证明正常游戏启用了它。

## 当前 Android 实机观察

Android 2.7.91 字段转储明确 BattleLogger.m_verboseRecorder 位于 Logger+0x38。本次使用独立只读连接，未干扰主统计窗；当前演习 frame 24，游戏时间约 0.80 秒，m_verboseRecorder 为 0x0。读取前后战局、帧一致，设备侧帧校验通过。完整观察见 ../acceptance/verbose-pointer-observation.json。

因此此局此刻没有实例化这条记录器。此结果不能推广为所有模式、战斗阶段或其他尚未发现路径都没有历史记录；也不足以承诺本产品可补齐全部事件。

## 下一步验证范围

调查其他可读历史来源；若发现实际事件列表，验证开始/结束帧、来源归属、长局截断、换局清空和停采期间记录是否仍在。伤害、治疗和护盾来源分别验收。只有整局事件恢复通过后，才能承诺重建整局接敌区间与重连时最近 5 秒的输出。

## 1.5 秒指标的事实更正

本地固定版本 characters.json 的 baseAttackTime：空爆、陨星为 2.8 秒，Lancet-2 为 2.85 秒；[空爆资料](https://wiki.biligame.com/arknights/%E7%A9%BA%E7%88%86)也标注 2.8 秒。1.5 秒作为用户选择的统一活跃阈值保留，但不是基础攻击间隔的上限。慢攻持续输出可能被分割为多个活跃区间，因此结果可能高于按连续交战时间计算的平均值。

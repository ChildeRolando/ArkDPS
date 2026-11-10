# 统计核心输入契约

`stats_core.Statistics` 是单局、由调用方提供可靠游戏时钟和数据的计算器，不读取游戏。窗口与命令行通过 `CollectionSession` 的生命周期模式共用 `BattleRuntime`。生命周期签发的 `feed:generation` 同时用于快照适配器、事件流、冻结结果和归档交接，每局只持有一个统计实例。认证后默认尝试临时事件观察，失败继续原生累计。ARK-5 已完成独立验收并合入；本次 ARK-6 集成候选的跨局实机验收仍未完成，不能借用 ARK-5 日志替代。入口与证据说明见[实时事件采集](event-capture.md)。

## 权威来源和顺序

1. 创建实例时传入本局可靠观察开始的游戏时间；已知事件流从同一时刻完整覆盖时才设 `event_coverage=True`。默认事件不可用，护盾和事件指标为未知，不是零。调用方负责保证这是可靠覆盖；研究探针代码不是本接口的产品采集器。
2. 以 `register_source(statistic_key, owner)` 注册已可靠识别的累计统计键和宿主，稳定维持至本局结束。键是累计记录身份，不是每个临时实体的身份：多个实体共用一个键只注册并提交一次；不同键可归属同一宿主。未知归属不得猜测，冲突被拒绝。
3. 在覆盖起点提交首个 `snapshot(start_time, {key: {'damage': D, 'healing': H}})`，即原生累计基线，包含启动前输出。后续同结构快照是**替换性的累计值**，不是再加一次的增量；宿主总量为其不同键的累计之和。缺失键保留上次可靠值，但 `counter_snapshot_complete=False`，其近期速率未知。新出现的非零键先作为该键基线，不能把原有数当成采集后输出。
4. `advance(t)` 推进可靠游戏时钟（暂停时不推进）。可靠事件调用 `event(id, t, key, kind, amount)`；事件必须按游戏时间有序、不能晚于当前时钟。伤害/治疗事件只驱动采集后的接敌分子和区间，**不加到累计快照总量或快照近期速率**；`shield_absorbed` 事件只累计实际吸收，不能以到期、覆盖、离场、剩余盾量或受盾者推算。重复事件 ID 且内容一致忽略，冲突拒绝。
   伤害事件可附带过量证据：`target`、原生有符号 `real_delta`（伤害为负）、`hp_before`、`hp_after`、`killed`、`target_type`、`damage_type`、仅作审计的 `shield_absorbed`。`amount` 是结算到 HP 的伤害（护盾/抵挡已在结算前扣除）。仅当 `killed` 为真且 HP 差值、符号与 `real_delta` 一致时，过量 = `amount - (-real_delta)`；其他事件、缺失字段或不一致证据使该干员过量保持未知，绝不补零。过量不改变总伤害、接敌分子或护盾累计。逐属性过量按原生 `DamageType`（1 物理、2 法术、3 真实、5 元素）分组；有非零过量但类型缺失或无法识别时，`overkill_damage_by_type=None`、`overkill_type_complete=False`，总过量仍可保留。
5. 首次基线与事件同帧时，必须先完成原子基线并确认事件发生在该基线之后，才接受该帧事件；无法判定先后的事件不应输入。下一帧快照自身会包含该事件，故不用再将事件加进快照。相同游戏时间的累计快照若发生变化会拒绝，调用方应等待可区分的下个游戏时刻，不能用时间不变的快照制造速率。
6. 明确发生采集缺口时，在最后可靠时间调用 `invalidate_snapshots()` 和/或 `invalidate_events()`。前者保留累计、从重新获得可靠快照的时刻重建近期窗口，不必再等满 5 秒；后者冻结事件覆盖终点及接敌已观察区间，停止接收事件，结果标记覆盖不完整。事件通道落后于快照时可向 `invalidate_events(end_time)` 传入最后可靠事件时间，须不早于已接受事件且不晚于当前时钟。未知缺口不能靠核心自动推测，调用方必须及时报告。缺口不能回填零；该实例没有恢复事件流的接口。
7. `finish(t)` 用战斗结束游戏时刻截断区间。事件尾部只有实际经过的时间计入；结果中的 `coverage_start`、`shield_coverage_start`、`event_coverage_end`、`event_coverage_complete`、`counter_snapshot_complete`、`overkill_damage`、`overkill_coverage_complete`、`overkill_coverage_start` 和 `overkill_coverage_end` 供展示和持久化传播，使用方必须保留不完整标记。未知过量为 `None`，不是零。
8. 终局后客户端可能在同一逻辑帧/时间继续结算累计。调用方确认**同一已结束战局的原生状态 3** 后，才可用 `correct_final_totals(counters)` 接受已注册来源的单调最终累计修订。该方法要求已有观察基线且已 `finish`，不改变冻结时间、事件、护盾或接敌区间；所有快照近期速率标为未知。普通 `snapshot` 的同时间冲突及结束后拒绝规则保持不变。字段不全、未注册来源、非法数值或累计回退时事务性拒绝；不能用于修复活动战局、掉线期间或新一局的数据。

`recent_damage` / `recent_healing` 是最多 5 个观察游戏秒的累计快照差分速率：不足 5 秒使用实际观察时长，零时长未知。5 秒边界位于两个样本间时作线性插值，仅当两个样本相距不超过 5 秒（即窗口长度；更长视为采集缺口）；这样 2× 下约 1.7 秒的正常采样间隔仍能给出速率。同一宿主有多个统计键时，以各键本次可靠观察起点中最晚者作为共同窗口起点，以同一时长计算并汇总；新键先建立自身基线。采样差分是估计，不是事件时间。调用方确认连续但长间隔的两个样本在短窗口仍按实际差分估算；缺口必须显式报告。伤害/治疗接敌区间分别按事件产生并合并 `[t, t+1.5]`，分子仅是对应已观察事件，零活跃时长未知。

最小离线调用：

```python
from stats_core import Statistics

meter = Statistics('battle-1', 30, event_coverage=True)
meter.register_source('host-stat', 'host')
meter.snapshot(30, {'host-stat': {'damage': 1000, 'healing': 500}})
meter.advance(32.3)
meter.event('hit-1', 31, 'host-stat', 'damage', 230)
meter.snapshot(32.3, {'host-stat': {'damage': 1230, 'healing': 615}})
row = meter.result()['rows']['host']
# damage=1230; recent_damage=100; observed_damage=230
```

事件 ID、来源映射、首次快照与事件的顺序、覆盖缺口识别由采集适配层负责；接入代码存在不代表实时指标已经通过实机验收。

`event_stream.EventStream` 是接收端的离线协议实现：调用方先证明基线后的同局帧与序号屏障，再连续提交事件；重复内容忽略，序号缺口、来源冲突和时钟倒序关闭覆盖。水位只有在生产端保证此前事件全部送达时才有意义，水位可推进最后可靠覆盖时间，并拒绝后续较旧的事件。

`event_capture.LiveEventCapture` 与 `event_probe_android2791.js` 的屏障协议检查相同帧、时钟、序号、回调入口计数及无在途回调。生命周期模式在单例读取前后核对后携带屏障和 drain；消费者先拒绝旧代次，再把同局事件输入唯一适配器。原 ARK-5 路径已有推进中实机对账；ARK-6 改变了跨局所有权，须对新的精确候选取证。未验证的护盾机制须关闭覆盖而不能计作零。

## 近期窗口证明

`Statistics.result()` 的每行 `recent_damage_window` / `recent_healing_window` 在对应速率有效且完整覆盖 `[max(coverage_start, game_time - 5), game_time]` 时提供 `(start, end)`，否则为 `None`。新来源或采集缺口后的短窗口速率仍可用，但不能据此证明全队同窗口占比。窗口起止使用游戏秒数。
## 已结束战斗的累计修订

普通 `snapshot()` 的时钟与结束约束保持有效。只有显式的 `settle_snapshot()` 可以修订已结束战斗的原生累计：调用方必须先确认快照仍属于相同会话、结束帧、结束时间与结算状态；核心校验来源、字段、有限数及累计不下降后一次性更新。该 API 不接收新事件，不推进游戏时间，也不重开覆盖和参战区间。

受修订影响的来源只保留结束时刻的累计点，其近期速率未知，避免把晚到累计伪装成零时长爆发。未变化的来源保留历史；完全相同的修订是无操作。适配器以 `snapshot_revision` 对外标识累计或可见归属变化，并在同一次发布中同步原生与统计值。`tests/test_settlement_snapshot.py` 和会话发布回归固定该约束。

生命周期终局入口 `correct_final_totals()` 复用 `settle_snapshot()` 的事务校验，并将所有来源的近期速率置为未知；重复的相同终局值不增加修订号。首个终局样本先处理可靠事件水位，再结束统计；后来的同帧修订不能改变事件序号、结束时刻和已观察事件。确认离场/新代次后才交接归档，归档仍携带覆盖状态及最后可靠样本。

# 统计核心输入契约

`stats_core.Statistics` 是单局、由调用方提供可靠游戏时钟和数据的计算器，不直接读取游戏或控制窗口。累计适配器及生命周期入口负责调用，产品接入边界见 [snapshot-adapter.md](snapshot-adapter.md)。每局创建新实例；换局或时钟回退由调用方停止旧实例并新建，不能用旧实例跨局累加。本文件定义离线输入契约，不是实机采集、真实事件完整性或全部机制兼容性的验收声明。

## 权威来源和顺序

1. 创建实例时传入本局可靠观察开始的游戏时间；已知事件流从同一时刻完整覆盖时才设 `event_coverage=True`。默认事件不可用，护盾和事件指标为未知，不是零。调用方负责保证这是可靠覆盖；研究探针代码不是本接口的产品采集器。
2. 以 `register_source(statistic_key, owner)` 注册已可靠识别的累计统计键和宿主，稳定维持至本局结束。键是累计记录身份，不是每个临时实体的身份：多个实体共用一个键只注册并提交一次；不同键可归属同一宿主。未知归属不得猜测，冲突被拒绝。
3. 在覆盖起点提交首个 `snapshot(start_time, {key: {'damage': D, 'healing': H}})`，即原生累计基线，包含启动前输出。后续同结构快照是**替换性的累计值**，不是再加一次的增量；宿主总量为其不同键的累计之和。缺失键保留上次可靠值，但 `counter_snapshot_complete=False`，其近期速率未知。新出现的非零键先作为该键基线，不能把原有数当成采集后输出。
4. `advance(t)` 推进可靠游戏时钟（暂停时不推进）。可靠事件调用 `event(id, t, key, kind, amount)`；事件必须按游戏时间有序、不能晚于当前时钟。伤害/治疗事件只驱动采集后的接敌分子和区间，**不加到累计快照总量或快照近期速率**；`shield_absorbed` 事件只累计实际吸收，不能以到期、覆盖、离场、剩余盾量或受盾者推算。重复事件 ID 且内容一致忽略，冲突拒绝。
5. 首次基线与事件同帧时，必须先完成原子基线并确认事件发生在该基线之后，才接受该帧事件；无法判定先后的事件不应输入。下一帧快照自身会包含该事件，故不用再将事件加进快照。相同游戏时间的累计快照若发生变化会拒绝，调用方应等待可区分的下个游戏时刻，不能用时间不变的快照制造速率。
6. 明确发生采集缺口时，在最后可靠时间调用 `invalidate_snapshots()` 和/或 `invalidate_events()`。前者保留累计、从重新获得可靠快照的时刻重建近期窗口，不必再等满 5 秒；后者冻结事件覆盖终点及接敌已观察区间，停止接收事件，结果标记覆盖不完整。未知缺口不能靠核心自动推测，调用方必须及时报告。缺口不能回填零；该实例没有恢复事件流的接口。
7. `finish(t)` 用战斗结束游戏时刻截断区间。事件尾部只有实际经过的时间计入；结果中的 `coverage_start`、`shield_coverage_start`、`event_coverage_end`、`event_coverage_complete` 和 `counter_snapshot_complete` 供展示和持久化传播，使用方必须保留不完整标记。
8. 终局后客户端可能在同一逻辑帧/时间继续结算累计。调用方确认**同一已结束战局的原生状态 3** 后，才可用 `correct_final_totals(counters)` 接受已注册来源的单调最终累计修订。该方法要求已有观察基线且已 `finish`，不改变冻结时间、事件、护盾或接敌区间；所有快照近期速率标为未知。普通 `snapshot` 的同时间冲突及结束后拒绝规则保持不变。字段不全、未注册来源、非法数值或累计回退时事务性拒绝；不能用于修复活动战局、掉线期间或新一局的数据。

`recent_damage` / `recent_healing` 是最多 5 个观察游戏秒的累计快照差分速率：不足 5 秒使用实际观察时长，零时长未知。5 秒边界位于两个样本间时作线性插值，仅当两个样本相距不超过 1 秒；否则未知。同一宿主有多个统计键时，以各键本次可靠观察起点中最晚者作为共同窗口起点，以同一时长计算并汇总；新键先建立自身基线。采样差分是估计，不是事件时间。调用方确认连续但长间隔的两个样本在短窗口仍按实际差分估算；缺口必须显式报告。伤害/治疗接敌区间分别按事件产生并合并 `[t, t+1.5]`，分子仅是对应已观察事件，零活跃时长未知。

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

事件 ID、来源映射、首次快照与事件的顺序、覆盖缺口识别均由适配器及采集入口负责；不能仅据离线核心的接口宣称原生事件已完整覆盖。

## 同帧终局累计的回归证据

`tests/fixtures/lifecycle-terminal-of8.json` 保留宿主机 ARK-5 候选 `aef605125f6f02e9fe09ed3404e3f46249769f1c` 的两个原始样本及编队：OF-8，第 2964 帧、游戏时间 `98.79990577697754`，两次原生状态均为 3，煌累计由 `28985.8828125` 变为 `31899.103515625`。来源日志 `of8-aef6051-01/product/capture.jsonl` 的 SHA-256 为 `066f2b1224f530e0c73465f7a81ea88b017d9369a3ee44292c9b17c060e2f027f`，零起点记录下标 1855 / 1858。

`tests/test_terminal_totals.py` 用该字节解析后的原始快照验证最终累计修订不推进游戏时间、不制造近期速率，并保留普通快照的拒绝规则；`tests/test_battle_lifecycle.py::test_terminal_same_frame_revisions_are_kept_until_confirmed_scene_exit` 在旧实现上失败、修复后通过。借用这组证据只固定已发现的回归，不替代 ARK-6 原生生命周期采集路径的完整实机验收。

## 近期窗口证明

`Statistics.result()` 的每行 `recent_damage_window` / `recent_healing_window` 在对应速率有效且完整覆盖 `[max(coverage_start, game_time - 5), game_time]` 时提供 `(start, end)`，否则为 `None`。新来源或采集缺口后的短窗口速率仍可用，但不能据此证明全队同窗口占比。窗口起止使用游戏秒数。

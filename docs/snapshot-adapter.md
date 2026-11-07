# 累计快照适配与归属映射

实现于 `snapshot_adapter.py` 与 `roster.py`。它把 `Collector.read()` 的同局同帧累计快照和 `Collector.read_roster()` 的编队/在场信息适配到离线 `Statistics` 核心，供后续 GUI/HUD 工单消费。

## 输入

- 累计快照：`session`、逻辑帧、游戏时间、每个统计条目的四类伤害与治疗累计。
- 编队快照：`BattlePlayerData.characters` / `tokens` 中的 `prefabKey`、`uniqueId`、`isToken`、`tokenOrHostKey`、`tokenOrHostUniqueId`，以及 `UnitManager` 的在场单位。读取在一个帧守卫事务内完成，逻辑帧变化则整体拒绝。
- 已验证版本的在场容器使用 `UnorderedArray` 布局；其读取失败时拒绝快照，不能用未经验证的 `List<T>` 偏移下的空结果替代，否则会遗漏地图单位的同键冲突。
- 两次读取是独立事务；调用方须确保同一战局，`update` 在修改状态前校验累计帧与编队帧完全相同，不同或缺失时抛 `FrameMismatch`，调用方应重读配对。编队的非空玩家、数组计数、槽位、条目或关键键读取不完整时抛 `RosterUnavailable`；在场列表不可读、在场槽位数据不完整、非编队预置非召唤单位的统计键不可读或在场槽位列表格式不完整时也拒绝，以免遗漏同键冲突。只有已确认的空槽才可跳过。读取失败按缺口处理，保留上次可靠累计。

## 稳定统计键与宿主

- 统计键使用客户端统计条目的 `prefabKey`（如 `char_242_otter`、`token_…`），同一统计条目只注册一次。
- 非召唤条目归属自身；召唤条目仅当 `tokenOrHostKey` 与 `tokenOrHostUniqueId` 同时指向唯一宿主时归并到该宿主。键或 UID 不一致、多个候选、同一键出现冲突归属或任一记录无法确认时一律视为未知，不猜测、不发布该条目的新累计；已缓存的累计保留但标为不完整。
- 在场列表中有明确预置标志及有效统计键的非召唤地图单位可独立归属。其退场后，已观察到的归属和累计保留到本局结束。首次采集时已退场且编队无记录的地图单位仍无法确认归属。若地图单位与玩家干员共用统计键，本局该键标记为冲突。
- 并发召唤实体共享同一统计键时只提交一次累计；实体被清理后，编队映射仍可用于归属。
- 统计核心禁止同一会话内更改来源归属；后来的冲突证据只记录诊断，不静默改归属。

## 输出

每行除 `Statistics` 的总伤害/总治疗/近期速率/接敌速率/覆盖信息外，还包含：

- `damage_by_type`：按稳定来源键分别保留物理/法术/真实/元素四类累计，再按宿主求和；`types_available` 表示至少一个来源有可靠分类，`types_stale` 表示任一已登记来源的分类不是当前值（包含缺失情况），保留各来源上次可靠值，部分和不代表完整分类。
- `present`：该归属是否有在场单位；默认使用最近接受的编队快照。在场列表不可读或非空槽位读取失败时，整个归属快照被拒绝。
- `sources`：归并到该干员的稳定统计键。
- `unknown_sources`、`unmapped_tokens`、`ambiguous_sources`、`missing_fields`、`mapping_conflicts`：无法可靠归属或字段缺失的诊断。存在统计条目但完全无法归属时拒绝发布，而不是输出零。

## 时间与缺口

- 首帧建立基线，近期速率从开始观察即计时，不足 5 秒使用实际观察时长；暂停或重复帧不生成新快照，也不制造速率。
- 读取失败由调用方调用 `on_gap()`：立即保留累计并将近期速率设为未知、累计快照标为不完整，重建近期窗口；事件覆盖关闭，接敌指标标记不完整。
- 战局标识变化或游戏时钟回退时结束旧序列并新建观察序列，不把两局累计混在一起；时钟回退先于重复帧判定，即使新旧局恰好复用帧编号也不能忽略回退。直接调用适配器不能区分延迟旧样本与真正回退；产品入口必须先经过下面的观察代次边界。实际历史存储仍由 ARK-8 负责。

## 产品生命周期与归档交接（ARK-6，实机待验证）

`battle_lifecycle.py` 的 `BattleFeed` 是单线程采集所有者。`Collector.connect(lifecycle=True)` 定位 BattleController 和 GameFlowController 的单例槽，允许在菜单建立连接；每次采集前后核对进程出生标识、当前单例、原生对象和场景状态，配对同帧累计与编队。读取失败不是空单例。只有确认的空/销毁单例、FINISHED、关卡到结算的场景转换、初始化阶段、新对象链或时钟回退才构成结束/新观察边界；暂停和撤退/再部署不构成边界。

每份 `BattleObservation` 保留采集时签发的 `feed`、单调 `sequence` 和 `generation`。消费者不能在出队时给旧样本重新签代次。`BattleSessions` 丢弃外来 feed、旧序号和旧代次；内部 Statistics 会话使用 `feed:generation`，不把可复用地址作为持久战局 ID。进程重启须重新连接和定位元数据。所有地址、帧及时间完全相同且整个生命周期都未被观察到时，无法推断漏掉了几局；本入口不恢复未观察到的整局事件。

连接状态 `connection` 与战局 `state` 独立：读取/连接失败冻结统计、关闭可靠覆盖，保留累计；不会因超时把旧局判失败或清零。确认结束时读取仍可验证的最后累计，在最后可靠游戏时刻截断；终局读取失败保留旧累计并标记缺口。新局归属暂不可用也不能丢失旧局的归档信号。

`pop_archives()` 将待交接记录转移给调用方，每个已观察战局在本进程只交接一次。记录包含 `battle_id`、代次、结束原因、元信息、冻结统计与最后可靠样本的独立副本；消费端修改副本不能改变实时统计。未来持久化必须以 `battle_id` 幂等写入，此队列不是磁盘存储或跨进程事务承诺。

`BattleView.show_history(record)` 固定 GUI 所选记录；`gui()` 保留该选择，`hud()` 始终取当前局。`show_live()` 才恢复 GUI 跟随。这里交付数据契约，历史页面、HUD 窗口和实际存储仍由后续工单实现。原型 `app.py` 已共用 `BattleRuntime`，采集日志中的 `archive_ready` 仅作交接证据。

结果与演习必须有明确来源证据。元信息契约接收带 `evidence` 的 `stage_id`、`practice`（布尔或未知）和 `outcome`（`victory` / `defeat` / `abandoned` / `unknown`）。后续部分元信息不会抹掉已有可靠字段；有证据的互相冲突值保留未知并列出 `metadata_conflicts`，`evidence_sources` 保留来源。**当前原生生命周期读取器没有验证胜负、退出原因或演习字段，实际采集均保留未知**；FINISHED、`battle_finish`、掉线和空在场列表都不证明胜利或失败。离线带证据的元信息用例只验证契约，不是这些原生字段的实机支持声明。

### 复核入口

离线：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests -q --assert=plain`（PowerShell 用 `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'`）。`tests/test_battle_lifecycle.py` 保护换局、代次、重复交接、结尾截断、缺口、视图选择和重放；`tests/test_lifecycle_reader.py` 保护原生状态读取、单例变化拒绝与短读不变零。

取得独占实机窗口后，在固定候选检出运行下面的只读采集。看到 `lifecycle_ready` 后，由 MAA 按实验手册执行零理智的同图重开、切图、暂停、撤退/再部署和结束场景。采集器本身不操纵游戏。尚未取得本票实机证据，不能用既有研究探针或模拟夹具替代。

```powershell
python capture_battles.py --seconds 300 --output runtime/ark6-live-01 --adb 'C:/Program Files/Netease/MuMu Player 12/shell/adb.exe' --serial 127.0.0.1:16384
python capture_battles.py --replay runtime/ark6-live-01/observations.jsonl
Get-FileHash runtime/ark6-live-01/observations.jsonl -Algorithm SHA256
```

输出目录不能复用已有 `observations.jsonl`。重放会核对归档统计和末尾汇总；缺失交接、损坏记录或截断日志会失败。结束采集只冻结仍在观察的局，不把停止插件伪装成玩家中途退出。

## 实机证据（2026-11-08）

MuMu 12 / Android 2.7.91，1-7 演习实测（MAA Copilot 固定部署，非代理指挥）：捕获从逻辑帧 26 开始，共 203 次累计更新；夜刀累计 5030 → 5260，近期 DPS 在 46–92 之间随 5 秒窗口滚动；`present=True`，四类伤害与治疗字段可用；编队读取得到 12 个统计键，无归属错误或未知来源。暂停帧不产生新速率。

同日 OF-2（0 理智） guarded summon 作业：298 次更新，覆盖 0.67–86.77 游戏秒；梅尔累计 7798.2，来源键为 `char_242_otter` 与 `token_10004_otter_motter`，近 5 秒 DPS 114.0；山 3961.6、歌蕾蒂娅 5260.8；无未知来源、无冲突、无未映射召唤。并发水獭共享同一统计键，只计一次。

同日 OF-8：330 次更新，覆盖 0–98.8 游戏秒；闪灵治疗累计 11710.5，近期 HPS 最高 325.7（187 个非零速率样本）；煌伤害 31899.1，近期 DPS 峰值 2270.6；夜刀 11565 且 `present=False`，累计保留。无归属错误。

另一局 17-2 的在场列表包含一个不在编队映射中的 token；适配器将其列入 `unknown_present_unique_ids`，没有猜测归属。

## 边界

- 召唤归并已用 OF-2 实机验证并发/重新部署同键与宿主归并；其他召唤体系与地图 NPC 的覆盖仍按能力报告边界。
- 治疗速率与护盾事件需要事件采集接入（ARK-5）；本工单只验证累计快照与归属。
- 事件采集、自动换局归档、历史持久化不在本工单。

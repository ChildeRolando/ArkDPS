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
- `overkill_damage`、`overkill_coverage_complete`、`overkill_coverage_start`、`overkill_coverage_end`：事件派生的过量伤害及其覆盖。它只在事件流连续覆盖、且每次伤害都有目标、`realDelta`、HP 前后与击杀标记时发布；击杀事件还要求同帧护盾/抵挡吸收上下文已验证。任一证据缺失或矛盾（含吸收量大于来伤）时该行及全队合计为 `None`，`overkill_coverage_complete=False`。原生总伤害不受影响，过量也不从总伤害扣除。探针报告的未归属伤害事件同时把对应来源或全队的接敌分子置为未知（`observed_*`/`engagement_*`/`active_*` 为 `None`），不发布少算值。
- `present`：该归属是否有在场单位；默认使用最近接受的编队快照。在场列表不可读或非空槽位读取失败时，整个归属快照被拒绝。
- `sources`：归并到该干员的稳定统计键。
- `unknown_sources`、`unmapped_tokens`、`ambiguous_sources`、`missing_fields`、`mapping_conflicts`：无法可靠归属或字段缺失的诊断。存在统计条目但完全无法归属时拒绝发布，而不是输出零。

## 时间与缺口

- 首帧建立基线，近期速率从开始观察即计时，不足 5 秒使用实际观察时长；暂停或重复帧不生成新快照，也不制造速率。
- 读取失败由调用方调用 `on_gap()`：立即保留累计并将近期速率设为未知、累计快照标为不完整，重建近期窗口；事件覆盖关闭，接敌指标标记不完整。
- 战局标识变化或游戏时钟回退时结束旧序列并新建观察序列，不把两局累计混在一起；时钟回退先于重复帧判定，即使新旧局恰好复用帧编号也不能忽略回退。直接调用适配器不能区分延迟旧样本与真正回退；产品入口必须先经过下面的观察代次边界。实际历史存储仍由 ARK-8 负责。

## 产品生命周期与归档交接

`battle_lifecycle.py` 的 `BattleFeed` 是单线程采集所有者。`Collector.connect(lifecycle=True)` 定位 BattleController 和 GameFlowController 的单例槽，允许在菜单建立连接；每次采集前后核对进程出生标识、当前单例、原生对象和场景状态，配对同帧累计与编队。读取失败不是空单例。确认的空/销毁单例、关卡到结算的场景转换、初始化阶段、新对象链或时钟回退构成结束/新观察边界；FINISHED 先进入结算冻结，暂停和撤退/再部署不构成换局边界。

每份 `BattleObservation` 保留采集时签发的 `feed`、单调 `sequence` 和 `generation`。消费者不能在出队时给旧样本重新签代次。`BattleSessions` 丢弃外来 feed、旧序号和旧代次；内部 Statistics 会话使用 `feed:generation`，不把可复用地址作为持久战局 ID。进程重启须重新连接和定位元数据。所有地址、帧及时间完全相同且整个生命周期都未被观察到时，无法推断漏掉了几局；本入口不恢复未观察到的整局事件。

连接状态 `connection` 与战局 `state` 独立：读取/连接失败冻结统计、关闭可靠覆盖，保留累计；不会因超时把旧局判失败或清零。成功确认游戏进程缺失、PID 或出生标识替换时，立即结束旧代次、以原战局 ID 交接一次归档并将连接标为断开；无需等下一局。进程查询要求设备侧命令状态标记，ADB 失败或无效输出不能作为退出证据。停止插件仍只冻结观察，不等于游戏退出。

首个原生状态 3 进入 `finalizing`，拒绝该代次迟到的活动样本。若累计/编队配对失败，保留旧累计并关闭不可靠覆盖，终局时间暂不确认；同代次恢复后的首个可靠终局配对可以推进时间、更新最终累计与最后可靠样本，随后仅接受同帧同时间的单调累计修订。事件缺口不会被累计恢复补成完整，快照近期速率保持未知。始终无法恢复时，确认离场后按最后可靠样本结束。确认离场/换局时才交接一次归档，不能以轮询次数或墙钟等待猜测结算完成。该边界及实机回归来源见 [统计核心契约](statistics-core.md#同帧终局累计的回归证据)。新局归属暂不可用也不能丢失旧局的归档信号。

`pop_archives()` 将待交接记录转移给调用方，每个已观察战局在本进程只交接一次。记录包含 `battle_id`、代次、结束原因、元信息、冻结统计、最后可靠样本及可靠快照区间 `snapshot_intervals` 的独立副本；消费端修改副本不能改变实时统计。ARK-8 的本地存储以 `battle_id` 幂等写入；该队列本身不是磁盘存储或跨进程事务承诺。

`BattleView.show_history(record)` 固定 GUI 所选记录；`gui()` 保留该选择，`hud()` 始终取当前局。`show_live()` 才恢复 GUI 跟随。历史页面和 HUD 窗口由后续工单实现。原型 `app.py` 已共用 `BattleRuntime`，采集日志中的 `archive_ready` 保留原始交接证据，正式入口同时写入下面的本地战绩。

### 进程退出与终局短读回归

`tests/test_battle_lifecycle.py` 覆盖活动/终局期间、配对前/后确认进程退出或身份替换：不再开局也只归档一次，保留旧累计、未知结果，拒绝迟到活动样本。终局短读与归属失败恢复测试验证首个可靠端点、最终累计和最后样本，之后仍拒绝不同帧/时间的修订。这两组新增复现在 `27e8efe` 的实现上实际为 8 failed、1 passed，修复后通过。

`tests/test_lifecycle_reader.py` 验证设备侧进程查询的成功、无进程与错误输出边界；`tests/test_lifecycle_events.py` 验证正式入口立即交接和日志重放，以及终局短读恢复后仍保留原事件覆盖端点、已观察事件分子和未知近期速率。确定性回归不代表新候选已完成宿主机游戏进程退出验收。

宿主机 `27e8efe807cf43f2accbfd6d1610d4a7a4ddf725` 的四局证据保存在 Review Loop `evidence/ark6-live-work/host-acceptance/final-27e8efe`：单进程单挂载、758 个有效样本、4 个独立 ID 各归档一次，覆盖 OF-1 胜利/同图重开/暂停/撤退再部署/放弃、OF-8 自然失败和 1-7 演习。该原始流可重放，但不覆盖本节两项故障。新候选的进程退出实机补验由宿主机使用 MAA Copilot 与 CloseDown 串行执行；MuMu 16384 留给该验收。

元信息包含 `stage_id`、`practice`（布尔或未知）和 `outcome`（`victory`、`defeat`、`abandoned` 或 `unknown`）。Android 2.7.91 的终局结果来自经配对核验的 `BattleController.m_result`，已结束的原生值 1 和 2 分别对应胜利和失败。主动放弃需要同时观察到进行中、原生结果仍为 0、同一战局向结算页转场。缺少对应原生证据的结束保持未知。

演习取自 `SquadPage.Params.isPractice`。只有采集器观察到本次开战、参数对象通过类型校验并且关卡 ID 匹配时才采信。复用旧参数、读取不稳或中途接入而没有观察到开战时保留未知。互相冲突的证据列入 `metadata_conflicts`，来源保留在 `evidence_sources`。场景切换期间短读可重试；地址范围失效时限频刷新映射并重新核验身份。

### 复核入口

离线：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests -q --assert=plain`（PowerShell 用 `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'`）。`tests/test_battle_lifecycle.py` 保护换局、代次、重复交接、结尾截断、缺口、视图选择和重放；`tests/test_lifecycle_reader.py` 保护原生状态读取、单例变化拒绝与短读不变零。

取得独占实机窗口后，在固定候选检出运行下面的只读采集。看到 `lifecycle_ready` 后，由 MAA 按实验手册执行零理智的同图重开、切图、暂停、撤退再部署和结束场景。胜负、演习和退出的对照应同时保留 MAA 回调、产品原始记录及重放结果。采集器本身只读。

```powershell
python capture_battles.py --seconds 300 --output runtime/ark6-live-01 --adb 'C:/Program Files/Netease/MuMu Player 12/shell/adb.exe' --serial 127.0.0.1:16384
python capture_battles.py --replay runtime/ark6-live-01/observations.jsonl
Get-FileHash runtime/ark6-live-01/observations.jsonl -Algorithm SHA256
```

输出目录不能复用已有 `observations.jsonl`。重放会核对归档统计和末尾汇总；缺失交接、损坏记录或截断日志会失败。结束采集只冻结仍在观察的局，不把停止插件伪装成玩家中途退出。

### 整合已合入的 ARK-5

只读字段取证已确认当前暂停的 OF-2：`LevelData.levelId` 为 `Activities/ACT3D0/level_act3d0_02`，前后快照均在逻辑帧 3084、102.79990196228027 游戏秒。读取器现将通过对象类型、字符串槽稳定性和生命周期配对校验的原生关卡 ID 传到元信息与归档；保留原生 ID，不猜显示编号。可确认的关卡 ID 改变也会在重复帧过滤前签发新代次；读取失败保留未知，不制造换局。`lifecycle_observation.lifecycle` 保留当次单例、状态、场景与关卡证据。

该切图回归已在 `756a581` 上实际失败、修复后通过：同地址、同帧同时间、原生 session 不变但关卡 ID 改变时仍建立新局，不吞掉首份累计。前后测试输出保留于 Review Loop `evidence/ark6-live-work/stage-id-regression`；新增契约同时验证 stage_id 进入归档，未知演习/胜负不会被字段接入顺带填值。

早期取证产物为 Review Loop `evidence/ark6-live-work/metadata-1794095779935419600/metadata.json`，覆盖暂停时关卡 ID 和原始结果零值。后续胜负、放弃与演习的场景对照和固定作业保存在同工单的宿主机证据目录。

正常窗口与 `app.py --capture-seconds` 均在连接前启用 `CollectionSession.enable_lifecycle()`，共用 `BattleRuntime` 持有的唯一统计适配器。先认证并准备默认事件观察器，再定位单例；生命周期 ID 同时用于快照、事件流、发布和归档。同一观察器跨同进程的新局继续排队，新基线的序号屏障丢弃旧局前缀；消费者先拒绝旧 feed/代次/序号，再输入事件，不为每局重复挂载同一 PID。

暂时的累计/编队跨帧与观察器 retry 保留队列、不签发新代次、不关闭事件覆盖；连接失败或序列缺口才冻结相应覆盖。首个可靠终局先应用事件水位，再冻结时间。后续同帧单调累计修订同步更新发布值，事件不增加，重复稳定修订不增加修订号。`tests/test_lifecycle_events.py` 覆盖真实入口、共用 ID、终局修订、一次交接、旧事件隔离、选择器和默认事件降级。

正式日志的 `lifecycle_observation` 包含原始快照、编队、屏障与 drain，`published.native_rows` 保留逐来源原生数据，`published.rows` 为窗口用的宿主归并投影，`statistics` 为共享结果。`archive_ready` 携带事件能力、错误、最后可靠样本和完整性。`lifecycle_summary` 固定结束状态，可独立复核：

```powershell
python app.py --capture-seconds 600 --baseline-timeout 240 --expect-head <最终完整SHA> --output-dir <本轮新目录> --adb 'C:/Program Files/Netease/MuMu Player 12/shell/adb.exe' --serial 127.0.0.1:16384
python capture_battles.py --replay <本轮新目录>/capture.jsonl
```

默认端口仍为 `127.0.0.1:27043`；`--no-events` 单独验证快照降级。旧 `capture_battles.py --seconds` 仅用于快照诊断，不替代默认事件入口集成验收。原生胜负、演习和退出原因仍缺本候选实机字段证据，保持未知，不能由 MAA 完成、状态 3 或结束场景推导。

2026-11-08 本轮复核没有其他活动兄弟编码运行，宿主机已释放设备。绝对路径 ADB 读到 PID 26586；MAA 6.21.4 在 `F:/Code/python/python.exe` 与已配置的 Frida Python 下都无法建立连接，日志为 `CreateOverlappablePipe failed, err 5`，回调 `ConnectFailed / Connection command failed to exec`。未执行导航、部署或消耗。新证据为 Review Loop `evidence/ark6-live-work/maa-connect-1794095141219848900` 和 `maa-connect-1794095150287986700`。须在最终候选补同图重开、切图、暂停、撤退再部署、结束/退出与元信息取证。旧设备占用不再是阻塞理由。

## 既有 ARK-4 累计快照证据（2026-11-08）

MuMu 12 / Android 2.7.91，1-7 演习实测（MAA Copilot 固定部署，非代理指挥）：捕获从逻辑帧 26 开始，共 203 次累计更新；夜刀累计 5030 → 5260，近期 DPS 在 46–92 之间随 5 秒窗口滚动；`present=True`，四类伤害与治疗字段可用；编队读取得到 12 个统计键，无归属错误或未知来源。暂停帧不产生新速率。

同日 OF-2（0 理智） guarded summon 作业：298 次更新，覆盖 0.67–86.77 游戏秒；梅尔累计 7798.2，来源键为 `char_242_otter` 与 `token_10004_otter_motter`，近 5 秒 DPS 114.0；山 3961.6、歌蕾蒂娅 5260.8；无未知来源、无冲突、无未映射召唤。并发水獭共享同一统计键，只计一次。

同日 OF-8：330 次更新，覆盖 0–98.8 游戏秒；闪灵治疗累计 11710.5，近期 HPS 最高 325.7（187 个非零速率样本）；煌伤害 31899.1，近期 DPS 峰值 2270.6；夜刀 11565 且 `present=False`，累计保留。无归属错误。

另一局 17-2 的在场列表包含一个不在编队映射中的 token；适配器将其列入 `unknown_present_unique_ids`，没有猜测归属。

## 边界

- 召唤归并已用 OF-2 实机验证并发/重新部署同键与宿主归并；其他召唤体系与地图 NPC 的覆盖仍按能力报告边界。
- ARK-5 累计/事件机制证据范围见 [事件采集](event-capture.md)；ARK-6 的新集成与跨局实机仍须单独验收。
- 历史持久化见下节；实际历史页面及 GUI/HUD 选择器仍由后续工单实现。

## 本地战绩（ARK-8）

`battle_history.py` 定义 `schema_version=1` 的 `BattleRecord`。每份 JSON 保留战局 ID、代次、结束原因、元信息与证据、冻结 `statistics`、最后可靠原生快照和事件能力。`duration` 等于最后可靠的游戏时钟端点；`observed_at` 来自最后样本的 UTC 墙钟，没有来源时为 `null`；`recorded_at` 是生成摘要的 UTC 时间。它们不推测未观察到的开局日期或真实终局时间。

`policies` 固定伤害含过量、治疗含过量、实际盾吸收、最多 5 秒近期窗口、独立伤害/治疗接敌并集（1.5 秒尾窗）及可靠归属口径。原始数值不舍入；召唤 `sources`、未映射和冲突诊断完整保留。每行保留累计完整性、近期窗口、事件/护盾覆盖起止。`snapshot_intervals` 记录已接受的快照连续区间，断档后恢复另开区间；终局短读也关闭旧区间。旧交接没有区间历史时保持 `null`。这些区间仅表示采集可靠范围，不能把此前护盾或缺失事件填成零，也不表示每个来源字段都完整；逐行完整性和缺失字段诊断仍须同时读取。

正常窗口和 `app.py --capture-seconds` 共用 `CollectionSession`，把确认离场/换局/进程退出后的冻结结果写入 `runtime/history/`。稳定结算快照和停止插件本身仍不会伪造结束。诊断采样 JSONL 保留，旧日志缺少新字段时仍可重放，新日志严格核对区间。

`BattleHistory(directory)` 提供 UI 数据接口：`list_recent()` 返回按观察日期倒序的最近 100 条，日期未知时使用记录日期，同时间按战局 ID 稳定排序；`get(battle_id)` 返回指定局。两者返回 `HistoryResult(records, diagnostics, written)`，记录调用 `to_dict()`、`statistics` 或 `metadata` 取得独立副本，空 `records` 表示没有可读记录，不能忽略 `diagnostics`。历史页与导出都应消费此契约。

文件名使用战局 ID 的 SHA-256，不把原始 ID 当路径。`save(record)` 校验版本、必需字段、冻结身份、有限非负数值、覆盖范围与口径，写入同目录临时文件，flush/fsync 后原子替换并回读，成功后才淘汰最旧的有效记录。同 ID 同内容重复交接不重写；内容冲突、损坏或未知版本拒绝覆盖。单条损坏保留原文件并给出路径/原因，其余记录继续可读；损坏文件不计入 100 条且不自动删除。淘汰失败返回 `prune_failed`，后续成功或重复交接可重试；读取列表仍最多返回 100 条。

本应用由唯一采集所有者写入，进程内锁保护并行读取。写入失败抛出 `HistoryError`，不淘汰旧战绩；待写冻结结果由跨连接存活的 `BattleRuntime.pending_history` 持有，`CollectionSession` 在后续读取/关闭时重试。同进程重新定位替换采集会话时复用该 runtime，保存及关闭重试均失败也不会丢失待写局。每轮对各待写项只尝试一次，失败项保留并逐局记录 `history_error`，继续保存其他健康战绩；后续成功不能清除本轮仍有失败的错误状态。成功记录 `history_saved` 及淘汰/损坏诊断，实时统计继续工作。失败摘要未成功落盘前仍依赖当前进程和诊断 JSONL，不承诺崩溃恢复或跨进程重建同一局身份。

归档交接必须先将 `pop_archives()` 返回的整批冻结结果转入 `pending_history`，再调用 `archive_ready` 诊断。JSONL 写入/flush 抛出 I/O 异常时仍向调用方报告错误，但不得丢失当前或同批后续战绩；日志恢复后，后续读取或关闭可重试保存且每局只写入一次。该保护限于本进程保留的待写队列，不承诺补写失败的诊断日志。

离线验收：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests -q --assert=plain --basetemp=runtime/ark8-full`，2026-11-09 本候选共 387 项通过（临时目录限定在检出内的 `runtime/`）。`tests/test_battle_history.py` 覆盖第 101 局、乱序日期、重启、重复结束、不同结果/演习/未知、护盾可靠零与缺口、召唤来源、版本/内容损坏、重复 JSON 键、原子替换/fsync/临时文件失败、回读失败、淘汰失败和恢复、正式入口重试以及旧日志兼容。快照区间回归在补充生命周期实现前实际失败、修复后通过；同进程一次/多次重新定位保留待写局、损坏待写局不阻塞健康局且恢复后可补存的回归，在 `d64f16e` 实现上为 3 failed，修复后为 3 passed。归档日志失败后读取/关闭恢复及整批保留三项回归，在 `c1a0116` 实现上为 3 failed，修复后为 3 passed；独立评审原始三项故障复现原样复跑为 3 passed（`python -m pytest runtime/review-ark8 -q --assert=plain --basetemp=runtime/ark8-review-pass`）。该验收是本地磁盘与离线采集集成，不是新的 MuMu 实机验收。

# 同类 DPS 工具架构调研：可借鉴的设计与 ArkDPS 差距

2026-11-08。调研对象为 WoW 的 Details!/Skada、FFXIV 的 ACT/Deucalion、GW2 的 ArcDPS 等主流战斗统计工具，重点不是复刻它们的采集方式，而是提取可迁移到 ArkDPS 的架构与产品设计。ArkDPS 侧对照基于当前 `main` 的代码与文档；外部信息为 2026-11 时点，具体版本可能继续变化。

## 结论先行

同类工具在架构上有四点共性：

1. **采集层可替换**：内存读取、网络抓包、事件钩子等来源都汇入同一个解析/归一化入口，换来源不改统计与 UI。
2. **未知值是一等公民**：不能运算、暂时读不到、平台不提供的值有明确状态和安全展示，不写零、不猜。
3. **展示层与数据源解耦**：面板消费扁平、版本化的 dataset 契约，逐面板实例保存位置、类别、排行和速率。
4. **历史、兼容和扩展是工程问题**：版本化 schema、插件注册表、游戏版本矩阵、变更日志和 CI 与功能同等重要。

ArkDPS 已经做对、而且值得保留的地方：

- **只读、不注入、不上传**：设备侧以 `O_RDONLY` 打开内存，传输只走模拟器回环地址和 ADB 转发，侵入性明显低于注入或抓包方案。
- **按游戏逻辑时间计率**：暂停不推进，倍速不直接乘进率，观察不足时用实际时长或显示未知。
- **未知不写零**：界面用破折号，统计核心用 `None` 和 coverage 标记保留不完整状态。
- **同帧事务校验**：每次采样前后核对逻辑帧，降低读到撕裂数据的概率。
- **累计快照与观察窗口分离**：伤害/治疗累计可补读，接敌和最近 5 秒只覆盖可靠观察区间。

主要差距集中在**产品化工程**，不是统计思想：

| 优先级 | 差距 | 现状 | 建议 |
| --- | --- | --- | --- |
| P0 | 历史与归属持久化 | 只有 `runtime/captures/*.jsonl` 和单次导出，没有历史库；召唤归属只在内存 | 先定 `BattleRecord` schema，再做最近 100 局、原子写、版本迁移和容量淘汰 |
| P0 | 采集抽象与事件归一化 | `Collector.read()` 一条轮询路径，研究事件探针未接入产品 | 定义可替换的 `CollectorSource`，让轮询和事件汇入同一归一化入口 |
| P0 | 公共数据契约 | `stats_core` 的输入契约只在文档里，没有版本号或运行时校验 | 版本化 `CollectorSource` / `MetricReducer` / `PanelDataset` 三个小接口 |
| P1 | UI 实例化与 dataset schema | `panel_model` 已有配置/运行态分离，但 HUD、多面板、几何持久化未实现，`app.py` 仍用旧 Treeview | 扩展 `PanelConfig` 的几何/透明度/锁定/HUD 字段，声明 dataset 必填字段与未知表达 |
| P1 | 版本矩阵与降级 | 硬版本门禁正确，但没有“游戏补丁→工具版本”矩阵、外置签名清单和降级路径 | 建 `compat.json`，偏移/签名外置并校验，版本不匹配时至少能只读已知字段 |
| P1 | 性能预算 | 固定 0.2 秒轮询、每样本 `deepcopy`、`read_ms` 只进记录 | 共享不可变 Snapshot，按战斗/暂停自适应采样，把采集延迟和样本间隔作为运行指标 |
| P2 | 安全模型成文 | 只读优势明确，但没有威胁模型、隐私声明、helper 哈希与清理验收 | 补 `SECURITY.md` / 隐私说明和设备端残留清理测试 |
| P2 | 发布工程 | 固定上游提交、第三方说明和验收脚本齐全，但无 CHANGELOG、SemVer、CI、发布校验 | 补变更日志、版本策略、CI 和发布工件校验；先解决上游 MIT 声明与实际非商业许可的冲突 |

建议实施顺序：**历史与归属持久化 → 事件采集归一化 → 公共契约 → UI/dataset → 版本矩阵 → 发布工程**。先做历史，是因为它同时暴露 schema、迁移、容量和导出问题；先做采集抽象，是因为事件探针和产品采集长期分叉会让后续每个指标都重复适配。

## 方法与证据强度

- 5 个检索角度：主流工具全景、采集链路与进程集成、实时性能与采样、统计语义与未知值、安全合规与社区生态。
- 抓取 19 个来源，提取 95 条可证伪 claim，对抗性核验 25 条，确认 23 条、否决 2 条，最终合并为 9 条发现。
- 证据以项目源码、API 文档、wiki 和变更日志为主；没有第三方性能基准或用户研究。
- ArcDPS 等来源已抓取，但本轮 confirmed claims 主要覆盖 Details!/Skada/ACT/Deucalion；ESO、Lost Ark 未覆盖，相关只能作范式参考。
- 两条被否决的 claim 没有写入正文：Details 历史条目“只是聚合表而非时间线”，以及 `savedata` 的“双层白名单”结构。

## 1. 采集层：Source 抽象，所有来源汇入同一归一化入口

**同类做法**：ACT 把采集、日志、解析拆成独立程序集。`Memory.dll` 负责检测进程和读内存，`Network.dll` 用 Machina 抓包解码，两者都把结果送入同一个 Logfile 程序集，再由 Parse 统一解析战斗动作，因此更换采集源不触碰解析逻辑 [S7]。Deucalion 走另一条路：注入 DLL 挂钩游戏读取已解码数据包的函数，再通过命名管道 `\\.\pipe\deucalion-{PID}` 广播给一个或多个订阅者，解析、统计和 UI 都在订阅端 [S8]。

**ArkDPS 现状**：`collector.py` 只有 `Collector` 类和 `read()` 一条生产路径，`app.py` 的采集线程按 0.2 秒调用；研究事件探针与产品采集互不复用，仓库内没有 source/plugin 注册表。`stats_core` 已经在快照和事件层留出边界，但传输与采集层没有接口，无法在不改统计核心的前提下对比轮询和事件两种来源。

**建议**：定义最小的 `CollectorSource` 协议，包含连接、轮询样本、可选事件流、关闭和能力标志。所有来源输出同一种带帧校验的 `SampleEnvelope`，由一个归一化器转换成 `stats_core` 的 `snapshot` / `event` 输入。这样研究探针可以复用产品归一化逻辑，事件采集上线时也不需要复制一套统计代码。

**准入提醒**：这是架构级抽象，实施前应按仓库治理的准入检查回答：它防止什么具体故障、隔离哪条变化轴、为什么现有 `Collector` 不是正确所有者、折进去会变差什么。只在“事件探针与产品采集必须共用归一化”这个具体压力下引入，不做泛化的插件平台。

## 2. 统计层：未知值类型化，时间口径显式化

**同类做法**：Skada 为 WoW 12.0 的 Secret Values 提供 `IsSecret` 判定、`SafeNumber` 和 `FormatNumberSecret`，数值不可运算时仍能安全格式化，柱长用 1000-rank 兜底，界面不会崩也不会把未知当零 [S5][S17]。Details! 的 `timemachine.lua` 按 actor 的 `last_event` 判断空闲，暂停时记录 delay，恢复时前移 `start_time`，使 DPS 分母排除发呆时间；`Tempo()` 还对 PvE/PvP 设 10/3 秒下限，避免极短窗口数值暴涨 [S6]。

**ArkDPS 现状**：`DESIGN.md` 明确“未知显示破折号，不写零”；`stats_core.py` 对非法时间/数值直接抛错，用 `invalidate_snapshots` / `invalidate_events` 和 coverage 标记保留不完整状态；暂停不消耗游戏时间。差距是：未知目前靠 `None` 和缺行约定传播，没有类型化的原因码写入历史；全局 DPS 分母是本局游戏逻辑时间，包含部署前和未攻击时间；只有接敌窗口是逐事件活跃时间，没有逐 actor 空闲暂停或计时模式选择；近 5 秒窗口使用实际观察时长，但没有把“最短分母”写成明确规则。

**建议**：在结果行中增加可持久化的未知原因（例如 `no_sample`、`coverage_gap`、`platform_secret`、`ambiguous_source`），历史导出保留原因而不是只留 `None`。计时模式是产品口径决策，不要静默照搬 Details! 的空闲暂停；若增加，必须作为可选模式并写入历史口径。近 5 秒窗口的“实际观察时长”已经正确，补一条最短分母/零分母的成文规则即可。

## 3. 实时性能：固定步长窗口 + 事件推送 + 节流拉取 + 共享快照

**同类做法**：Details! 的 `currentdps.lua` 每 0.10 秒采样，缓存 50 个 tick（5/0.1），新样本插入头部、超过 51 个从尾部移除，窗口累计除以固定 5 秒，更新预算可预测 [S1]。ACT 对外用 `NetworkReceived`、`CombatantAdded`、`LogLine`、`ProcessChanged` 等事件推送，实体列表查询每 100ms 才刷新一次，并在所有调用者之间共享同一个列表对象，用节流拉取保护性能 [S7]。

**ArkDPS 现状**：`app.py` 以 `stop.wait(0.2)` 固定轮询；`collector.read()` 每次做帧守卫事务读并返回 `read_ms`；`metrics.py` 只在相邻样本间隔不超过 1 秒时对 5 秒边界线性插值，缺口显示未知。问题是暂停、扫描、空闲时仍按 0.2 秒空转，没有退避；每次更新对 sample 做 `deepcopy`，未来多面板/HUD 各自渲染会重复复制；`read_ms` 只进采样记录，没有端到端延迟或开销预算。

**建议**：引入单一不可变 `Snapshot`（统计数据 + 版本号 + 采集元数据），所有面板共享，不再各自复制；按战斗/暂停/扫描状态调整采样间隔；把采集延迟、样本间隔、丢帧率、更新耗时作为一等运行指标。不要为了“实时”把窗口分母改成固定 5 秒；Details! 的固定 5 秒是它的产品口径，ArkDPS 已经选择实际观察时长，不能混用。

## 4. 展示层：扁平版本化 dataset + 逐实例配置

**同类做法**：Skada 的窗口契约是扁平 dataset：每行必须有 `id`、`label`、`value`，可选 `class`、`role`、`icon`、`order`、`valuetext`，通过 `win.metadata.maxvalue` 缩放；`BarDisplay.lua` 直接遍历 `win.dataset` 渲染，因此任何数据源都能复用同一面板 [S5]。Details! 的 `SaveLocalInstanceConfig` 遍历 `ListInstances`，为每个实例保存 `pos`、`is_open`、`attribute`、`sub_attribute`、`mode`、`segment`、`snap`、`isLocked`、`last_raid_plugin`，重登后逐实例恢复 [S3]。

**ArkDPS 现状**：`panel_model.py` 已实现 `PanelConfig`（持久化配置，`SCHEMA_VERSION=1`）与 `PanelView`/`PanelEntry`（运行态）分离、线性柱长、原始值排序、`share_known` 和近期窗口一致性校验。这是正确的方向，甚至比部分同类工具更严谨。差距是 HUD、多面板、托盘仍是设计文档中的计划，`app.py` 仍是单一 Treeview 旧窗口；`PanelConfig` 还没有几何、透明度、锁定、HUD 开关等逐实例字段；也没有对外声明 dataset 行的版本化 schema（哪些字段必填、未知如何表达）。

**建议**：把 `PanelConfig` 扩展到逐实例几何与显示状态，并做 schema 版本迁移；定义 `PanelDataset` 的必填字段、未知表达和版本号，让 GUI、HUD、导出共用同一契约。先把 `app.py` 接到 `panel_model`，再谈多面板，否则新模型和旧窗口会长期双轨。

## 5. 历史层：Completed Battle 对象、稳定 schema、归属缓存与降级

**同类做法**：Details! 的三层模型是 `Combat → 4 个 Container（伤害/治疗/能量/杂项）→ Actor`；战斗结束后当前 Combat 整体放入 History Segment，`GetCombat` 支持 `-1/0/>0` 索引或 `overall/current` 字符串取回，`AddCombat` 把整场对象插入历史表头部 [S1][S2]。Raid History 采用“难度 → encounter id → 战斗记录数组”的三级结构，可按难度与首领枚举 [S2]。宠物归属 `saved_pet_cache` 只在 party/raid 登出时保存，重登后由 `SetPetData` 恢复，用于归属回填 [S1]。Skada 缓存 `lastSessions` / `knownSessions`，当 `C_DamageMeter` 返回空列表时合成 set 列表，使历史视图在 API 短暂失效时仍保留 [S4]。

**ArkDPS 现状**：`app.py` 每次启动新建 `runtime/captures/*.jsonl`，只在“导出本局”时保存最后一次有效采样；`v1-design` 规划最近 100 局但尚未实现；`stats_core` 的 `owners` 只在内存中；README 明确护盾累计无法补读、中途启动只恢复伤害/治疗累计。没有历史 schema、版本、原子写、损坏恢复、容量淘汰，没有“战斗级摘要 vs 逐样本时间线”的明确取舍，也没有跨进程的召唤物→主人缓存。

**建议**：先定 `BattleRecord` schema，明确保存哪些级别：战斗摘要（关卡、时间、时长、结果、演习、全队累计、口径与完整性）是必需，逐样本时间线是否保存要单独决定。写入用临时文件 + 原子替换，读取兼容旧版本并保留损坏恢复；容量按最近 100 局淘汰。召唤归属按战斗持久化，至少保存 `token → host` 映射和冲突/未知原因；读取失败时用最近已知缓存合成，不要清空历史。导出要能追溯到口径版本和 coverage 标记。

## 6. 扩展性：插件是一等模块，公共契约窄而稳定

**同类做法**：Details! 的每个插件以绝对名注册进 `Details.plugin_database`，拥有独立存档表、`enabled` 状态和 `__enabled/db` 字段，仓库把 7 个插件作为独立 addon 单独打包 [S1]。Skada 明确“内置模块使用平台原生 `C_DamageMeter`，插件可以采集和展示任意数据”，模块对采集、存储、展示三层都有控制权 [S5]。ACT 把对外契约收得很窄：第三方只能使用 `Common.dll` 的公共接口，主类只暴露 `DataSubscription`、`DataRepository`、`PluginStarted` 三个属性，其余一律 internal and subject to change [S7]。

**ArkDPS 现状**：`panel_model.py` 用枚举硬编码 damage/healing/shield 三类和两种排行；`collector.py` 直接 import vendor 模块；仓库内没有 plugin/register_module 机制。`docs/statistics-core.md` 写明了 `register_source` / `snapshot` / `event` / coverage 的输入契约，但只是文档约定，没有版本号、协议类或运行时校验来防止适配器越界。

**建议**：不要先做完整插件平台，先做三个版本化小接口：`CollectorSource`（来源）、`MetricReducer`（指标归约）、`PanelDataset`（展示数据）。每个接口带能力标志和版本号，公共字段写入测试，内部实现可替换。ACT 的“窄公共契约 + 其余 internal”比“所有东西都开放”更值得学：公开面越小，维护成本越低。

## 7. 兼容性：版本矩阵、外置 schema、降级路径

**同类做法**：Deucalion README 维护逐补丁的兼容矩阵（7.2x→1.2.x、7.3x-7.40→1.4.0、7.3x-7.57→1.5.0、7.57→1.5.1），源码以 `RECV_SIG` / `SEND_SIG` 常量锁定签名，跨版本靠重新编译和发版跟踪 [S8]。ACT 维护者明确说“游戏更新后，网络包 opcode 和内存位置会变，更新插件需要时间” [S9]，说明“更新即失效”是采集架构的结构性常态。Skada 则示范另一条路：平台一旦提供原生 `C_DamageMeter`，就放弃自行解析，直接消费官方会话数据 [S4]。

**ArkDPS 现状**：`connection.py` 用 manifest 的 `EXPECTED_GAME_VERSION` 门禁，版本不匹配抛 `UnsupportedVersion`，并区分“未连接/已连接但不支持”；地址提示缓存用 pid + process birth 校验；README 明确“游戏版本变化时拒绝继续使用旧偏移”。安全侧做得对。但偏移仍在 `vendor/ark-source/.../game_structs.py`、`generated_offsets.json`、`game_data/offsets/android_arm64.json` 等 vendor 数据中，没有对外兼容矩阵，没有按版本分发的签名清单，版本不匹配时只有硬拒绝，没有“只读已知字段/降级显示”的路径，也没有发布节奏跟踪游戏小版本。

**建议**：建 `compat.json`：游戏版本、工具版本、支持的采集能力、已知失效字段、最低可用模式。偏移/签名外置并做哈希或签名校验，加载失败时拒绝而不是猜。版本不匹配时至少允许“已知字段只读”模式，并在 UI 明确标注能力降级。把游戏小版本跟踪写进发布流程，而不是等用户报错。

## 8. 安全与隐私：这是 ArkDPS 的优势，但要写成可验证模型

**同类做法**：Deucalion 注入 DLL 挂钩游戏内解码包读取函数，属于进程内代码拦截 [S8]；ACT 同时有内存读取和网络抓包两条链路 [S7]。这类方案能力强，但侵入性、稳定性和合规风险都更高。

**ArkDPS 现状**：README 明确只读游戏进程、不修改数值或代码、不注入 DLL；设备侧小型服务以 `O_RDONLY` 打开游戏内存；传输只监听模拟器回环地址并经 ADB 转发（27381，单实例占用 27380）；记录不上传；关闭窗口会释放转发。这些设计在侵入性上明显低于注入/抓包方案，是明确的产品优势。缺口是：没有成文威胁模型或隐私声明；`/data/local/tmp/ark_dps/` 的辅助服务文件会驻留，Root 前提与二进制完整性没有校验（无签名/哈希）；导出与截图可能带账号、关卡、时间信息；ADB forward 的端口暴露面和异常退出清理没有自动验收。

**建议**：补一份安全模型文档，写清数据流、留存位置、攻击面、清理保证和用户可验证的操作；对设备端 helper 做哈希校验；增加异常退出后的转发/进程清理测试；导出时提示可能包含的敏感信息。只读是优势，但“说清楚并测得到”才算产品能力。

## 9. 发布与社区：版本、变更日志、CI、许可

**同类做法**：Details! 把 7 个插件拆成独立 addon 文件夹，各有 `.toc` 和依赖声明，便于单独发布和按需加载 [S1]；Skada 每个版本有 CHANGELOG/Version 记录 [S4]；Deucalion 用 tag + 兼容矩阵发布 [S8]；ACT 用 wiki 固定“as of 2.6.2.9”的公共接口页作为长期契约 [S7]。

**ArkDPS 现状**：已有较好基础：README 固定上游提交 `5734c30bf54780056ff758505380322c0cf519fe`，`THIRD_PARTY.md` 记录修改与编译方式，`acceptance/` 有可重放校验，当前 `main` 的 93 项测试通过。但仓库没有 CHANGELOG、版本号或 SemVer 策略，没有 CI 配置（测试需手动设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD`），没有发布工件/安装包校验，也没有公开的兼容矩阵和问题模板；README 还指出上游 README 的 MIT 描述与实际许可证不一致，本原型按非商业许可保留限制。这一许可边界会直接影响社区分发、贡献和二次开发。

**建议**：补 CHANGELOG 和 SemVer（至少对公共契约和历史 schema 生效）；把测试命令固化到 CI；发布工件附校验和与支持的游戏版本；提供 issue 模板收集游戏版本/adb/模拟器信息；在开放社区贡献前解决上游许可声明冲突，明确哪些部分可再分发、哪些只用于非商业。

## 不建议照搬的做法

- **注入 DLL / 网络抓包**：能力强，但提高稳定性、合规和账号风险；ArkDPS 的只读定位是差异化优势，不应为了“数据更全”轻易放弃。
- **固定 5 秒分母**：Details! 的 current DPS 用固定 5 秒窗口，但 ArkDPS 已选择实际观察时长和未知语义；直接照搬会破坏“观察不足显示未知”的产品规则。
- **把未知清零或猜值**：Skada 的 `SafeNumber` 返回 0 是平台限制下的降级显示，不是统计语义；ArkDPS 应继续用未知和原因码，不能把 0 写进累计。
- **逐 actor 空闲暂停作为默认口径**：Details! 默认 profile 是 Effective Time，空闲暂停不是所有模式的默认行为；改变分母必须作为产品决策并写入历史口径。
- **自动更新偏移而不校验来源**：版本矩阵和热更新必须配合哈希/签名、回滚和最小权限，否则会把兼容问题变成安全问题。

## 对 ArkDPS 的具体建议

1. **先定历史 schema，再做 100 局 UI**：`BattleRecord` 至少包含战斗摘要、口径版本、coverage、归属映射和未知原因；逐样本时间线是否保存单独决定。
2. **把事件探针和轮询统一到一个归一化入口**：先支持“快照来源”和“事件来源”两种能力标志，不让统计核心依赖具体采集方式。
3. **公开三个小契约**：`CollectorSource`、`MetricReducer`、`PanelDataset`，带版本号和运行时校验；其他内部实现保持可改。
4. **把 `app.py` 接到 `panel_model`**：先完成单面板迁移，再扩展多面板/HUD 和逐实例几何持久化。
5. **建兼容矩阵和降级模式**：`compat.json` + 外置签名清单；版本不匹配时至少能只读已知字段。
6. **补安全与发布工程**：威胁模型/隐私声明、helper 哈希、清理测试、CHANGELOG、CI、发布校验和、许可边界。

## 开放问题

- ArkDPS 是否应把运行时事件探针（如 `ApplyModifier`、实体钩子）纳入正式采集，以换取逐事件时间和更准的接敌/召唤归属？还是坚持只读轮询，把接敌口径明确降级为采样估计？性能、封号风险和版本适配成本需要量化。
- 100 局历史存“战斗级摘要”还是“逐样本时间线”？若未来做跨局曲线和技能区间，存储格式、压缩、schema 迁移、导出兼容和损坏恢复如何设计？
- 偏移/结构签名能否外置为可热更新的版本清单，同时保证完整性与来源可信（签名、回滚、最小权限）？多模拟器和 PC 客户端的兼容矩阵由谁维护、以什么节奏发布？
- 是否开放稳定的插件/数据契约？在上游 MIT 声明与实际非商业许可冲突解决前，社区贡献、二次分发和商业使用的边界如何确定？

## 来源

- [S1] [Tercioo/Details-Damage-Meter](https://github.com/Tercioo/Details-Damage-Meter) — 主仓库；Combat/Container/Actor、插件、宠物归属缓存。
- [S2] [Details! API General](https://github.com/Tercioo/Details-Damage-Meter/blob/master/API%20General.txt) — 历史与 `GetCombat`/`AddCombat`、Raid History。
- [S3] [Details! savedata.lua](https://github.com/Tercioo/Details-Damage-Meter/blob/master/functions/savedata.lua) — 逐实例配置保存/恢复。
- [S4] [zarnivoop/Skada](https://github.com/zarnivoop/skada) — 模块化统计、会话缓存。
- [S5] [Skada API.md](https://github.com/zarnivoop/skada/blob/midnight/API.md) — 扁平 dataset 契约、Secret Values、原生 `C_DamageMeter`。
- [S6] [Details! timemachine.lua](https://github.com/Tercioo/Details-Damage-Meter/blob/master/core/timemachine.lua) — 逐 actor 空闲与时长下限。
- [S7] [FFXIV_ACT_Plugin 公共接口 wiki](https://github.com/ravahn/FFXIV_ACT_Plugin/wiki/Project-Releases,-DLLs,-and-public-interfaces) — Memory/Network/Logfile/Parse 分层、窄公共契约。
- [S8] [ff14wed/deucalion](https://github.com/ff14wed/deucalion) — 注入 DLL、命名管道、版本矩阵。
- [S9] [FFXIV_ACT_Plugin issue #322](https://github.com/ravahn/FFXIV_ACT_Plugin/issues/322) — 游戏更新导致 opcode/内存位置变化的维护说明。
- [S10] [ArcDPS API README](https://www.deltaconnected.com/arcdps/api/README.txt) — 插件 API。
- [S11] [ArcDPS EVTC README](https://www.deltaconnected.com/arcdps/evtc/README.txt) — 战斗日志格式。
- [S12] [ArcDPS](https://www.deltaconnected.com/arcdps/) — 工具主页。
- [S13] [GW2 Elite Insights Parser](https://github.com/baaron4/GW2-Elite-Insights-Parser) — 离线解析与报告。
- [S14] [ravahn/FFXIV_ACT_Plugin](https://github.com/ravahn/FFXIV_ACT_Plugin) — ACT 解析插件主仓库。
- [S15] [SnowCrows ArcDPS 指南](https://snowcrows.com/guides/arcdps/arcdps) — 使用与生态说明（blog）。
- [S16] [Details! parser.lua](https://github.com/Tercioo/Details-Damage-Meter/blob/master/core/parser.lua) — 日志解析入口。
- [S17] [Skada SecretValueHelper.lua](https://github.com/zarnivoop/Skada/blob/midnight/SecretValueHelper.lua) — Secret Values 安全处理。
- [S18] [Square Enix 用户协议](https://support.na.square-enix.com/rule.php?id=5382&la=1&tag=users_en) — 第三方工具合规背景。
- [S19] [goatcorp/Dalamud](https://github.com/goatcorp/Dalamud) — FFXIV 插件框架与生态。

## 边界

- 本报告是同类工具的架构对标，不是 ArkDPS 的产品决策；任何新抽象在实施前仍需按仓库治理的准入检查评审。
- 外部证据以项目自述、源码和变更日志为主，缺少第三方性能基准和用户研究；版本状态为 2026-11，具有时间敏感性。
- ArcDPS 有直接来源，但 ESO、Lost Ark 未进入 confirmed claims；相关结论只能作为同类范式参考，不能当作一手核实。
- ArkDPS 侧的“缺口”有些是实施进度而非架构缺陷（GUI/HUD、历史、事件采集、护盾仍在开发）；本报告区分了“设计方向正确但未完成”和“设计本身缺失”。
- 两条被否决的 claim 未纳入正文；如需要可回看本次调研的原始记录。

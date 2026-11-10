# 单局战斗事件与计算要素挖掘

2026-11-10。本文整理当前 ArkDPS 采集边界、公开项目线索和后续运行时挖掘方案，供后续事件 schema 与采集功能开发使用。本文是调查清单，不表示列出的候选事件已经接入或实机验收。

## 证据级别

- **产品代码**：当前工作树正式采集路径确实安装 hook、序列化或消费该字段；不自动代表所有模式/机制都实机验收。
- **限定实测**：有特定版本、干员、目标、技能或护盾条件下的运行证据；不得泛化为完整机制覆盖。
- **静态线索**：类型、字段、枚举、日志表、模拟器公式或 API 文档存在；不证明游戏运行时触发或产品实际采集。
- **候选/待验证**：本清单建议调查的事件或字段。

## 当前采集能力与明确缺口

正式采集路径当前由**原生累计快照 + 有限 Frida 事件观察器**构成，并非完整逐击战斗日志：

- 原生快照提供干员伤害/治疗累计及伤害分类槽，和编队、召唤宿主等归属信息；累计快照是总量的权威来源。
- 正式事件流当前有三类：`damage`、`healing`、`shield_absorbed`。伤害/治疗来自 `Entity.ApplyModifier`；已验证数值盾吸收来自 `Buff.OnTakeDamage` 与 `BlockDamage._AssignDynamicVar`。护盾只对已验证来源和机制提供可靠事件，不代表一般盾/减伤都覆盖。
- 产品伤害事件现在额外序列化目标 ID、签名 `realDelta`、目标 HP 前后、`killed`、`targetType`、`damageType`、`cancelReason`，以及同帧已验证护盾的 `shield_absorbed` 审计上下文；金额仍读取结算后的 `m_value`（护盾/抵挡已在结算前扣除），不是 HP `realDelta`。仍未覆盖技能、攻击/命中关联 ID 和原始伤害子类；这些新字段的设备端偏移与逐干员对账要等 ARK-21 的事件观测恢复后验证，读取失败时只让过量保持未知。原生累计依旧不能与逐事件再相加。
- 产品近期 DPS/HPS 来自原生累计快照差分；接敌 DPS/HPS 来自已观察的伤害/治疗事件。事件序号、水位、帧、游戏时间和缺口状态用于覆盖完整性。
- 研究 Frida probe 曾将 `AttackInfo.GetFinalAtk`、`BattleFormula.CalculateDamage*` 与 `Entity.ApplyModifier` 对账；祥子样本确认 `atk` 与 `atkScale` 分离，并观察到击杀超出目标剩余 HP 的数值差。但这批公式字段尚不属于正式产品事件 schema，原始探针记录不是仓库内的可重放验收工件。

详细产品实现和已限定实测请参见 [`event-capture.md`](event-capture.md)、[`capability-validation.md`](capability-validation.md)、[`reconnect-research.md`](reconnect-research.md)。正式脚本为 `event_probe_android2791.js`；相关下游位于 `event_capture.py`、`stats_core.py` 和 `snapshot_adapter.py`。类型/偏移见 `vendor/ark-source/tools/enemy_health/game_structs.py` 与 `vendor/ark-source/game_data/offsets/android_arm64.json`。静态字段不等同于运行事件。

## 单局事件矩阵

| 领域 | 目标事件/状态 | 最小建议字段与关联 | 可支持的后续能力 | 当前边界/优先级 |
|---|---|---|---|---|
| 战局身份与时钟 | 战局建立、重开、切图、终局；逻辑帧、游戏时间、暂停/倍速 | `battle_id/generation`、进程身份/版本、关卡、状态、frame/game_time、sequence/watermark | 串局保护、时间排序、DPS 时间基准、终局截断 | 时钟/水位及战局代次已有代码基础；关卡/胜负/演习标记仍需分别验证。**P0** |
| 原生累计基线 | 首次连接及稳定重连快照 | 各统计来源的伤害分类、治疗累计、编队/实体映射、有效字段和同帧屏障 | 总量基线、采集后速率，跨局累计归档 | 中途可读到已有累计，但无法将连接前累计拆成逐击公式事件。**P0** |
| 干员/实体生命周期 | 部署、撤退、死亡、再部署、召唤创建/消失、地图预置单位 | 统计来源键；实体 UID + 实例/生成代次；宿主 key/UID；入场/离场时间与原因；位置/朝向（若可读） | 归属、在场时间、召唤物归并、技能/输出 uptime | 编队和宿主归属以快照为主；没有通用部署/撤退/实体生命周期事件。**P0/P1** |
| 攻击尝试与目标选择 | 攻击开始、弹道/投射物生成、目标切换、命中/未命中 | 来源/目标实体 UID、攻击/技能/投射物实例 ID、攻击类型、帧、子命中序号、取消/闪避/免疫原因 | DPH、命中次数、目标拆分、未命中与免疫分析 | 公开项目提到敌人列表、操作日志及 `PROJECTILEBORN` 静态日志键；运行时 payload 未验证。**P1** |
| 伤害计算输入 | `AttackInfo`、`GetFinalAtk`、目标有效属性/状态 | `originAtk`、`atk`、`atkScale`、`atkAddition`、damageType；攻击方增伤/攻速/穿透，目标 DEF/RES/减伤与相关 Buff；攻击关联 ID | 区分面板攻击力、技能倍率与伤害公式输入，解释来源 | 祥子限定实验直接观察到攻击字段/倍率差异；全属性、所有伤害路径尚未覆盖。**P1** |
| 伤害公式与 HP 输出 | `CalculateDamage`、`CalculateDamageNoEvent`、`CalculateDamageInternal`、`CalculateElementDamage`；最终 Modifier 与目标 HP 变化 | 公式输入/返回、来源/目标、Modifier `value`、`realDelta`、HP 前后、targetType、原始 damageType、cancelReason；统一关联 ID | 折前/折后伤害、减伤分析、实际 HP 扣除、溢出、击杀伤害 | 公式 hook 是研究探针；样本中 `CalculateDamage` 返回与 Modifier `value` 对上，击杀时 `value` 与 `realDelta` 不同。尚未证明所有技能/持续/元素路径均经这些方法。**P1** |
| 防御、抗性与减伤归因 | 攻击方穿透与增伤、目标防御/抗性、物理/法术减免及临时效果 | 命中时的有效属性快照及修改来源；AttackInfo、公式分支与目标类型 | 按用户口径计算净减伤，或进一步拆分来源 | `1 - 公式后伤害 / 公式前有效攻击量` 可形成该样本净衰减；要把它专归为护甲/抗性或特定减伤字段，须排除增伤、穿透等其他公式项。**P1** |
| 治疗与 HP 恢复 | 治疗公式、治疗 Modifier、持续恢复/再生、过量治疗 | 来源/目标、治疗来源/类型、目标 HP 前后、治疗值、实际 `realDelta`、周期/效果 ID | HPS、实际恢复、过量治疗、持续恢复单独归因 | 产品治疗事件不拆实际恢复与过量；山 S2 持续恢复未进入原生治疗累计已有特定观察。**P1** |
| 数值盾及其他防护 | 盾施加/更新/消耗/耗尽/到期/替换；减伤、闪避、次数阻挡 | Buff/盾实例、来源与宿主、目标、盾余额前后、来袭值、吸收量、状态转变原因 | 有效吸收归属、盾 uptime、解释未吸收伤害；避免把盾吸收算减伤 | 闪灵 S2、凯瑟琳装置等有范围限定的吸收证据；其他机制未知。施加、到期/覆盖、次数盾、一般减伤不能由现有盾事件推断。**P0/P1** |
| SP 与技能生命周期 | SP 增加/消耗、技能就绪/施放/切换/结束/打断、冷却 | 技能 key/等级/模式；SP 前后与来源；技能开始/结束帧；实体/统计来源键 | 技能次数与覆盖时长、技能对伤害/治疗/盾的贡献 | 当前无通用 SP/技能流水事件；技能配置不证明战内触发。**P1** |
| Buff、属性与异常状态 | Buff 添加/更新/叠层/移除；关键属性变化、控制/异常状态 | Buff key/instance UID、来源/目标、层数、时长、黑板参数；有效属性前后及帧 | 公式变化解释、状态时长与技能归因 | 有 Buff/黑板静态字段和特定盾上下文读取；不是通用 Buff 变更追踪。应优先按变化/命中取样，不要盲目高频扫全状态。**P1/P2** |
| 敌人、波次与地图效果 | 敌人生成/入场、变阶段、受击、死亡、漏怪；Wave/SpawnAction、机关/环境伤害 | 敌人实例 UID、静态 ID、波次/出生键、HP 前后、位置/路线状态、来源；阶段/环境效果 ID | 击杀归属、敌人 TTK、漏怪、阶段机制、持续/环境伤害 | vendor 有 Enemy/Scheduler/Wave/SpawnAction 字段；公开计时器提到敌人列表候选；没有运行时事件完整性证据。**P1/P2** |
| 采集健康与对账 | Hook 就绪/关闭、事件缺口/丢失、读取失败、重连、覆盖终点 | 观察器状态、错误原因、进程/战局代次、序号/水位、帧区间、原生累计差 | 明确覆盖完整度，防重复累计、串局和“缺口补零” | 当前有状态/水位/缺口契约；中途累计恢复不等同于补回逐事件历史。**P0** |

### 通用事件信封

建议每个事件都保留：`battle_id/generation`、版本/进程身份、单调 `sequence/event_id`、`frame/game_time`、事件类别、采集来源、来源统计键、来源实体 UID + 实例代次、目标实体 UID + 实例代次、数值/单位、原始值和派生值、coverage/error 状态。主机 `observed_at/captured_monotonic` 只作诊断，不能替代游戏逻辑时钟。

战局关联键、统计来源键、实体实例键和事件 `correlation_id` 是不同概念。若游戏没有原生攻击 ID，采集端推导的关联键必须标记为推导值；不能只用可复用的指针或“同来源干员”配对。对公式—Modifier 链需保留调用序号/线程/frame/source/target/AttackInfo 等证据，并通过真实样本验证相关性。

## 公开项目线索（静态/候选，不视为游戏事件协议）

- [`Tim23333/Arknights_timer`](https://github.com/Tim23333/Arknights_timer)：README 提到 `BattleController → UnitManager.enemies`、`Scheduler.m_managedWaveEnemies`、敌人 `m_hp`/属性、帧/游戏时间、`BattleLogger` 玩家操作及 RNG 目录；也有自建模拟器。此处主要是 README 级线索和模拟组织方式，不能据此确认游戏提供完整实时伤害流。项目 README 列 MIT；其运行时内存定位仍需当前版本验证。
- [`ArknightsAssets/ArknightsGamedata` 的 `extra_battlelog_table.json`](https://github.com/ArknightsAssets/ArknightsGamedata/blob/master/cn/gamedata/battle/extra_battlelog_table.json)：静态类别 `SELECTOR`、`DEATHDETAIL`、`PROJECTILEBORN`、`OUTPUT_DAMAGE_TOTAL` 及 `sourceId/sourceMode/enemyId/enemyApplyWay/projectileName/abilityName/logAlias` 等候选关联字段。配置存在不证明普通关卡中记录器已启用、字段非空或包含逐击值；仓库页面未显示许可证。
- [`OpenArknightsFBS`](https://github.com/MooncellWiki/OpenArknightsFBS)：把 `extra_battlelog_table` 列为数据文件的结构线索；没有由此证实运行时日志触发和 payload。
- [`Tim23333/Arknights_timer` 战斗 API 逆向文档](https://github.com/Tim23333/Arknights_timer/blob/main/ark_api_docs/04_battle.md) 与 [`ArkPRTS`](https://github.com/ashleney/ArkPRTS/blob/master/arkprts/client.py)：战后接口有 `battleId/stageId/completeTime/stats` 等线索；ArkPRTS 实现 Base64/ZIP/JSON 重放解码，但已检查源码没有证明 JSON 内含逐击事件。重放数据可作为后续调查入口，不是当前可依赖的历史回填来源。两项目许可证和版本须按准确仓库/版本复核，不能从 API 文档推出官方支持。
- [`wxhwwla/calc-framework`](https://github.com/wxhwwla/calc-framework)：提供物理、法术、真伤计算函数和 DAG 属性链，能对照攻击、倍率、穿透、伤害加成、DEF/RES 等公式要素；它是计算器，不是游戏事件采集器。调用侧存在百分数与比例值单位转换，复用前需审单位。仓库标 AGPL-3.0，数据许可另列，直接嵌入前须做许可评估。
- [`BiologyHazard/arknights-game-model` 字段模型](https://github.com/BiologyHazard/arknights-game-model/blob/main/arknights_game_model/_raw_game_data/excel/character_table.py)：静态属性结构含 HP/ATK/DEF/抗性/攻速/基础间隔等；不能代表战中有效属性变化。[`Sunsetra/AKSimulator`](https://github.com/Sunsetra/AKSimulator) 已归档且采用 GPL-3.0，可参考模拟器设计，不是现役事件接口。

公开线索中若项目页面不显示许可证，应视为许可未确认；“能浏览/clone”不等于可复制进 ArkDPS。数据许可与程序代码许可须分开核对。

## 指导挖掘方向与验证顺序

### 本轮公式 probe 的运行时签名与完整性注意项

run7 的 `ready` 元数据实际解析到以下候选重载（只代表该次 ARM64 客户端进程中的签名）：

- `BattleFormula.CalculateDamage(target, source, triggerTargetEvent, triggerSourceEvent, AttackInfo&)`、`CalculateDamage(atk, target, damageType, atkScale, atkAddition)`；另有 `CalculateDamageNoEvent(target, source, AttackInfo)` 与 `CalculateDamageInternal(AttackInfo, penetrateRatio, penetrateFixed, target)`。
- `CalculateElementDamage(source, target, atkScale)` 以及以 `atk/target/(atkScale)` 为参数的重载。
- `AttackInfo.GetFinalAtk()`；`Entity.OnCalculateDamage`、`OnAfterCalculateDamage`、`OnBeingCalculateDamage`、`ApplyModifier(Modifier&, force)`、`_OnOutputDamage(Modifier&)`、`OnAfterOutputDamage(Modifier&)`。

临时 probe 按 Modifier 元数据偏移减 `0x10` 读取字段；运行时签名显示其参数是 `Modifier&`，这与按引用传递值类型的读取方式相符，但并不单独证明类型布局。脚本没有像 `AttackInfo` 那样把 `Modifier` 的 `class_is_valuetype` 结果写入 `ready`；正式扩展前应显式验证值类型、偏移和未装箱表示，并检查运行时解码结果。run7 已有的来源/目标/金额观察彼此吻合，但这不应替代可复用的布局自检。

另一个采集完整性边界是临时脚本在安装 hook 前先发送 `ready`；hook 安装失败另发 `hook_error`，不一定变成 Frida 脚本级 error。因此 `ready` 不能单独作为“所有 hook 已就绪”的屏障。该次 run7 最终得到 14 个 hooks、无 `hook_error`、836 条 hook 记录、`lostEvents=0`；后续脚本应把“必需 hook 安装完成、可选 hook 失败列表、hook 数、丢失数”明确写入 ready/summary，并只在安装屏障后开始实验计时。

`DamageType` 静态枚举值为 `0=NONE, 1=PHYSICAL, 2=MAGICAL, 3=PURE, 4=HEAL, 5=ELEMENT`。这可作为解释 raw 值的类型线索；仍应核对当前客户端运行时字段的语义及产品事件的映射。正式产品当前将逐事件伤害子类合并为 `damage`，不能由累计快照的分类字段推断每个 hook 事件都已保留分类。

1. **先建立运行时方法/字段目录，不做全量盲 hook。** 在已认证版本和 ARM64 默认架构下，通过 IL2CPP metadata 枚举相关类、字段、方法签名/参数/返回值，优先检查 `BattleFormula`、`Entity`、`Modifier`、`Buff`、`BlockDamage`、Ability/Skill/SP、属性、Enemy、Scheduler/Wave/SpawnAction、投射物和 BattleLogger 候选。名字/静态类型仅用于排序；要继续核对具体调用与读写点。
2. **画清生产与研究的边界。** 现有产品脚本的核心观察点为 `Entity.ApplyModifier`、`Buff.OnTakeDamage`、`BlockDamage._AssignDynamicVar`；研究候选包括 `CalculateDamage*`、`CalculateElementDamage`、`AttackInfo.GetFinalAtk`、`OnCalculateDamage` / `OnBeingCalculateDamage` / `OnAfterOutputDamage` 等。先确认回调之间是同一结算链还是重复观察，避免重复累计；hook 方法清单不等于可发布 schema。
3. **按事件族做小而可证伪的受控样本。** 顺序建议：物理/法术/真伤/元素分支；有/无 DEF/RES、穿透与伤害增益；击杀前后 HP 差和溢出；数值盾完整/部分/耗尽；治疗与满血/持续恢复；SP 多种获取与技能开关；Buff/属性变更；召唤物及宿主；敌人出生/死亡/漏怪/波次。逐条记录“方法实际触发、payload、公式映射、累计对账、版本/环境”，无事件也作为结果保存。
4. **在产品接入前先验证完整关联与开销。** 优先使用公式方法输入/输出和最终 Modifier 的调用上下文或可靠关联 ID；仅以同帧/线程/指针近邻配对要标明推导并实测。测定丢失、重入、高频开销、暂停/倍速、换局、observer 生命周期与 hook 错误；遵守现有版本校验，不使用会改游戏数值的测试手法。
5. **建立原始事件档案与派生口径分层。** 保存原始 payload、方法版本/签名和 coverage，再由稳定统计层派生 DPH、净减伤、HP 实扣、溢出、治疗过量、护盾吸收等。快照用于累计对账，逐击事件用于时间与因果分析；两者不能重复加总。接入前未观测事件保持未知，不能外推成全局指标。

## 当前优先级结论

- **P0：可信基础**：战局代次、逻辑时钟、原生总量基线、伤害/治疗/已验证盾事件、来源归属、序号/水位、缺口和终局覆盖状态。
- **P1：扩展因果链**：目标与实体实例 UID、公式前后值、`realDelta`/HP 前后、命中/技能关联、DEF/RES/穿透快照、SP/技能区间、通用 Buff/属性变化、敌人出生/死亡与漏怪。
- **P2：完整重演**：完整敌我动态属性、所有技能/状态、投射物、随机数与特殊模式日志；这可能支持更丰富仿真，但不是现有累计/DPS/HPS 正确性的前提。

**结论：**目前没有证据表明公开项目提供可直接接入的全量逐击事件协议。最可行的方向是以已有产品采集为稳定底座，扩展经过版本门控的 Frida 运行时观察，并用公开静态表/公式与受控实战逐项交叉验证；连接中途建立的覆盖起点要明确，历史逐击记录不能假定可回填。

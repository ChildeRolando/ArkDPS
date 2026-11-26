# 事件探针与共享统计接口

MuMu 与官服 PC 的探针负责采集和证明事件；它们将相同口径的事件交给 `LiveEventCapture`、`BattleStatisticsAdapter` 与 `Statistics`。平台决定如何连接、读取原生状态和校验身份，不决定技能拆分、实际吸盾、有效伤害或接敌速率是否可用。高级指标由实际能力、事件质量和覆盖证据决定。

## 边界与口径

`event_probe.py` 中的探针契约集中处理批次身份、序号与重放验证、质量声明、确认范围和终局身份。`event_observer.py` 保持 Frida 传输与资源清理所有权；`event_capture.py` 与生命周期入口消费同一个契约。探针不计算面板统计，WPF 不重新计算事件指标。

实时Frida与ArkPie共用宿主前缀游标及ACK判定；本地已消费与生产者已确认分别保存。丢失ACK回复时保留已消费水位并累计重试，不重新读取/归约该前缀。游标绑定observer的实际stream寿命：重新加载脚本建立新寿命，旧回复不得确认或污染新游标；普通战局reset不借mark重建ACK身份。cooperative host继续保留自己的锁、单字段回复及重放边界，只复用相同ACK值校验。JSON有限数及唯一字段校验属于公共探针契约，不依赖测试host模块。

| 接口 | 职责 |
| --- | --- |
| `EventProbe.start / mark / read_through / commit_events / stop` | 建立观察、同帧标记、有界读取、宿主确认和清理；保留既有资源所有权。 |
| `ProbeContract.validate_binding / validate_batch` | 验证已确认身份、有界前缀及一致重放。 |
| `ProbeContract.normalize_quality` | 解释声明版本和质量标记，不改事件量或原始证明。 |
| `ProbeContract.ack_scope / validate_ack_mark / terminal_identity_matches` | 限定确认和终局证据所属的战局。 |
| `NativePcBinding` | 将 PC 原始进程、世代和精确时钟证据绑定到采集器已确认的战局。 |
| `assemble_probe_source` | 固定组合平台适配器和共同运行时，记录各组件、组合源码与宿主实际提交字节的摘要。 |

实时宿主明确选择 `MUMU_CONTRACT` 或 `NATIVE_PC_CONTRACT`。契约描述随生命周期证据保留以支持回放；生产者自行发送的字段不能把宿主已选契约降级。旧来源标签只在 `contract_for` 的兼容边界解释，不进入统计公式。新增宿主 profile 可通过 `register_contract` 注册，不能覆盖内置名称或降低 PC 必需的身份、前缀、重放与确认校验。

两端共享质量语义及统计口径，保留各自原有游标策略。MuMu 允许跨局保留队列的旧前缀，在新基线处丢弃已过期记录；它沿用既有重放和确认行为。PC 继续严格校验有界前缀与原始重放，并要求有界读取及同身份标记确认。不能把 PC 的帧范围校验直接套到 MuMu 跨局旧队列，也不能为统一接口去掉 PC 校验。

卫戍 playing 状态的事件专用采样共用 `verified_autochess_event_window`，平台只解析独立快照及观察者原始身份、时钟证据。
PC 的当次 `native_window_guard` 与 MuMu 原 `guard` 不互相冒充；验证窗口后仅投影 `events_only` 样本，独立快照锚点保留且累计 rows/global 清空。
普通关卡和终局仍走原屏障。采集后端选择不进入伤害、父子结算或技能公式；Frida 与 ArkPie 继续运行同一份共同 JS。

公开 profile 的能力字段必须为布尔值；`raw_replay` 与 `strict_batches` 必须同时启用或同时关闭，以保持原始记录存储与重放比较一致。注册和解析都拒绝不一致组合，避免静默跳过冲突重放，或把同一重放的不同存储格式误判为冲突。

- `damage` 和 `healing` 的 `amount` 沿用 MuMu 的原生名义结算量。治疗的实际 HP 增量保留在 `proof.direct_healing`，不能替换治疗事件量。
- `shield_absorbed.amount` 只记录同次结算确认的实际吸收，不能用施盾量、剩余盾量或护盾消失推算。
- `skill_start` / `skill_end` 保留来源、技能身份、游戏时钟和原始方法证据，交由原有技能窗口逻辑处理。
- HP 前后值、实际扣血、吸盾、公式前后值及结算完整性作为附加证据；部分证明不自动变成全局完整覆盖。`damage_settlement` 和原生累计仍遵循既有结算、归属及核对规则。
- 原生累计快照保持权威，事件不能重复加到累计伤害或累计治疗。重放同一前缀不能重复计数；内容冲突、序号缺失、战局或构建不一致必须保留失败。

`BasicSkill.OnCastFinish` 的 `skill_end` 记录表示原始施放回调，并不宣称效果结束。该补充记录必须带字面的 `skill_wait_for_end: bool`、原生 Int32 `skill_reason`、技能键及已确认角色 UID。布尔值来自经过方法签名、字段类型、receiver/owner/当前技能、身份和 Q32 时钟复核的原生 `m_waitForSkillEnd`；不能由弹药剩余数或安装能力推算。公共技能转换函数只更新该 actor 的施放状态，实际效果结束继续由既有 `Character.OnSkillFinish` 等可靠事件提供。没有新字段的旧记录保留旧保护；false 不是结束证明，true 也不能补出遗漏的开始或结束。此补充状态不承担累计伤害来源注册。

卫戍的部分原生回调里，`Entity.id` 与当前 `Character.data.prefabKey` 可能是不同的角色形态键。普通 CID+data UID 精确匹配保持优先。针对 raw `char_002_amiya` 的权威结算和元素条，生产端可提供可选 `proof.native_autochess_character_form`：`version=1`、`entity_cid`、`data_cid`、`data_uid`、`instance_uid`、`frame`、`game_time`、`scope_key`、`phase`、`round`、`collection_mode`。

该证明必须来自同一原生 Character 和其经过元数据验证的 BattleCharacterData，开合复核实体 CID、prefabKey、data UID、实例、对象/数据指针及精确时钟、scope。普通来源不增加形态读取；失败省略证明而不猜角色。消费者只在 `damage_settlement` / `elemental_bar` 中接受严格的完整字段，要求原事件来源和 UID、事件 stamp 的帧/时钟/scope/mode 全部一致，再由当前同 scope 名单精确核实 data CID 与 UID 的普通干员。不能缓存别名、仅凭 UID 覆盖 CID 冲突、从旧样本补写证据，或把同名另一个实例当作该来源。原始 source、proof 和重放收据保留；只在已验证的归约入口使用限定后的来源键。

普通扣血证书可由唯一目标受击回调的 HP 前值，与同一 `ApplyModifier` 结束时的真实有符号扣血和最终 HP 联合证明；不能假定目标回调返回时已经扣血。来源、目标、UID、借用的 Modifier、类型和游戏时钟必须持续匹配，HP 差必须与实际扣血一致。嵌套元素击杀另需子进入前已发生的父原生 `realDelta` 与 HP 检查点、同目标的独立子击杀证书，以及父返回时的数值/身份复核；父、子各计自己的实际分量。缺少这些独立证据的嵌套、引用复用、歧义和身份变化不能认证。拒绝证明时必须留下未验证记录或明确质量失败，不能静默漏掉后再认证空的完整零值。

普通子命中在父受击回调内先结算时，以父的零扣血检查点核对子命中的起点，再以子结算的 HP 终点、父的原生有符号扣血和最终 HP 验证父命中。两笔伤害按各自的属性入账。若子命中已击杀目标，父的实际扣血与最终 HP 都为零且名义量仍为正，则父命中的名义量全部计为过量。该证明要求同一目标、时钟和卫戍交战范围，以及可核实的零护盾消耗。

## 单证书输出与独立原生观察

2026-11-26重构后，共同生产者对有结算证书的敌方命中只发一笔`damage_settlement`，其`proof.settlement_wire_version`为字面整数`2`。不再为同一事实追加`damage`、`hp_damage`、`elemental_burst`。原生受击、输出、HP和护盾hook仍是独立证据输入，不随输出记录删减。

自伤、友方、token等证书域外的原生观察、明确未提供敌方证书能力的生产者，以及确实无法形成证书但已有独立名义量/公式观察的情形，保留其可证事实和质量失败。这些记录不是第二份敌方金额账本，不能与同笔证书双计。当前接收方均归约到`DamageSettlement`；缺HP证据不得通过兼容入口补成完整命中。

新证书的附加事实均绑定同一外层来源、类型、实例、原始时钟和scope：

| 字段 | 来源与含义 | 严格边界 |
| --- | --- | --- |
| `hp_before/hp_after/real_delta/cancel_reason/target_type` | 仅来自已闭合的同次HP证据；实际HP差与有符号增量一致 | 不用名义量反推；缺证省略，不填零；`returned`本身不是扣血证明 |
| `proof.native_modifier_observation` | `{version:1, method:'Entity.ApplyModifier', amount:...}`；外层结束时已经实读的Modifier最终值 | 与`nominal_amount`是不同事实；全吸盾时最终值可为0、已证名义量为40；输出回调入口量不能代替该最终值 |
| `proof.target_callback_checkpoint` | 原受击入口的`version:1/method/returned/source/source_character_uid/target_uid/hp_before/frame/game_time` | 继承外层target实例绑定；子先结算的零父量仍需这个原始正HP起点，不能用结束时0HP补造 |
| `proof.nested_elemental_chain.version:2` | 每段只引用真实`settlement_sequence`及该段来源、HP端点和实际增量 | 不制造替代burst记录或序号；v1旧录制仍按原双记录配对严格读取 |

数值、类型、UID、时钟、来源、scope或版本不一致仍失败。原生累计精确浮点对账读取有名的`native_modifier_observation.amount`，不拿证书名义量或实际HP替代；该算术对账不宣称已经反汇编证明客户端计数器更新指令。`native_modifier_observation`缺席时保留验收缺证。

旧录制没有v2标记，继续按原记录解释；验收器按每个证书的字面格式区分。一个现代证书可以与独立域外观察同处一段有序前缀，不能因为看到旧形状的域外记录，就忽略其余现代证书；同producer的证书格式缺失、混合或未知版本不得静默降级。原始输入保持不变，既有未知量不因重构升级。

入口和目标回调返回时的量值只是阶段观测，不能要求它们与最终结算量相等。游戏监听器可在目标回调之前、期间或之后修改来伤；证书核对实际读取的最终 `m_value`、最终 `realDelta`、HP 差和同次已证吸收量，最终名义量必须覆盖实际扣血与吸收。允许证据一致的增幅、衰减、非致死封顶和致死过量，不按某个倍率推算或补字段。

数值盾回调提供独立的实际吸收证据。已确认同次全吸盾导致 `m_value` 从来伤量变为零时，应保留原有盾证书，不能被新增扣血证书的量值检查误伤；该例外不能掩盖身份或 HP 矛盾。最终名义量为零却仍有实际扣血，属于不一致证据；正量归零、HP 未变但没有数值盾等消耗证明的情况仍保留未知，不能仅凭阶段值不同推断吸收量。

初始接口拆分保持了旧 PC 统计文件，但该条件不足以证明复用了当前 MuMu 的行为。2206 复用审计后先合入 MuMu `01b7de4` 的迟接入处理原补丁，再通过共同事件接口增加上述原生状态证据及质量/actor 投影；没有另建 PC 统计公式。PC 原生身份、在场与结束证明仍保留在其原有边界。累计、来源归属、技能窗口与覆盖解释继续由[统计核心](statistics-core.md)、[累计适配器](snapshot-adapter.md)和[面板模型](panel-model.md)拥有。后续共同源码与业务验收见[复用审计](event-probe-reuse.md)。

## 质量声明与覆盖

新探针声明 `quality_contract_version: 1`。两端都必须明确输出布尔质量字段：`damage_unknown`、`engagement_unknown`、`shield_unknown`、`skill_unknown`；公式族另需 `mitigation_ready` 和 `mitigation_unknown`。只有严格的 `False` 才证明该 unknown 标记未置位，公式族还要求严格的 `True` readiness。缺失、非布尔值或后续质量丢失不能认证“零事件”；未知或类型错误的声明版本明确拒绝批次。旧 MuMu 无版本回放由兼容契约解释历史语义，PC 不能借缺失版本降为宽松模式。

这些布尔字段只声明探针能力与已知全局失败，不能代替实际事件字段。即使 `mitigation_ready: true` 且 `mitigation_unknown: false`，某次命中没有同次匹配的 `mitigation_before` / `mitigation_after`、前值非法或后值与名义结算不一致，该次公式证明仍失败，完整减伤率仍未知。生产端必须检查真实字段解析、回调命中和同次关联，不能靠补布尔声明把缺失前后值变成成功。

例如受控生产回调给出同一次命中的前值 80、后值 40，且入口名义伤害为 40，共享统计可得到该已匹配命中的 50% 减伤。候选公式后值为 39、但它试图匹配的入口量值为 40 时，生产者必须拒绝配对；若候选在入口已经匹配，后续同一命中的最终量值被修改，则保留原公式前值并记录实际最终后值。全局安装能力都可以是 ready，逐次证明的结果仍不同。某一命中配对成功，也不能证明其他命中、HP/护盾结算或整局覆盖完整。

质量失败只影响相应指标族；缺少技能证明不能抹掉可靠累计或实际吸盾。覆盖中断保留已观察区间及原始证据；未覆盖区间仍为 `None`，不得补零。恢复传输不能补采已经丢失的事件，也不能把本局既有不完整覆盖改成完整。

版本 7 的 `settled_damage` 另有逐次结算证书。已确认的 HP/扣盾分量可在 `complete: false` 时保留为已观察值，完整性、未验证占比和理由必须一起消费；不能把该部分和称为全局完整值。它与传统事件族独立，任一族未知不应抹掉另一族已有的独立证明。

可选 `damage_settlement_source_quality_version: 1` 以 `damage_settlement_unlocalized_unknown` 和最多 128 项的 `damage_settlement_source_issues` 限定失败来源。每项带原始来源 CID、角色 data UID、产生时的帧/时钟与原始 scope。生产者保持原有全局 unknown 和首个失败证据；不能定位、原生开合见证失败或溢出时置 unlocalized。消费者验证完整字段、类型、当前 scope/模式和时间边界，并核对本小局内当前及历史归属，才可按干员及账号缩小影响范围。旧来源、缺少字段、相互矛盾或无归属的声明继续采用全局保护，不能用此扩展认证未被记录的伤害。

友方退场的目标域排除只适用于已经确认不属于 Enemy 的 `char_` Character：同一目标指针、类、CID、Modifier、伤害类型和原始时钟，UID 从正数归零，期间没有未知或潜在敌方域、换目标、Modifier 重用或任何同线程嵌套 ApplyModifier。原始调用的按线程深度包含不参与玩家统计的敌方来源，正常退出和早退都清理。该排除不产生 HP、扣盾或金额证明；敌人、陷阱、token、不同正 UID、归零后恢复及不能完整复核的路径仍保持保护。

有界读取必须保留已交付但尚未确认的事件。宿主只确认已接收前缀；确认回复丢失可以重试，不能造成漏计或重复计数。确认和终局证据必须属于原战局及原始流身份，不能将旧战局尾部接到新战局。

共享生产者在经过签名验证的 `BattleController.ClearRunTimeData` 回调中捕获卫戍真实零帧，仅接受交战阶段、同一 controller/根、清零后的 frame 0 和精确 Q32 时钟 0、无嵌套或夹入事件的返回边界。`autochess_round_start` 保存该时刻的身份、原始阶段/轮次/账号表、质量、序号和独立活角色空集合证据；不能从后来的快照回填。准备阶段的零时钟或单纯切换阶段不构成此证明。可靠新小局重新建立动态质量状态，安装能力及旧事件的原始 scope 保持不变。

小局正常切换而物理战场尚未销毁时，`BattleObservation.autochess_successor` 可保留继任采样的两份原始标记。仅在同一持续观察者已消费旧局前缀、旧局有零帧覆盖、相同战场/构建/账号/采集模式、已验证的结束阶段或下一轮、无在途或延迟回调，且旧水位与新两标记的事件序号和回调 epoch 均未推进、显式质量声明合法且一致时，按旧水位完成归档。新标记须用各平台已有的独立快照与原生绑定验证；PC 和 MuMu 都发出现场已复核的 `native_battle_state`。旧局不借用继任的时间、数值或空编队，无法证明的速率仍未知；真实逐笔缺证不因关账成功而消失。存在未消费尾部、观察者替换/退出、标记或质量缺失时保留 gap，不将正常阶段变化本身当作完整证明。真正时钟回拨的现场候选也要求同一持续观察者，旧离线记录保持原校验语义。

首次 `read(N, N)` 的普通 `events` 仍为空。额外的 `autochess_round_start_events` 只携带零帧序号之后、截至 N 的完整原始前缀，`autochess_round_start_events_complete` 必须为字面布尔值。单次最多 2048 笔；超过时宿主通过现有有界读取分块组装，最多 100000 笔。全部身份、scope、序号、时钟和质量验证通过后才建立零帧并消费，任何中段失败都不能部分接纳或 ACK。已确认释放，或生产者已查明原始 scope/时钟不合格的前缀不得重建；生产者省略可执行的零帧证明并给出不可用原因，保留普通保守基线及后续采集。两端共用该机制；PC 原生累计读取和旧记录的保守基线不因此放宽。

## 回归与证据边界

现代事件先按玩家和角色 UID 验证归属，再进入技能状态及结算处理。已确认属于他人的同名干员回调不能使本家同 CID 的技能分类变成未知；未确认 UID 仍保留归属缺口。元素条的外国来源还需保留否定见证，以阻止后续无来源爆发借用更早的本家元素条。`event_capture.apply_event_quality` 是本家和多家观察共用的质量入口，各家只将已确认来源映射到本作用域，不补齐生产者缺失的字段。

`EventStream.accept` 的内部位置参数允许消费者提供已验证的候选来源键，只影响共享未知状态的传播。未匹配的 `BasicSkill.OnCastFinish` 且 `skill_wait_for_end: false` 继续按原规则忽略；缺 UID 的普通技能事件、待结束或非法证据则保留未知。多家层不复制这些技能判断。该接口保留原始重放记录，不能由生产者通过同名关键字传入候选来源键。

`tests/test_player_event_isolation.py` 固定了官服 PC 卫戍灰喉原始字段回归：另一家的技能结束曾污染本家技能分类，造成 46/72（63.9%）未核实命中。修复后本家 72 笔的技能分类缺口为零，实际扣血、过量与已配对减伤不变。`tests/test_multiplayer_battle.py` 使用冻结的小局完整事件前缀核验三家分账，并独立测试四家合成输入、UID/宿主冲突、重编号、重复批次、终局与缺失质量。真实前缀仅证明已观察的三家输出，第四家保持未知；离线回放不替代新候选的四家实机验收。

`tests/test_event_probe_parity.py` 使用同一组规范事件分别经过 MuMu 和 PC 契约，再通过真实 `LiveEventCapture` → `Statistics` 比较伤害、治疗、实际吸盾、技能拆分、有效伤害和覆盖状态；包含中断、重放、确认重试、质量缺失及跨窗口事件。生产 JavaScript 的质量输出在受控 Node 环境执行，测试不得替生产者补上缺失质量字段来制造成功。

该夹具实际执行生产安装函数的成功、能力不可用和安装失败回滚路径；另由生产公式及 `ApplyModifier` 回调生成成功配对、缺少候选和后值不匹配的事件，保留原始来源、UID、时钟、事件量及证明进入共享采集链。全局 ready 且 unknown 未置位时，该次缺失前后值仍不能产生完整或已观察减伤率。两个干员的交错技能窗口分别核对，结束一个窗口不能结束另一个干员的窗口。这些地址和战斗状态均为受控合成输入，不是官服实机字段采集验收。

`tests/event_probe_pipeline_fixture.js` 另外将真实生产公式、技能、受击及数值盾回调组合，模拟目标回调返回后才由外层提交扣血的时序。原始批次经过共享采集、统计及 `render_panel.details_by_owner`，验证版本 7 普通干员的有效伤害、公式配对和技能拆分；覆盖不同幅度的后期增幅与衰减、调整在目标回调之前/期间/之后、早期输出回调保留旧量、非致死封顶、致死和两种全吸盾顺序。反例检查最终名义量小于实际扣血、最终量归零但仍扣血、HP 差矛盾、身份、时钟、重复回调及嵌套，不能把“任意阶段量值变化”当成失败原因。夹具透传真实错误，不将批次错误或质量标记改成成功。

技能进行中的批次在尚无 `skill_end` 时就核对可证明的技能/非技能分量及有限占比；下一批次真正结束技能后，再核对结果连续、旧前缀不重复计数。这证明所测普通技能的进行中路径，不代替弹药、移动或其他特殊结束语义的独立覆盖。

这里沿用既有 MuMu 修复 `91e47ecc` 的“实时发布仍开放的技能伤害窗口”，没有新增 PC 技能计算算法，也不要求等技能结束才显示占比。PC 实机已有进行中窗口的有限分量；当生产端错误拒绝结算并把结算族全局质量标记置为失败后，版本 7 完整技能分量会按原规则降为未知，仍可保留已观察的部分分量。应修复源头证书及其质量污染，不能通过改变技能起点或放宽统计覆盖来掩盖。

既有 `test_event_capture.py`、`test_event_stream.py`、`test_lifecycle_events.py` 和 `test_stats_core.py` 继续覆盖累计权威、归属、跨局和覆盖语义。接口测试是离线合成与受控进程证明，不等于新的实机战斗验收。此前 PC 冷启动及手动重连成功只证明启动与连接链路；不同技能、护盾机制、召唤归属及特殊结算仍按[事件采集验收](event-capture.md)记录已覆盖与未覆盖范围。本轮不调整旧 `snapshot_only` UI 文案。

2026-11-22 对既有 `1.0.0-rc.2026112204`（源码 `0f612e7b06296eb550741236b4da7597e483cea1`、PC JS `f9db4bb6…`）冻结 CE-5 记录的审计发现：7 名正输出干员的 368 笔普通正伤害已有公式前后值配对，但对应 368 笔普通 HP 结算记录均为 `amount: 0`、`complete: false`。这不表示实际没有扣血，也不能用全局 ready 声明修成完整值；版本 7 结算指标仍须保持未知。另有 2 笔元素爆发具有完整的独立证明，可保留其部分统计，不能代表普通结算或整局已覆盖。

上述基线记录没有输出回调的入口顺序，自身不能区分未匹配输出和过早捕获零 `realDelta` 等原因。后续同一已验证 PC 构建的实机时序诊断确认，所测普通命中的目标回调返回时 HP/`realDelta` 尚未更新，真正扣血发生在外层 `ApplyModifier` 结束前；这提供了生产端联合证书的时序依据，不能仅靠补质量声明修复。

连续元素子结算也属于同一生产端证明。普通命中的目标回调内或扣血后，可以继续产生无来源和具名来源的元素伤害；不能用一个子结算槽覆盖更早的证书，再把父命中判为缺证。共享运行时按同线程、同目标及原始时钟/作用域保存至多16段连续证书，逐段核对原生实际扣血、HP端点及已发出的`damage_settlement`；v1历史录制还配对原`elemental_burst`，当前v2不再依赖它。具名子结算还需复核来源指针、类、实例及角色data UID。父命中的金额始终来自它自己的原生有符号结果，不能由名义伤害或整段HP差反推。原有父先单段保留`nested_settlement`证明；子先或多段附加`nested_elemental_chain`。已确认子结算的金额和归属不变，父命中只有闭环才能从未知恢复；确认协议不变。缺少子证书、跨线程同目标重叠、身份/作用域变化、盾证据不明或超过上限时继续保留未知。归档终点的完整覆盖须另有最终水位，不能由逐笔证明代替。

父命中首次受击回调取得的 HP-before 是其观察区间起点。此前已完全结束的子回调不再污染该区间；只有真实的首次目标证书、已排除在途同目标或未知目标活动、没有跨区间 Modifier 复用时才能重新定界。缺少/重复目标回调、区间内仍有嵌套或跨线程写入继续保守处理，不能在最终返回时单凭 HP 差抹除嵌套状态。

随后标准 `2205`（源码 `bd79f5872a37d5b4d54195bb1b5b5347d56bb466`）的冻结胜利记录中，425 笔普通结算有 402 笔完整、23 笔不完整。实机还观察到目标入口量 306，最终量与实际扣血均约为 397.8，公式前值为 1306；旧的入口量必须等于最终量的守门因此被证伪。新的受控回归使用该实际最终值及其他非固定倍率、下降和时序组合，依据最终原生字段的一致性修复生产端，而非放宽质量声明。

这些发现与受控回归均不等于本接口候选已完成实机战斗验收；修复后的数值、归属、技能/护盾机制及覆盖仍需独立记录。本机原始证据已冻结，未作为仓库公开可下载附件。

## 卫戍可选在场点

PC 继续通过原有独立低频 `capture_presence` 读活角色，点的时钟绑定物理快照 `autochess_snapshot_anchor`。现在 phase 4/5/6 的 1–4 个已确认账号均可采集，实际尝试计数 `sample_seq` 随同次读取透传。MuMu 在已有 closing `mark` 请求中每墙钟秒最多附带一次 `with_presence: true`，不增加 RPC 次数、回调钩子或宽编队读取。生产端在普通 mark 已生成后独立守卫角色集合，返回可选 `native_autochess_presence`（version 1、状态、原始时钟/fixed、范围/模式及活角色）。失败只使该可选点不可用，不能改写原 mark 的序号、epoch、质量或重试标记。

MuMu 可选读取限制为 4096 个引用、128 个角色、12000 次计费操作、256 KiB 和 12ms 墙钟预算；超限立即保留未知，不连续重试。敌人只做类型判别，不读取 Character 数据。只缓存不可变元数据布局，活值、根、时钟、角色生命周期、单位引用及作用域仍双次现场校验。预算和受控夹具证明有界行为，不代表模拟器实机 FPS。

宿主先接受原有事件窗口，再严格校验可选字段与 closing mark、当前账号成员和阵营，发布 `{version:1, binding:'event_mark', sample_seq, frame, game_time, units}`。它绑定 closing mark 的真实时间，不能拿更早的独立累计快照时间拒收或替换。可选失败发布 `units: null`，未调度不发布字段。每次真实尝试先递增序号，即使整个窗口后来被拒绝；消费者只连接相邻尝试，不能越过未发布的失败。PC 新点沿用自己的物理时钟与相邻尝试约束，旧无序号记录保留原有兼容读取。两种时钟依据不得混用。

## 真实部署生命周期（可选 version 1）

两端共享生产者在元数据验证 `Character.Born(): Void`（真实 override，与敌人使用的 BObject.Born 地址必须不同）及 `BattleController.OnCharacterFinished(Character, Entity.FinishReason): Void` 后发布真实本体边界。验证包括枚举 declaring type、Int32 布局及每个 literal；`Entity.FinishReason` 与 `Ability.FinishReason` 绝不通用。能力不可验证时不发布证书，完整分母保持未知。没有读取 `m_createdTime` / `m_deadTime` 作为出生或死亡时间。

`character_born` 是独立事件，amount=0，不开启技能。它只在同 Character、data UID、部署 instance UID、原始 frame/Q32 时钟及 scope 保持一致，且 Character.Born 调用使 borned 从 false 变为 true、isDisappeared 为 false 时发布。`skill_end` 的 CharacterFinished 记录可携带同样的 body 证书，不要求当前有技能键或窗口。

两种记录的显式字段为 `source_instance_uid: uint32>0` 和 `actor_lifecycle: {version:1,state:'born'|'finish',instance_uid,data_uid,frame,game_time,fixed}`；fixed 是原始非负 Q32 Int64 十进制字符串。finish 另含 `reason_type:'Torappu.Battle.Entity.FinishReason'`、`reason:Int32`。scope 沿用该回调的原始 `native_autochess_scope`，不能借后继快照补写。`event_processing_payload` 保留这些字段；EventStream 完成顺序、时间、scope 与本家 actor 归属校验后，才把证书交给 Statistics 的部署归约器。

永久终点只接受已验证 literal `REACH_EXIT / HP_ZERO / FALLDOWN / WITHDRAW / DEADLIKE_WITHDRAW / SILENT_WITHDRAW / HP_ZERO_WITH_NO_SOURCE / REPLACED`。NONE、OTHER、RESPAWN_SELF 与 MOVE_LIKE_RESPAWN_SELF/EXTERNAL 不认证永久终点。11/12 仍保留移动语义，不能终止本体生命周期。

技能回调和 typed native cast 返回在已证身份下透传 `source_instance_uid`；实际 Modifier 来源在入口和返回保持同身份时也透传该字段，不能拿召唤实例 UID 冒充宿主本体。无匹配激活窗的 wait=true cast 保留 `(owner,dataUID,instanceUID)` 的未知状态，不升级为 owner-wide 归属缺证，也不假造生效开始。真实未来激活只恢复未来分类；已记录的未知命中保留。

零帧 point v2 可独立附带 `lifecycle_baseline:{version:1,complete:true,frame:0,game_time:0,fixed:'0',units:[...]}`。每个 unit 含 `cid,data_uid,instance_uid,player_index,player_uid,player_side,inited:true,borned:true,disappeared:false`。生产者从原始零帧完整 UnitManager 集合读取，经既有类型/布局验证、完整引用读取、账号匹配和双次现场 guard；非本体 token 不进入集合。缺字段、短读、重复 data/native UID、非法flags或名单冲突使该独立证书不可用，不改旧技能初态语义。消费者只按同 point 的玩家/member、原始scope与零时钟归约，从0截入当前小局分母，不声称该本体在备战时的出生时间。旧 point v1/v2 无证书时保守兼容。


完整生命周期能力必须实际安装 Character.Born 与 typed CharacterFinished 两个钩子，零帧证书和其质量声明均须 `character_lifecycle_ready:true`，宿主还核对当前 mark/read 两端同能力；缺任一、安装失败或地址 alias 均不能认证完整分母。该可选失败不改旧统计。PC v29 的选取事实冻结于测试 fixture（metadata SHA e58fb854…），验证 Entity/Ability 的两个不同 declaring type，以及真实 Character.Born override、OnAfterSkill(BasicSkill,Ability.FinishReason) 与 OnCastFinish(Ability,Ability.FinishReason,Boolean) 原始签名。

`skill_cast_invocation` 是生产者每次 typed BasicSkill.OnCastFinish enter 的唯一正整数 ID。只有同一线程嵌套调用、同 owner/skill 指针、data/native UID、帧/Q32/scope 的 Character.OnAfterSkill 与最终 native leave 才共用；真实效果端点在同调用内也可带此证书。正常 AfterSkill 先于 native leave 时，已真实生效的窗口暂存 pending 状态，必须同 ID、同本体、同技能、同刻最终 wait 状态才能继续；原始回调/序号/重放顺序保持。缺真实生效起点、不同 invocation/body、失败返回或真实效果已终止均不能用 wait=true 开窗。旧记录没有此证书时保留旧规则。

## 高频回血与失败现场

HP setter 的入口与返回仍逐次校验。时钟入口使用同一块原始帧/Q32读取，并独立重读关闭帧；终点身份只缓存不可变类名，活指针仍现场核验。最多128个目标保存尚未分配序号的回血尾段，首笔真实恢复保留，之后只合并同目标、同帧、同scope的连续单调恢复。任何非HP事件、mark/read/ACK发布边界都会先处理相应尾段；已发布前缀不可回写。压缩记录数不等于原生回调数，`enemy_regen_ticks` 保留被覆盖的恢复次数。

scope缓存失败后仅允许一次有界scope-only恢复：根、完整账号双射、slot/profile/UID引用和唯一manager必须与原图一致，并通过双次现场guard。容器计数或版本变化本身不再使后续回调一直缓存不可用；真实身份图变化继续拒收，不恢复旧角色归属缓存。

可选 `damage_settlement_failure_samples_version` / `damage_settlement_failure_samples` 保存最多16条首次失败现场，按scope、来源、目标和拒证原因去重。使用回调已经读到的HP前后值、原生有符号增量、盾/取消/嵌套信息，不增加原生读取和逐回调IPC。该字段默认进入战绩，旧档缺字段仍兼容；它不把不一致的HP差反推成回血或有效伤害。性能夹具的桥读和有界队列对照、独立自有进程原语耗时与实际游戏FPS分别记录。


普通嵌套子结算的真实零 HP 终点不再被当作缺证：父回调已返回、父原生 realDelta 与父 HP 检查点在子入口独立闭环，子有自己的完整原生结算证书、killed 与 HP=0 一致，且父结束时身份/时钟/原始 realDelta 未变、目标仍等于子真实终点时，可保留父独立扣血。父不承担子击杀，子的名义伤害和过量不变；不从整段 HP 差或名义量反算任一击。UID 归零、子缺证、重复/额外子回调、盾未知、父字段变化或终点不连续仍拒绝。此分支由 PC/MuMu 真实生产回调的拥有内存夹具验证，不代表测10 R9/R10 溯光星源未核实命中的原始原因已经确认；那些历史归档未保存逐笔 guard。


敌人首次地图层入场可能晚于真实 Born。既有 BObject.Born 回调在同指针/类/CID/native UID、borned 从0到1、原始 frame/Q32/scope 不变时，可保存只读出生证书；延后的 enemy_enter 附 `proof.native_enemy_birth`（version1、source_method、target/native UID/native pointer、原始时钟、Born-enter前及成功return后的原始sequence、return epoch）。无额外钩子或RPC。回收、假死/重生、换scope或读取失败清除此可选证书。宿主严格校验后，仅当元素条同UID与同native pointer、其原始序号严格位于Born成功return与首次entry之间，且bar进入时的原始epoch严格晚于Born-return epoch（仅pending delayed body的bar附此小型标记）、原时钟顺序及连续覆盖成立时，保留这条本周期证据。无证书仍按原规则清条，不从“首次看到entry”猜出生；Born前或Born内部旧条、同UID回收新周期、不同pointer/UID、坏entry/坏重生、观测gap不能借此恢复。真实reborn仍清旧条，新bar只恢复未来归属。该边界已由PC/MuMu拥有内存原生产回调验证，不声称解释缺原始guard的测10第14回合11笔元素归属未知。


## 正时间空 scope 锚点（pending_scope_start v1）

这是宿主采集事务，不是 producer 零帧证书。仅在同一 observer 寿命内，旧零起点 combat 前缀通过既有严格 successor watermark 封口、并观察到 phase/round 先行的正时间新 scope 时，Feed 保存独立 playing 读、原始前后静默 mark、与已接受 cursor 一致的空 baseline、真实正 frame/game_time 及原 observer；fresh late-attach 或旧前缀不能严格封口时仍立即建立原有晚基线，不额外等待首事件。pending 不推进 ACK，也不发布新的金额基线。无此原始锚点的旧录制保留原合同，不能事后补造。

首次后续事件必须由采集层调用 `read_round_start_range(anchor.sequence, closing.sequence]`，按2048条分页、总100000条及32MiB原始JSON预算读取。`events.pending_scope_start` 恰有 `version/anchor/pages`；anchor 恰有真实 `sample/roster_frame/before/after/sequence/drain`，每页恰有 `after/through/batch`。页与合并 drain 的事件字节语义必须一致，序号完整连续，capture clock 位于锚点与当前端点之间，同帧时间相同；每页重新验证 source/session/build/generation、五根、scope 和唯一账号表。序号跨锚点增长时 callback epoch 必须严格增长；若原始页声明 epoch，还须在锚点与closing epoch之间单调且不伪零。重试、换 observer、同对象重启导致 `_cursor_lifetime` 改变、缺页、冲突或超预算均不 ACK，不改读当前 baseline 兜底。寿命令牌在read前后、consumer交接与ACK前分别重验；令牌是现场owner边界，不接受回放payload自报令牌来授权新source。

Sessions 只消费已取得的事务，不调用 observer。初始化用 anchor 的真实 frame/time 与该帧空 roster，随后回放全部前缀，再交 Runtime 确认。新typed字段不接受consumer提供的zero/技能初态/生命周期完整声明；PC重验原native_window_guard、serial/PID birth的canonical绑定，Android重验独立guard/roots/session。正时间仍是晚覆盖；观察窗口内已证金额可以完整，但不能升级为完整整scope、已知技能初态或完整body在场。

无reset的合法零输出scope不能丢弃：`pending_scope_exit` 保存原始独立successor，并物化anchor和最后同scope空窗口后归档，沿用严格终局/body门禁，缺证仍未知。明确absence/finished/process-change通过 `pending_scope_end` 保留原lifecycle及原terminal诊断并归档已知观察窗口，不证明未读尾部、不推进更高ACK。原队列不因零伤害或timeout被释放。

只有 `pending_scope_superseded` 的原始独立PC serial严格+1、原生generation严格+1、PID birth/build/五根/manager/账号相同、两时钟确实回退、整个空片段没有进入callback且source无未定位失败时，才允许用真实reset替代pre-start空片段。它和正时间初始化是不同事务；不把旧锚点重标成零，不以新scope标志自证reset。未知版本、冲突事务、已建立generation上的重初始化一律拒绝。

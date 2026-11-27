# 按业务消费者裁剪原生伤害取证

本轮承接 `ef1486640e42146e7a8aaaae22488b1ac0be35f0` 的单一事件存储、ACK、Destroy
回放证据域和租约修复。改动针对 Windows 原生运输合同；伤害、公式、护盾、技能、归属
和生命周期继续由同一份共享 JS 推进。Android/Frida 使用这份业务实现，未新增第二套统计。

## 实机依据及范围

948137d 第12回合 18:36:53–18:37:24 的30个完整 FPS 秒桶中位4，27个低于10；
原始边界约63832/s，取证入队72.45MiB/s。HP 轻量化已生效，Buff 进入/退出仍各约
32/31微秒、90/89次读取。恢复段与协防明显较轻；管道写入约0.330ms/s，WPF未见
对应秒级应用停顿。这些是旧版本测量，不能作为本候选的实机效果，也不能独自证明全部
帧损失由 ArkDPS 导致。

侧会话报告的 SHA-256 为：

- `round12-performance-report.md`：`ecfcc05f66902cc91e57261e77cb4b475c9970c7b66cf45004a4d959d54ae692`
- `buff-read-ledger/read-breakdown.md`：`a0b491c3fc023a70a8be12919735b0809e052e6735972fd37b2e0123bb715a77`

18:39 的 Destroy 结构拒收是另一个已在 ef14866 修复的问题；不将它用来解释18:37掉帧。

真实安装清单由 `tests/damage_demand_installed_inventory.js` 从实际 attach 与 callback
对象生成，见[完整清单](evidence/capture-demand/installed-hooks.json)。普通配置实际安装
11个伤害/公式地址；`Entity.OnTakeDamage`、`Unit.OnAfterOutputDamage` 为 trace-only。
离线受控补入这两条 metadata 后，trace配置确实安装13个地址并执行其回调。
这不是对实机版本签名的证明。仅被枚举的 Execute/OnBlockDamage/OnAppliedModifier/
OnTakeHeal 没有被冒认为已安装钩子。HP、生命周期、技能和终局监听器另列保留用途。

## 唯一消费者及每条路径

| 路径 | 普通进入 | 普通退出 | 普通完整scope |
| --- | --- | --- | --- |
| Buff.OnTakeDamage | 独立时钟、Buff引用入栈 | 仅弹栈与结构闭合 | 两边不需要 |
| CalculateDamage/NoEvent | 时钟、目标ID/条件UID、来源ID、原始参数 | 时钟、AttackInfo类型、条件来源/目标UID、精确Q32返回值 | 两边不需要 |
| CalculateDamageInternal | 指针匹配与借用 | 恢复借用指针 | 两边不需要；不读普通时钟 |
| GetFinalAtk | 类型；postFormula分支时钟 | Q32结果；postFormula分支时钟 | 两边不需要 |
| 两个NewDamageModifierAfterCalculate | 时钟、ID/UID与实际使用的ABI槽 | 时钟、source/target指针与UID、raw/type/0x60判零、80/88精确值 | 两边不需要 |
| Unit/Entity.OnTakeDamage | 时钟、当前目标/来源及独立父检查点 | 时钟、目标与父结算复验、必要诊断 | 仅退出 |
| Entity/Unit.OnAfterOutputDamage | 输出时点金额/realDelta、来源/目标、条件token/form | 最终HP/身份与父结算复验 | 两边保留，入口有条件消费者 |
| Entity.ApplyModifier | 初始目标、来源、结算锚、公式候选、条件token/form | 最终金额、HP、归属与完整结算证据 | 两边保留，入口有条件消费者 |
| BlockDamage._AssignDynamicVar | 本次父Buff、来源UID、目标UID、incoming、黑板before | 黑板after、独立时钟、数字吸收/legacy事件 | 仅退出 |

逐字段的生产根、偏移、长度、用途、调用分支与保留/删除决定分别见：

- [伤害/护盾字段账](evidence/capture-demand/damage-fields.csv)：570行，包括缩窄后的替代字段。
- [公共scope字段账](evidence/capture-demand/scope-fields.csv)：64个实际生成字段及边界用途。
- [公式字段账](evidence/capture-demand/formula-fields.csv)：公式与两个重载的逐字段映射。

表中的动态布局使用离线固定 metadata；产品的技能/AttackInfo等偏移来自启动时已核实的
metadata，不将夹具地址或示例偏移冒充实机逐地址日志。字符串的类、长度、正文及独立
复读保留；删除 actor class 不等于删除字符串类型证据。

具体删除包括无消费者的receiver树、source HP/出生状态、目标出生标志、OnTake主退出
来源树、非Apply的origin字段、Block退出modifier树，以及公式中通用actor/Character数据。
Apply/Output 的 token/form/host 证据仍保留。Unit退出验证父来源时读取的是本次
`parent.body`，不能用已删的主来源树或旧缓存替代。父检查点的HP、身份、子调用关系和
时钟不跨记录合并。

Factory的7/8显式参数重载在x64含sret时分别保留mask `0xb7`/`0x16f`；仅删除未读取槽。
`0x60`判零和`0x68`类型仍保留；取消原因是`0x70`，不混淆字段名称。ABI控制流所需的
入口返回地址仍读取，计量时与Snapshot读取分列。

## 合同与执行边界

扩展既有 `required_projections` 机制，增加 `consumer_edge_evidence` 能力。
`CompiledPlan` 按已声明、受白名单约束的回调合同编译普通scope边界，运行期不判断玩家
归属、伤害正负、Buff类型或业务栈状态。缺新能力的组件不宣称合同完整。

声明的四个12字节独立时钟仍完整。Buff退出与Internal两边的时钟标为
`tier: publication`，普通边界不生产，发布候选或冷取证时恢复。其余涉及业务时钟的
边界不得改变tier；错误字节数、根、阶段、tier分布和缺冷闭合证明均被实际compiler拒收。
publication/cold具有完整当时scope及本次时钟，终局仍按真实Destroy完成点发证。

原始进入/退出、参数、父子/TLS调用关系、线程出生身份、模式世代、连续处理与闭合
前缀始终生产；没有把少量业务输出等同于少量原始输入。游戏线程不等后台，不排裸指针
让后台补读。队列满仍显式缺口，不提前ACK。

## 验证与重放入口

`tests/capture_demand_run.py` 生成真实合同、固定自有heap，运行实际
`capture_scope_point`、队列serialize/pop，逐笔记录字段标签。原生读取、ABI读取、
FieldState/Segment/载荷、scope/parent/publication/cold分别计数；插桩版本仅作计数。
后台读取已持有副本不计为生产读。基线与候选使用同heap，失败回执保留。

`tests/damage_demand_aggregate.py` 重生伤害及公共scope字段账。
`tests/test_capture_demand.py` 运行四份实际共享消费者夹具，
同时验证PC/Android、trace上下文等价、入口stamp为零的前提，以及缺父检查点确实失败。
不能用原地修改已冻结事件而没有真正注入故障的夹具制造通过。

实际原生门禁包含10份consumer合同/70个拒收负例，以及37份最新完整合同/274个负例。
已有嵌套普通/元素、独立HP writer、护盾、跨帧、空端点、首帧、Destroy、发布及ACK门禁
继续执行。混合吞吐使用未插桩原生代码、真实本进程ReadProcessMemory和共享消费者，
与逐笔计数、Node语义夹具、实机FPS分别报告。

## 同输入测量与限制（677c420）

计数基线是 `ef14866`，候选适配器 SHA-256 为
`d0c972dfc2c5d376fd640b6212baf972600dbc1e01369b0adc5bb4f4491b4ad6`。
以下是普通边界的实际 Snapshot 读取次数，未加入口ABI返回地址及所选栈参数读取：

| 路线 | 进入，修改前→后 | 退出，修改前→后 |
| --- | ---: | ---: |
| ApplyModifier | 157→140 | 157→140 |
| Entity/Unit.OnAfterOutputDamage | 157→139 | 157→139 |
| Entity/Unit.OnTakeDamage | 157→51 | 157→111 |
| Buff.OnTakeDamage | 89→2 | 89→0 |
| BlockDamage | 209→45 | 157→91 |
| CalculateDamage / NoEvent | 103→17 | 103→5 |
| CalculateDamageInternal | 81→0 | 81→0 |
| GetFinalAtk | 81→3 | 81→2 |
| NewDamageModifierAfterCalculate，两个重载 | 136→18 | 202→10 |

完整字节、ABI、普通/发布/冷取证/父检查点分账见
[生产者对照](evidence/capture-demand/producer-comparison.csv)。计数输入的scope为64字段、
publication为540字段；这不是每场实机的固定规模。计数构建不能用于性能结论。

干净混合实验使用原生本进程读取、实际队列与同一共享JS消费者；五条合成路线等权，
并非实机钩子比例。此处scope为64字段、publication为1604字段，和上面的计数输入
分开登记。六个语义场景分别验证伤害、工厂候选和数字护盾，基线/候选输入及
完整事件一致，独立预期包含40伤害、30伤害加10护盾吸收等。

- 最终三次不限速试验：完整链路中位数662.33→686.86 cycle/s，约增加3.70%。
  生产者耗时中位数896.62→432.34ms，约减少51.8%；此数包括现场读取、取证与入队，
  不是CPU百分比或实机FPS。前一次干净试验仅增加1.49%且区间重叠；不将少量离线
  重复试验写成稳定的实机改进倍率。
- 400 cycle/s、10秒、消费者暂停500ms：两边43200条事实均处理/闭合/发布，
  无缺口；读取5698960→2922960，入队345938000→175422800字节。
  暂停结束后追平约966→616ms，峰值队列字节17723229→8842150。
- 额外800 cycle/s、每cycle加35次HP写入、暂停500ms的最终试验，646400条事实
  最终均处理/闭合/发布，无缺口；但生产约10秒、全部处理需12.713秒，暂停结束后
  11.213秒才追平，峰136956条/111238435字节。稳态积压增长，性能目标未通过。
  前一组QPC年龄诊断开启的同预算试验约9.279秒即 `queue_full=4`，599580条已入队、
  460315条已处理，失败回执保留，未改变容量、拒收或ACK来取得最终无缺口。
  不能宣称约6.4万条/秒的混合负载已达标。

回执入口为 `runtime/capture-demand/mixed/final-gates/suite.json` 和
`runtime/capture-demand/mixed/final-extra800/receipt.json`，摘要及文件身份见
[测量回执](evidence/capture-demand/measurement-receipt.json)。早期带JS计时包装的约9%结果
不作为干净性能结论。默认试验不取诊断QPC，符合产品性能计时关闭语义；显式抽样
分段另列忙消费与空轮询，二者不能混为业务成本。当前收益是减少游戏线程生产工作和队列字节；后台服务能力仍是
限制，实机FPS与更新延迟需要新包对照验证。

最终全量回归为6517 passed、29 skipped；新DLL在自有进程中通过owned view、租约过期、
数值边界、直接HP批次、父/发布回退、轻量HP和冷取证期限检查。实际完整引擎的冷类型、
采样调度与Destroy最终read/ACK回归通过。独立审查未发现新增P1/P2；在临时源码副本
去掉publication scope覆盖条件后，原有行为断言确实失败，产品源码前后摘要一致。
另一个独立轻量HP场景完成450000次setter、约44960次/秒供给与500ms消费者暂停，
暂停结束后约88.65ms追平，无缺口。该场景大量使用原生planned omission，不能替代
上述包含实际伤害业务的高负载混合验收。

## 独立审计的后续收口（2a964ab）

随后收到的独立报告冻结于 `ef14866`，其2604行账将scope按每个进入、退出及父检查点
展开；上面的570行伤害账与64行公共scope采用分开列示的口径，不是遗漏了其余回调。
报告和字段账SHA、当前源摘要及后续回执见
[审计接入回执](evidence/capture-demand-followup/audit-receipt.json)。先对照 `677c420`，
其已完成的普通字段和边界裁剪不重复实施；剩余父合同与错误目标分支继续在原有所有者内修复。

父检查点进一步移除7个没有父现场消费者的token/form字段，以及重复的 `parent.hp`。
source类、实例UID、CID、data类/UID、Modifier和目标HP仍保留。token及阿米娅的完整
形态/宿主证明仍由它们自己的Apply/Output回调提供。单一 `parent.target` HP字段供
同一 `parent.body`、同一checkpoint的读取，不跨进入/退出、父子checkpoint或guard缓存。
字符串内部类、长度、正文复读与所有前后guard保持原样，未引入通用地址缓存。

[父字段对账](evidence/capture-demand-followup/parent-fields.csv)列出5种合同共120行。
同一固定heap的104个真实生产者边界均无捕获错误；一个活动父级的24字段降至16字段，
读取61→39次，序列化2984→1862字节。该计数不代表所有分支的最大读取或实机耗时。

补验收发现一个早于本次裁剪的错误：Unit.OnTakeDamage的receiver不同于父Modifier目标时，
仍被登记进父 `targetHit`；父退出随后读取该对象的HP/ID，而父记录并未持有这些字节。
旧直接路径产生未核实的名义伤害，严格owned路径却令整个流出现结构缺口。

现在先按已有目标一致性规则分流。错目标只保存子调用现场的ID/地址描述，不保存供父
回放解引用的指针；父结算跳过对此对象的HP、UID和结算证明读取，沿既有统一record出口
保留名义伤害及未知状态。没有补造HP、realDelta或可信命中。该状态仍属于当前Apply调用：

| `targetCallbackMismatch` | 含义与可用信息 |
| --- | --- |
| 未设置 | 沿既有targetHit及结算验证路径处理 |
| ID/地址描述 | 只有一笔不匹配目标的入口观察；仅供未核实名义记录描述 |
| ID/地址为null | 多笔不兼容目标；不能选取任意一笔冒充唯一目标 |

既有 `targetHitAmbiguous`/`targetCommitRejected` 阻止该调用恢复可信；Apply结束时随原
调用上下文释放。下一笔独立调用从正常初态开始，不跨回合保存此状态。原始字段缺项、
坏guard、错租约或身份仍严格拒收，与这种已观测到的语义不一致分别处理。

新门禁包含30个PC严格owned场景及MuMu实际Android适配器的两种trace场景：错目标保留
名义40并保持未知，同会话下一笔正常命中可信10；token/阿米娅/嵌套元素各有真实10+40
结算；同对象自治疗40与直接路径一致。缺来源ID、目标UID、主token宿主或scope guard
实际失败；跨帧和形态变化不能借旧证明。所有owned读取均无延迟现场补读。
在内存副本跳过错目标判断后，原行为测试检出结构缺口，产品文件前后摘要一致。

真实已交付DLL的四项补充门禁也通过：72个原生边界生成7条canonical事件并read/ACK至7；
删除Unit必需金额字段后真实code6拒收；带Output的错receiver后正常命中共28边界，
read/ACK至3且精确目标/实例与独立声明一致；另一个fresh进程未read就ACK得到服务端
错误。poison后本地拦截与绕过本地标记后的真实断管ERR232分别记录，后者不冒充执行过
DLL ACK业务，也不补写断线后不可查询的ACK状态。最终全量6518 passed、29 skipped。
回执为 `runtime/capture-demand/dll/current-identity-final/receipt.json`。
本次离线补齐不构成同级实机FPS验收，677c420高压混合场景的持续积压限制继续保留。

## 整合d14fdd1

按用户要求，在上述收口完成后合入 `d14fdd110ca83b45c168374bc6acfdc5c0d0803b`，
保留其原始提交关系。普通PC冷树验证完成后只允许当前记录持有的规范绑定发布
**后续采集**所需的图；不以新图认证本条旧记录。真实零帧前缀、独立终局与生命周期
分母、详情页读取的修复及来源边界见[H7-4根因修复](h7-4-root-fixes.md)。

字段裁剪、错receiver修复、七项capability与严格冷取证/发布/ACK均保留。
测试装配改用 `ownedFields` 的正式 `reuseBridge` 参数，移除临时源码字符串手术。
整合后6554 passed、29 skipped；专项297 passed；无输入桌面测试验证普通/参考详情
使用canonical的10秒与50%，没有操作窗口或游戏。

实际DLL五项门禁通过，除前述四项外新增普通关卡cold→未来图→嵌套结算→read/ACK：
初始化无预发布图；首个采样点raw ticket为0、**游戏frame为30**，只持有cold证据。
真实owner发布scope/publication图后，本条facts仍不改变；下一次独立采样才携带新图。
随后8个真实原生边界生成普通40、元素60的完整结算和元素条5，父子HP分别100→60、
60→0，最终read/ACK至4（包括初始化的一条fixture事件）。元素条不计作额外伤害。
相同声明输入的合并前源码反例明确失败于未发布后续图，而不是另一个缺字段或协议错误。
这验证冷交接，不把raw ticket 0当作游戏第0帧实机验收。

整合回执及当前源码、工具、DLL与输入摘要见
[d14整合回执](evidence/capture-demand-followup/integration-d14.json)。
普通PC真实零帧前缀的宿主接纳由专项正/负例验证，实际游戏IL2CPP发现、游戏方法ABI和
新包同级场景FPS仍需实机验收。高压混合消费积压的既有未达标结果继续保留。

## 变更控制

范围局限于原生字段合同、编译/取证器的既有选择机制、共享JS现有调用上下文的目标匹配，
以及离线测试和证据文档。Python统计、WPF、事件存储/ACK所有者未改变；没有第二套业务所有者。
新能力由既有合同编译器拥有；缺项应在合同或owned回放边界失败，而非默认通过。
撤回必须配套恢复JS与DLL，不能混装不匹配源码清单。旧战绩兼容与本轮实时生产裁剪无关。

交付前以固定提交、DLL、实际执行source、输入heap和脚本哈希核对最终回执。
离线吞吐/读数改善不代替实机FPS。复测保持相同倍速、帧限和统计选项，对照同级防御、
协防、密集伤害与盾吸收，记录完整秒窗、回合/阶段及恢复时刻。

# 国服 PC 采集预研（ARK-16）

## 2026-11-21 · CE-5 演习

ArkDPS 基于 rc.8 候选 `ee7cf86b61642108db8c832c0fc9994c4ff76747`。实机使用鹰角启动器的国服 Windows x64 客户端，客户端文件与已知构建的三个 SHA-256 一致，演习后资源清单记录 `V079 / 26-11-13-06-34-18-84612175`。玩家通过游戏界面完成 CE-5 演习。客户端文件自带 ACE，取证期间系统报告 ACE 的两项服务及 `ACE-BASE` 驱动为停止状态。

| 检查 | 观测 |
| --- | --- |
| `Player.log` | 从 7,721 字节增长到 18,496 字节。新增 154 个非空行，其中 48 个战斗相关匹配为战斗 UI 调用栈或资源诊断。未找到逐次伤害、治疗、护盾或结算统计。 |
| 其他本地文件 | 启动器和 SDK 日志未找到可用的战斗数值。安装目录与游戏用户目录未发现 `BattleLog`。结算附近有二进制文件 `eld_Arknights.db` 更新，公开格式和字段尚未确认。 |
| Windows 只读访问 | 在启动阶段和演习结束后分别识别到与安装路径匹配的 `Arknights.exe`。两次请求 `PROCESS_QUERY_INFORMATION \| PROCESS_VM_READ` 均在 `OpenProcess` 返回错误码 `5`，读取字节数为 `0`。 |
| 对照 | 探针在中等完整性级别运行。同一环境使用相同权限成功读取自建测试进程中的已知字符串。游戏进程拒绝访问的具体原因尚未确认。 |

本轮没有取得可供统计的实时数据源。PC 快照、逐次事件与 MuMu 功能齐平均待验证。后续可使用已确认的官方数据接口或可正常访问的普通只读来源重新对账。

## 2026-11-21 · 权限对照与开源实现核查

用同一个已启动的客户端比较 Codex 受限令牌与沙箱外普通令牌。两者均处于中等完整性级别，状态为未提升。Windows 在两个环境里都允许 `PROCESS_QUERY_LIMITED_INFORMATION`，并拒绝 `PROCESS_QUERY_INFORMATION`、`PROCESS_VM_READ`、二者的组合和 `READ_CONTROL`，均返回 `5`。独立申请 `PROCESS_VM_READ` 也失败，因此组合掩码和 Codex 受限令牌都无法解释这次拒绝。对照只申请并关闭句柄，未读取游戏内存。[Windows 进程访问权限](https://learn.microsoft.com/en-us/windows/win32/procthread/process-security-and-access-rights)

`GetProcessInformation` 返回 `PROTECTION_LEVEL_NONE`。`OpenProcessToken(TOKEN_QUERY)` 返回 `5`，无法直接核对目标令牌。磁盘上已签名的 `Arknights.exe` 和启动器的 `Games.exe` 都声明了 `requireAdministrator`。这将调查集中到游戏与采集进程的权限差异，但还无法确定是哪条访问控制规则拒绝了请求，也无证据将拒绝归因于 ACE。[Windows 进程保护级别](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/ns-processthreadsapi-process_protection_level_information) · [Windows 应用清单](https://learn.microsoft.com/en-us/windows/desktop/sbscs/application-manifests)

用户随后在管理员 PowerShell 运行同一构建的 `--handle-only` 探针。北京时间 11:41:36 的输出为 `read_handle_available`，三个构建哈希一致，目标路径唯一，`OpenProcess(0x410)` 成功，读取次数为 `0`。本机对照证实运行权限差异影响句柄取得。读取内容和战斗字段仍待独立验证。用户提供的输出存为 `runtime/native-pc/2026-11-21-access/administrator-handle-user-report.json`。

| 来源 | 已核查的能力 |
| --- | --- |
| [ArknightsCostBarRuler](https://github.com/ZeroAd-06/ArknightsCostBarRuler) | 从官服 Windows 窗口的费用条估算逻辑时间，提供本地 API。费用满、禁止自然回费或锁费时，无法靠这个方法推进计时。 |
| [MAA 的 Windows 截图代码](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/ae2cd60b46841f3c60dda1af67705e7aaf2e4663/src/MaaCore/Controller/Win32Controller.cpp) 与[战场识别代码](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/ae2cd60b46841f3c60dda1af67705e7aaf2e4663/src/MaaCore/Task/BattleHelper.cpp) | 可识别击杀数、费用、部署栏等可见状态。当前实现没有给出逐干员原生累计和逐次结算数据。 |
| [鹰角启动器数据面板说明](https://customer-service.hypergryph.com/app/arknights/question/ART178365239398256169747501) | 公开范围包括理智和任务进度，本轮未找到该面板提供战斗统计的证据。 |
| [ArkHelper v1.4.2 快照契约](https://github.com/zhengkanqin/ArkHelper/blob/v1.4.2/include/arkhelp/ipc.h) · [CSV 导出](https://github.com/zhengkanqin/ArkHelper/blob/v1.4.2/native/modules/damage_meter/csv_export.cpp) | 开源项目声明在国服 PC 获取数据。v11 实时快照定义了战局、逻辑帧、游戏时间、逐干员及团队指标、能力掩码，以及事件缺失和行截断标记。CSV 同时导出实际扣血和计算伤害。当前机器未观察到该发布端运行。 |
| [ArkFramework v1.0.0 CSV 导出](https://github.com/wuyu8512/ArkFramework/blob/v1.0.0/plugins/damage_meter/csv_export.cpp) | 导出实际扣血、计算伤害和游戏时间，未找到独立的实时消费接口。 |

ArkHelper 的公开说明称其数据发布端使用进程内注入和 Hook。ArkDPS 的实验接收端只尝试读取一个已存在的战斗快照。实时头文件里的总伤字段按实际扣血定义，ArkDPS 的游戏累计伤害含过量，两者保留各自口径。连续事件、技能区间、接敌 DPS 和护盾归因仍需要独立日志及实机对账。[ArkHelper 项目说明](https://github.com/zhengkanqin/ArkHelper/blob/v1.4.2/README.md) · [ArkDPS 统计契约](statistics-core.md)

权限对照回执和本轮使用的诊断脚本保存在 `runtime/native-pc/2026-11-21-access/`。

用发行版 C++ 头文件按 Windows x64 布局编译了独立的合成快照。实验接收端读出预期的战局 `42`、帧 `309`、时间 `10.3` 秒、逐干员实际扣血 `123.25` 和治疗 `45.5`。护盾能力位未设置，输出为 `null`。本机正常运行游戏时读取 ArkHelper 快照返回 `feed_missing`（Windows 错误码 `2`）。具体客户端构建上的真实战斗输出尚未验收。

## 2026-11-21 · 官方 PC 客户端 2× 实机采样

用户在管理员 PowerShell 运行 `7945b7c` 的原生探针，报告实战使用 2×。关卡 ID 未记录。对应的游戏进程、三个构建哈希和内存模块字节匹配。探针通过 Windows 标准只读 API 完成 120 秒采样，于北京时间 11:57:27 正常结束。

| 验证项 | 实测结果 |
| --- | --- |
| 实时推进 | 同一局有 468 份通过同帧与归属链检查的样本，逻辑帧 `0→4347`，游戏时间 `0→144.89986` 秒。17 次读取期间跨帧或对象链变化被丢弃。 |
| 伤害累计 | 读到 7 个来源键，5 个来源观察到累计增长。逐来源伤害是物理、法术、真实和元素分类的和，治疗单独保留。 |
| 两种伤害字段 | 最后一次采样中，原生全局累计为 `127719.6875`，来源分类伤害之和为 `183360.08203125`。全局与来源分类来自不同的原生结算字段，不能用这一差值推算过量，也不能把全局值直接当作各来源占比的分母。 |
| 覆盖范围 | 全部有效样本在战斗状态 `2`，详细记录器指针持续为空。这次尚未覆盖非零治疗、召唤归属、逐次事件、结算、换局及 1× 对照。 |

采集验证了 Windows 原生实时伤害快照可用，后续对账需将稳定来源键和编队宿主归属在同一逻辑帧核对，再送入现有统计与生命周期。原始 JSON Lines 和独立校验回执分别位于 `runtime/native-pc/2026-11-21-battle/admin-20261121-115527.jsonl` 和 `runtime/native-pc/2026-11-21-battle/admin-20261121-115527-verified.json`。原始文件 SHA-256 为 `13eff4e1da6624a8c6d468caf63a5773ad96efaa230ab294c650e2a00dbe9abc`。

## 复现

通过鹰角启动器打开客户端。在普通权限的 PowerShell 中运行以下命令，将路径改为本机安装目录。

```powershell
python acceptance/probe_native_pc.py --game-root 'D:\games\Hypergryph Launcher\games\Arknights'
```

探针按三个文件的哈希、进程与模块路径限定目标。如 Windows 授予读取权限，探针会比较 `GameAssembly.dll` 中两个固定位置的 96 个字节。`read_gate_passed` 表示固定字节比较通过，仍需用实机战局验证游戏时钟、编队归属与累计数据。

用 `--handle-only` 可以只核对 Windows 是否授予读取句柄，随后立即关闭。`read_handle_available` 只表示句柄申请通过，该模式不枚举游戏模块或读取游戏内存。

`record_native_pc_battle.py` 复用一个已验证的只读句柄。它先校验匹配构建的模块字节，再在战局对象和逻辑帧的前后检查都通过时记录样本。实验 JSON Lines 含原生全局累计、逐来源分类累计的可用状态，以及详细记录器指针是否非空的布尔值。运行时使用 `--game-root`、`--output` 和 `--seconds`，输出必须是一个新文件。

外部实时快照探针可以检查当前 Windows 会话是否已有兼容的 ArkHelper 快照发布端，并将其当前快照输出为 JSON。

```powershell
python acceptance/probe_arkhelper_feed.py
```

录制已有发布端的实时变化时使用 `--watch-seconds 30`。该模式输出 JSON Lines，记录不同的有效快照和连接状态变化。汇总的 `frame_progress_observed` 只在同一局连续样本的逻辑帧与游戏时间同时增长时为真。换局或数据中断会重新建立基线。该标记只证明观察到推进，逐次事件的完整性仍需单独验证。

独立 Windows 进程使用 v1.4.2 合成数据验证了持续采集。接收端在同一局读到第 309 帧实际扣血 `123.25` 和第 339 帧 `225.25`。本机真实发布名称连续轮询返回 `feed_missing`，没有产生有效样本。两份回执分别位于 `runtime/native-pc/arkhelper-v1.4.2-abi/watch-result.json` 和 `runtime/native-pc/2026-11-21-arkhelper-watch/live-check.jsonl`。

本机回执位于 `runtime/native-pc/2026-11-21-ce5/receipt.json`，包含权限状态、构建身份及 `Player.log` 的比较结果。回执仅保存诊断与统计摘要。

## 2026-11-21 · Mon4terTeam ArkFramework 接口

用户提供的 Mon4terTeam fork 明确声明适配 Windows x64 的《明日方舟》PC 客户端，并在上游 ABI 1 之外提供 V2 逐次战斗记录。以下结论来自公开契约、导出格式和仓库中的回放快照。尚未找到该 fork 对外输出的 PC 逐次结算样本。[仓库说明](https://github.com/Mon4terTeam/ArkFramework)

| 可见接口 | 对 ArkDPS 的用途与缺口 |
| --- | --- |
| 战斗事件 V2 | 每条记录带全局序号、战局、逻辑帧、帧内顺序、来源、宿主和目标。伤害记录区分计算伤害与实际扣血，类型中含治疗。读取状态带部分覆盖、缓冲溢出与结束标志。公开字段没有明确的施盾人与实际吸收量、技能起止，也没有将该单条记录与 ArkDPS 同帧累计绑定的外部契约。该接口由框架在进程内提供。[战斗事件头文件](https://github.com/Mon4terTeam/ArkFramework/blob/main/include/ark/battle.h) |
| 外部 WebView 数据 | 每个 topic 只保留最新负载，战斗状态每 500 ms 发布一份快照。桥接消息序号用于这些发布，不能证明期间的逐次事件完整。[外部桥接说明](https://github.com/Mon4terTeam/ArkFramework/blob/main/docs/ExternalBridge.md) |
| Web 文件导出 | 核对提交 `a2ae65f37282c9ba2d64b759ce918ecaa82be87d` 的序列化代码。`_snapshot.json` 可包含伤害、治疗、承伤的统计和按帧汇总的曲线，另有单位当前盾量与技能状态。`damage.events` 是事件数量，`damage.sequence` 是统计快照附带的序号。可用作累计和状态对账，缺少逐次结算字段和同帧事件顺序。[Web 导出字段](https://github.com/Mon4terTeam/ArkFramework/blob/a2ae65f37282c9ba2d64b759ce918ecaa82be87d/plugins/BetterBattle/plugin.cpp#L702) |
| CSV 和回放文件 | 伤害 CSV 按干员导出累计计算和实际扣血，可作对账。回放 JSON 保存按逻辑帧排序的玩家操作。另有逐帧状态日志，技能字段可供离线核对技能状态变化，具体覆盖需查实际文件。[伤害 CSV 字段](https://github.com/Mon4terTeam/ArkFramework/blob/main/plugins/damage_meter/csv_export.cpp) · [BetterBattle 回放约定](https://github.com/Mon4terTeam/ArkFramework/blob/main/plugins/BetterBattle/README.md) |

同一提交下公开的 SK-5 数据含 11 份快照，来自时间轴标注的一次 PC 录像回放，覆盖逻辑帧 42 至 6,927。相邻快照相隔 88 至 1,216 帧，每秒 30 帧。11 份均提供技能状态，逐次事件和当前实现新增的 `damage` 汇总块均未随该批文件公开。最后一份已结束，记录了 50 次操作的回放完成。样本可供核对回放状态字段，无法定位技能生效或终止的精确逻辑帧。[SK-5 时间轴](https://github.com/Mon4terTeam/ArkFramework/blob/a2ae65f37282c9ba2d64b759ce918ecaa82be87d/webview/ui/fixtures/sk5-9op-live-20261103/timeline.json) · [SK-5 快照](https://github.com/Mon4terTeam/ArkFramework/tree/a2ae65f37282c9ba2d64b759ce918ecaa82be87d/webview/ui/fixtures/sk5-9op-live-20261103/snapshots)

要让 ArkDPS 发布连续观察者统计，仍需有序的外部记录、同局同帧绑定，以及对每类事件覆盖的明确证据。受控自定义关卡的记录只验证其实际覆盖的机制。

## 2026-11-24 · 非 Frida 逐笔来源筛查

目标是让采集器自己取得逐笔事件，现有 Frida/第三方录制仅用于离线对账。不把操作指令、快照差值或按帧累计曲线当作真实结算事件。

北京时间 01:33，基于工作树基线 `012acf4`，读取本机已经生成的 Unity 普通日志；未启动或附加游戏，未核验这些文件对应的客户端构建，也没有人为划定从开局到终局的样本窗口。脚本只读 `Player.log` 和 `Player-prev.log`，不复制原文；在游戏数据目录顶层也只检查几个已知普通战斗日志名称是否存在，不读取其内容。

| 日志 | 已读字节 | 非空完整行 | 战斗相关词命中 | 结算事件词与带标签数值同现 | 单行 JSON 对象 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `Player.log` | 58,203 | 712 | 109 | 0 | 0 |
| `Player-prev.log` | 23,037 | 449 | 0 | 0 | 0 |

数据目录顶层未见 `BattleLog`、`BattleLog.log` 或 `MultiBattleLog`。对 `Player.log` 的战斗命中做抽查，可见游戏报错调用栈；其中技能词命中 1 行，没有结算量。表中计数用于筛查，不覆盖任意编码、命名与日志格式；结果仅说明这两份样本未找到可直接消费的逐笔记录。随后使用前一份回执核验文件标识及内容前缀，两份日志均无新增完整行；该对照不是新的战斗样本。回执在 `runtime/no-frida-source-audit-20261124-eqrkius6/full-player-logs-v1.json` 和 `append-player-logs-v1.json`。

[鹰角启动器数据面板说明](https://customer-service.hypergryph.com/app/arknights/question/ART178365239398256169747501)公开的是理智、任务进度等部分数据，未说明逐笔战斗接口。[MAA 的关卡结算回调](https://docs.maa.plus/zh-cn/protocol/callback-schema.html)提供关卡、星级和掉落，不提供伤害或治疗事件。两者均未取得可验证的逐笔源样本。

### LS-5 追加窗口

用户报告完成了一场 LS-5。2026-11-24 北京时间 01:33:33 的基线与收到报告后 01:39:50 的回执匹配同一文件标识，原有内容摘要一致。`Player.log` 新增 16,489 字节、241 个非空完整行；战斗相关词命中 80 行，其中 70 行符合 C#/Unity 调用栈形态。未发现带结算量的逐笔候选或单行 JSON 对象，`Player-prev.log` 没有变化。检查时常见战斗日志名称仍未出现。

窗口覆盖用户报告的战斗，但未找到独立的 LS-5 编号或明确的开局、终局标记，客户端构建和治疗覆盖未核验；不能将所有新增内容都归于这局。结论仅为这次普通日志增量不能作为逐笔来源。脱敏回执位于 `runtime/no-frida-source-audit-20261124-eqrkius6/ls5-log-delta-n5xbmraw/`。

### 对一场普通关做增量检查

打开官服客户端、进入关卡前保存基线。保持同一客户端会话，手动完成一场包含伤害和直接治疗的普通关或演习，再保存增量。记录客户端构建、关卡和是否正常结算。不需要启动 ArkDPS 或 Frida。

```powershell
New-Item -ItemType Directory -Path runtime\non-frida-log-check
python acceptance/audit_pc_player_log.py | Out-File -Encoding utf8 runtime\non-frida-log-check\before.json
# 手动完成一场战斗后运行：
python acceptance/audit_pc_player_log.py --baseline runtime\non-frida-log-check\before.json |
    Out-File -Encoding utf8 runtime\non-frida-log-check\after.json
```

只有 `scanned` 才有扫描计数。文件缺失、不稳定、超大小上限或不满足追加条件，都不能记作零条事件。若找到疑似逐笔输出，先对照关卡、帧/序号、来源目标、实际量和终局，再扩展吸盾与技能起止；字段或覆盖不到的机制继续标未知。

### 后续进程内实时来源实机

本节普通日志筛查并非最终数据源。随后在同日新增独立 QuickJS/MinHook 组件，由用户操作官服 PC：
在最初 476 条共同窗口对账、无 Frida 独立 513 条之后，又完成 1480 条完整源前缀同局逐笔对账及盾/元素/嵌套正例，
独立运行补取了真实 3 条销毁尾笔。最后针对性独立局收齐 2406 条源事件并验证 6 组嵌套击杀，仍留下 10 笔非致死元素父证明缺口。
各轮完整原日志、失败回执和来源摘要保留；源事件首尾、独立累计、语义完整性分别登记，没有发布便携包。
设置、启动失败对照和正式回执见[非 Frida 实时采集候选的实机记录](native-pc-native-hooks.md#2026-11-24-官服实机结果)。

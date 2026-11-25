# WPF 桌面端与 Python worker

这是当前桌面入口和分发契约；统计及历史格式仍由 [panel-model](panel-model.md)、[statistics-core](statistics-core.md) 与 [snapshot-adapter](snapshot-adapter.md) 拥有。技术取舍见 [ADR 0003](adr/0003-wpf-python-worker.md)。

采集审计的 `lifecycle_observation` 保留原有 `gap/read_unavailable` 原因，并可附带 `read_error: {type, message}`。该诊断记录被拒采样的具体错误，不构造快照或事件；缺少此字段的旧录制仍可重放。

桌面状态可附带 `sampling_rejection: {code: "mapping_unavailable", reason}`，表示连接仍在运行但当前样本未通过归属校验。它与连接错误分开，绑定当前采集来源；成功样本、实际消费成功的准备/等待/终局观察或来源切换后清除，普通跨帧重试和旧生命周期状态不能清除。主窗、侧栏和 HUD 显示同一原因，保留数据超过原有两秒门槛仍显示过期；该提示不改变统计、事件确认或重连策略。字段缺失或为空表示无此诊断。

调试模式下，自有 PC 观察器通过既有审计流记录 `native_pc_request_timing`。耗时含传输和绑定校验，结束后才写日志。`outcome=success` 表示方法返回。对于 ACK，另记录请求的序号和 `acknowledgement`。`confirmed` 要求回复明确确认同一整数序号，`rejected` 表示返回了错误或不匹配的序号，`unknown` 表示请求抛出异常。旧记录缺少这些字段时，回复证据仍为未知。日志写入失败不改变请求结果。

计时保留 `started_monotonic` 供旧采样关联，另以 `started_perf_counter` 和高精度计数器计算 duration，避免 Windows GetTickCount64 将短请求量化为零。自有 PC 观察器允许对明确且尚未消费的忙帧作有限短重试，包括已绑定来源在 mark 中以正数 active 明确报告的 `CallbackBusyMark`。最多三次 read 起点，额外起点须在首次起点后 40ms 内准入，间隔为可取消的 5ms。预算只限制新的请求起点。普通 `ObserverReadRetry` 不因消息文本而获得这项准入。未知错误、真实读失败、映射失败、来源或观察器变化及已消费路径均恢复原节奏。每次重试仍经过生命周期检查，实机刷新间隔须单独验证。

卫戍按事件统计时，观察者的起止标记分别证明自己的逻辑帧和同一战局归属。MuMu 和 PC 复用 `verified_autochess_event_window`：独立且受守卫的快照须在这段时钟区间内，并与两次标记的进程、战局根、关卡和阶段归属一致。事件批次严格读到结束标记的序号，消费成功后才确认。跨帧时 `autochess_snapshot_anchor` 记录独立快照的帧和时间，显示和回放使用结束标记的事件时钟。此投影只能是 `events_only`，旧累计 rows/global 数值清空，不进入统计或初始点证明。终局、普通关卡和未经认证的阶段继续使用各自原有屏障。

MuMu 的 `guard` 仍属于原快照。PC 的 `native_window_guard` 来自原生探针当次成功读取的前后五根、原始帧/Q32、关卡及两端进程出生核验；采集器以同一份根及 battle serial 形成 session，不读取稍后的缓存补证。共同窗口核验还要求相同构建、出生、世代和交战范围。不能从观察者 mark 反填独立快照 guard，不能把事件结束时钟伪装成该处另读的原生累计。该 PC 路径的实机验收记录见[PC ArkPie 事件后端](native-pc-native-hooks.md#2026-11-24-卫戍实机流畅度验收)。

## 进程与所有权

- `desktop/ArkDPS.Desktop` 是 .NET 10 / C# / WPF 原生 UI：主窗、统计面板、历史页、连接表单、固定干员报告、HUD、托盘与 PNG 均由 WPF 绘制；不是 Tk 换皮，也没有 WebView。
- WPF 启动一个 `desktop_bridge.py` 子进程。只有 Python 可以调用 `ConnectionConfig` / `CollectionSession` / `BattleRuntime` / `BattleHistory`；C# 不读游戏内存，不执行 ADB，不重新计算统计或占比。`DesktopState.view` 只调用既有 `render_panel`。
- GUI 隐藏时两个进程仍运行，HUD 跟随当前局。显式退出发送 `quit`，worker 停止并 join 采集线程、释放自己拥有的观察器及转发，再发 `bye`；UI 等待进程实际退出。stdin EOF 也触发同一清理路径。最终清理在审计流关闭前最多重试一次。若重试仍未能释放资源，则保留所有权并令退出失败。没有按进程名称杀进程，也不因协议错误静默切换到模拟数据。
- 重定位必须先成功释放旧会话；释放异常保留会话供重试。成功重试后的历史清理异常仍保留并令退出结果非零，不能把“当前句柄释放”误报为“本次没有清理失败”。每次 worker 运行以独占方式创建新的采集日志分段。
- Python 保留原有单实例设备通道锁；明确的离线验收不占用该设备通道，必须指定独立配置目录。Tk 面板已封存（[ADR 0005](adr/0005-seal-legacy-tk.md)），`app.py` 只路由到 WPF 或无界面 `--capture-seconds`；`--legacy-tk`、`--acceptance-output`、`--self-test` 等旧参数由 argparse 明确报错退出，不会回退到其他界面。

关卡名称由 Python 统一解析：`stage_names.py` 读取随包的 `vendor/ark-source/game_data/catalogs/stage_names.json`，把原生 `stage_id`（如 `Obt/Main/level_main_01-07`）翻译成玩家可读名称（`1-7`）。实时 `stage`、历史 `labels[0]`、PNG 与导出的 `display.stage` 共用同一解析；原始 `stage_id` 原样保留在历史、`stage_raw`、`stage_detail` 与导出中，UI 不改写它。目录缺失、无法解析或与 manifest `data_version` 不一致时，`warnings`/`history_notice` 明确提示版本问题并退回可读短码（如 `rogue6_1-3`），不显示内部路径，也不影响战绩读取。

`app.py` 是唯一入口：默认调用 `desktop_launcher.launch` 启动 WPF，`--capture-seconds` 走无界面采集；旧 Tk 自检/测量参数已移除。归档前的旧构建与自检契约见 `archive/legacy-tk/`，不继承其真实设备验收结论。

## 发行日志与调试模式

ArkDPS 自己生成的桌面、采集、退出、更新和命令行日志在开头记下诊断上下文。文本日志和桌面采集 JSONL 的首行均为 `log_header` JSON 记录，采集日志轮转后的每个分段都有头部。已有独立采集命令在原有首条元数据内携带同一上下文，旧录制仍可读取。

头部记录实际包版本和构建、操作系统、处理器、内存及可读取的显卡信息，以及连接平台和相关设置开关。游戏和模拟器版本以采集器的实际观测为准。启动时尚未观测到的字段可为空，若已有匹配的观测缓存则标注为上次观测。连接成功时在同一轮日志补记实际环境，后续分段沿用。头部采用字段白名单。

默认只记少量关键事件，包括启动和连接状态、重要观察者阶段、失败、战绩归档结果与关闭。PC 的启动阶段带 UTC 时间，可按游戏进程身份关联环境异常报告。逐次 IPC、战斗帧、事件批次和 PC 请求计时在调试模式下记录。

ArkPie 的首次管道请求失败始终记录 `native_hook_request_failed`，包含 UTC 时间、操作、异常类型和可取得的 Windows 错误码。已识别的 `response_too_large` 还记录响应字节数、通道上限及请求的事件条数。记录发生在观察器转为失败时，后续归档可能晚于这次故障。该记录不包含原始响应或异常正文。

采集停更诊断 `capture_health` 不受调试模式开关影响。有效采样间隔超过原有两秒过期门槛时记录 `stale`，持续停更最多每 30 秒记录一次 `still_stale`，恢复有效样本时记录 `recovered`。字段只保留固定原因代码、原采样时间形成的年龄或间隔、重试次数、当次读取耗时和已验证来源上下文。原因区分回调忙、原生时钟忙、事件屏障忙、映射缺失、读失败及普通重试。该记录不包含原始事件或异常正文，不更新采样时间，不改变过期判断，也不增加观察器请求。已确认的准备、等待、终局或来源变化会结束原采集诊断区间。

从设置页的「诊断工具」切换「调试模式」。切换实时生效并保存到本地。收集详细问题时，在复现前开启，完成后关闭。完整的战局回放需要从采集开始持续开启调试模式。途中切换留下的日志仍可用于定位故障。专用无界面采集和显式验收会按其原有用途记录详细数据。

游戏帧率通过独立的 Python 观察线程采集，`snapshot.game_fps` 只传递已完成的一秒窗口及来源、进程身份、可用状态和原因。帧率计数、定时发布和日志写入均不等待战斗样本或观察器请求成功。WPF 按样本编号更新状态行，长期没有新窗口时清除旧数值。PC 的来源是指定进程中成功且非测试的 DXGI Present，不代表物理屏幕扫描帧率。模拟器来源是 guest 中已验证游戏图层的实际呈现时间，不采用宿主模拟器交换链。缺少可靠时钟、窗口或图层证据时保留未知。

PC 按 QPC 事件时间归入一秒窗口，保留 1.25 秒缓冲交付余量，因此读数带有交付延迟。ETW 丢失、过晚交付或配对损坏会使相关窗口失效。模拟器使用 guest 单调时钟的纳秒窗口，不能将它伪装成精确 UTC。两种来源均记录 `observed_at_utc` 作为主机发布时间，窗口时钟由 `clock_source` 标明。对齐故障时使用窗口时间，并区分发布时间。

`performance/fps/` 每秒保存一条 `game_fps`，包含 UTC、来源、窗口、帧数、FPS 和状态；记录不受调试模式影响。复用采集日志的独占分段、头部和容量管理，单段 4 MiB、目录 32 MiB。离线验收不启动帧率观察器。连接来源变化会使旧窗口失效，退出时先停止帧率观察线程，再关闭其日志，清理完成后才能返回 `bye`。

## 唯一协议：stdio JSON Lines v1

依照 ARK-19 最新修正，当前始终是一对父子进程，因此使用重定向的 stdin/stdout；**不使用 Named Pipes、TCP 或第三方 IPC 依赖**。只有未来有独立 worker 生命周期或多客户端需求时才重新评估 Named Pipes。

每行一个 UTF-8 JSON 对象（无 BOM），命令含整数 `version: 1`、递增 `id` 和 `op`。UI 串行发送请求，按 version/id 核对回复。单条上限 32 MiB。收到完整、序号匹配的 `error` 时，只结束该请求，通道继续可用。

WPF 在后台按字节写入命令，并用独立线程读取回复。ADB 子进程的标准输入连接到空设备，避免 ADB shell 读取桌面命令。worker 在握手中声明 `request_replay`，并缓存最近一条命令的原文和序列化回复。写入成功后等不到回复时，UI 最多用同一编号补发一次。worker 直接返回缓存回复，同一命令只执行一次。UI 会丢弃补发产生的一条完全相同的重复回复。

最终超时、I/O 故障或协议校验失败会触发自动重连。UI 保留最后有效统计和已载入的战绩，先关闭旧 worker 的 stdin 完成清理，再启动新 worker。跨进程恢复不会补发结果未知的旧命令。自动重试用完后，可点击「重连观察者」继续。超时日志记录命令名、编号、读写阶段和字节数，便于与 worker 日志核对。

| op / 响应 kind | 语义 |
| --- | --- |
| `hello` / `hello` | 必须先握手；返回协议、transport、实际 Python 版本/路径和可选 Frida 客户端版本。 |
| `snapshot` / `snapshot` | 约每 200ms 请求。包含状态、战局标识、面板视图、配置、战绩索引和所选战绩的展示数据。 |
| 面板、主题、位置、历史、连接与导出命令 / `state` | Python 校验并持久化，再返回新状态；没有第二套 C# 配置文件或统计源。 |
| 任意失败 / `error` | `ok: false`、可读 error；状态不伪造为成功，UI 保留最后有效值并提示。 |
| `quit` / `bye` | bye 只在采集清理完成后发送；清理失败返回 error，UI 等待退出并显示失败。 |

`snapshot` 的面板视图还包含事件可用性字段（`rate_available`/`rate_reason`/`rate_hint`/`rate_action`/`rate_action_label`）和逐行速率分类（`rate_state`/`rate_text`/`rate_note`）。WPF 只在接敌口径下显示原因与入口；`retry` 调用既有 `rescan`（不重启应用），`settings` 导航到连接设置。字段语义由 [panel-model](panel-model.md#事件不可用时的接敌展示ark-22) 拥有。

`details_by_owner` 使用与榜单行相同的详情投影，以归属干员 ID 索引。干员退出近期榜后仍可在固定详情中查看。历史详情读取选中战绩的完整面板视图。原始战绩由 worker 持有。导出命令使用打开保存窗口时的 `battle_id`，JSON 由 worker 从对应原始战绩生成，PNG 由 WPF 从当时的视图生成。

卫戍协议提供可选的 `multiplayer` 投影，由同一个生命周期持有：只有共享采集器已接受的有界批次才扇出到各玩家统计，不重复读探针或确认游标。PC 卫戍原生本家编队可以为空，经过同帧原生身份接口认证的战斗阶段可用 `events_only` 建立共享时钟与事件接收基线；CID 累计仅保留为诊断，不能回退成为独立玩家累计，也不能因空字典显示完整零值。可选投影失败只冻结该观察范围，不能回滚共享事件、阻止确认或阻断归档。终局事务成功后才提交玩家尾部并冻结速率；原生阶段已变为 END/SETTLE 时保留最后已证战斗范围，仅接收该范围的可证尾部，不建立空的终局范围。四家原始观察结果保存在战绩的 `last_sample.multiplayer`，schema 2 校验真实 UID、角色 owner、scope、占位和冻结时间；旧 schema 1 与缺失该字段的战绩各自保留原语义。

PC 未接入事件观察时，卫戍主面板和 HUD 使用同帧校验通过的原生角色键累计。旧编队表读不到时仍可显示这些数值，界面标注「原生角色累计 · 玩家归属未知」。不同玩家使用同一角色键时客户端合并计数，召唤物保留自己的计数键。角色计数也无法证明本家、队友或全场总量。新战绩按采样片段归档，历史中的旧片段从最后可靠快照读取原生角色行，原始导出保留已有证据。

### PC 卫戍的提交、重试和重置

| 层 | 提交条件 | 重试与重置 |
| --- | --- | --- |
| 原生可选读取 | 敌情、出怪计划和操作记录各占一个可选采样机会，在通过原生校验的快照被取用时提交本次调度。卫戍敌情快照还需通过事件标记绑定当前作用域。 | 未取用或被原生校验拒绝时恢复到期时间，下一次同局先读基础数据。事件标记失败时保留同局仍在有效期内的上一份已绑定敌情。换局后只使用带新作用域的快照。 |
| 采样与作用域 | 原生快照需通过时钟、战局及编队检查。事件边界还要和快照的原生会话、构建与同帧上下文匹配。 | 同帧稳态 `events_only` 可用同一已接受作用域下的结束标记作为有界事件序号。两个标记必须都没有在途回调，且序号及回调世代不回退。新基线、普通累计、阶段切换和终局继续要求严格的前后边界。 |
| 共享统计与四家投影 | 共享核心按序归约敌人入场、状态、复生及已证对敌扣血。玩家核心按玩家和角色 UID 归约自己的事件。同一原始记录有一份属于四家投影的私有回执副本，各玩家仍独立推进序号。 | 每一层校验重传内容。一段事件可以收到序号回执而缺少归属证据，此时相应完整性下降。作用域切换时为新局建立基线，旧局的终局证据只允许补入旧局。 |
| 敌情展示 | 敌人列表、入场容量和对敌扣血按共享世界账本的序号发布。快照只提供已证明的当前成员和 HP。缺少全量证据时，已证子集保留观察值，总量完整性保持未知。 | 恢复更新可以追加同局已证事实。完整性缺口不能由后来一次成功读取补齐。新局重新验证快照作用域、成员与时钟。 |
| 事件 ACK | 共享统计接纳批次后才能提交释放请求。读取起点按主机已接纳序号推进。观察器收到生产者确认后才报告 ACK 成功。 | 确认超时时保留该批已提交统计和待确认游标。同一观察器、同一世代和原生会话才可重试。解绑、换源或换局取消旧待确认。 |
| 战绩与桌面 | 归档写入历史后才通知桌面索引。IPC 按顺序在回复前处理有限数量的通知，每个样本仍更新分析。 | 剩余通知保留给下次命令。数据年龄由原采样时间决定。WPF 的回复等待、采样过期、停止推进和正式断连分别判断。 |

「卫戍队友DPS」仅支持 PC，适用于官服和 B 服。PC 客户端开启多战场视野时，防御页可展示各家已取得的实时输出，准备阶段显示最近一场有效防御结果。MuMu 及其它端的防御只统计本家，在主面板保留本家防御回看。按钮显示昵称和真实玩家 UID，四个 slot 用于排版。主面板、侧栏、HUD 和趋势在防御、首领阶段的 `events_only` 样本中选唯一已证的本家统计。Python 用 `DesktopState.view` 和现有面板配置生成各家的 `view`，WPF 按玩家 UID 选择。首次进入上轮回看时默认选本家，清除旧的固定详情。后续刷新保留手选。头像使用单独的 `portrait_id`。

「统计卫戍队友DPS」首次安装及旧布局默认关闭。`multiplayer_enabled` 保存期望范围并由现有采样请求传给 PC 和 MuMu 生产者。各采集接口验证同帧阶段、轮次、整场标识及账号，主机独立核对战局归属，再把两端的证据归一化为同一场景。生产者在已验证的新卫戍交战范围固定 `collection_mode`，采集、统计和显示沿用该值至小局结束。非 PC 生产者的防御阶段固定为 `local`；协防阶段独立使用 `all`，收集当前已验证协防战场的参与者，防御和首领继续遵循原设置。协防的采集范围不会打开已关闭的四家详情，也不产生设置尚未生效的提示。PC 提供「卫戍队友DPS」实时入口，已确认由 PC 采集的战绩可查看同期队友输出。本家模式只在分配序号前跳过确认与本家和公共敌情都无关的明细。公共世界事件、影响本家的结算和缺少足够排除证据的事件继续进入共享有界批次。统计复用本家账本，四家身份仍用于归属和战局校验，未采集账号保持空值。缺少新契约证据及已声明 `local` 的旧协防回放沿用原先的统计口径；已经绑定的小局遇到临时短读时保留最后有效结果。

防御页的「本家对敌扣血」复用同小局、同采样时间的本家 `enemy_damage` 账本。它统计本家干员已证实造成的敌方 HP 损失。敌人列表保留全场观测范围，页面明确标示。入场人数、入场总血量和剩余血量需要独立的敌人归属证据，缺少证据时保留未知。命中过某个敌人不能证明它属于本家战场。全场敌情事实仍由共享核心保留供回放。

已认证的协防阶段主面板显示「当前协防战场」，联合相同 scope、时间及玩家 session 下已有的逐次统计行。参战干员可能属于被协防的账号，不能按本家账号再过滤一次。每行必须有一致的玩家 UID、角色 UID 和账本 owner 证据。历史和 PNG 使用同一展示投影，趋势按阶段、轮次和 scope 分段。展示副本可从已有逐次账本投影已观察量，原生累计及完整性仍保持未知。

采集线程在同帧阶段证据校验后发出 `autochess_scene`，与样本按同一队列顺序交付。结算批次重试时，这条通知仍可切换协防等待页。协防隐藏四家，保留上轮防御的有效展示数据。准备阶段恢复同场防御，标注回合，并一直保留到下一场本家防御出现第一笔已核实玩家归属的结算。身份基线或重连后暂时空的事件流不能覆盖已有输出。一般读取重试和短时失联保留缓存。确认离场、进程或来源变化，以及已验证的战局身份变化后清空缓存。缓存只影响桌面展示，金额、ACK、原始记录及历史仍由原有链路提交。 MuMu 防御阶段的原始标记可带 `autochess_view_mode`，记录同帧读取的多战场视野和步进模式。只有字段类型和布尔值经校验时才输出，用于区分不同客户端提供的战场范围。

桌面响应的 `multiplayer.players` 仅省略未被 WPF 消费的原始 `statistics` 与 `actor_ledgers`。现有 `view.source`、详情、身份和覆盖字段继续发送；原始样本、审计及历史保留完整账本。生命周期已保留的独立投影可用于下一次展示深拷贝，避免同一批次重复构造；缺失投影时仍重试，返回值不能反向修改内部状态。

玩家 UID 是保留原文的标识字符串，前导零属于身份；去掉零后的拼写是另一账号。采集、历史和桌面共用 `valid_player_uid` 校验有界 ASCII 数字、非全零及既有 uint64 范围，不将 UID 整数化。角色 owner、scope、本家选择及历史读写始终使用同一原始字符串。

可选的 `live_analysis` 提供单个战局采样点、连续段标识、团队指标和各干员近期 DPS。团队近 5 秒 DPS 沿用含过量的近期速率，只有团队归属完整且可靠窗口一致时汇总。本局平均 DPS 使用已确认累计伤害除以游戏时间。观察峰值在本次采集内维护。WPF 按游戏秒保留最近 10 分钟，按连续段断开曲线，换局清空。该字段独立于历史记录和导出格式。

stdout 专用于协议。WPF 保存 worker 的 stderr 至 `runtime/wpf-collector-*.log`（发布包位于 `collector/runtime`），原始采样审计仍在 `runtime/captures`。每次进入历史页可以发送 `history_refresh` 读取磁盘。写入新战绩后，采集会话用已验证的存档结果增量更新桌面索引，保留当前选中副本、详情和筛选。实时通知按 FIFO 处理，每条新命令最多接纳 32 项，25ms 后停止接纳下一项，已开始的处理继续完成。尚未处理的通知留到后续命令，同编号请求重放原有回复。一次性 CLI 连接覆盖在本次重定位中保留，显式应用设置才改长期偏好。`connection_apply` 先保存候选布局，再保存连接配置。配置写入失败时尝试恢复原布局并报告回滚失败。两份文件分别原子替换。

PC 复用同一 worker、采集会话和战局归档。连接配置 v1 增加 `platform`（缺省 `mumu`）和 `native_game_root`。`connection_config` 保留已保存设置，`effective_connection_config` 表示本次启动实际选择。`--platform` 和 `--native-game-root` 覆盖本次启动，显式 Apply 才写入设置。PC 通过 Windows 读权限、三个文件哈希和模块字节校验后发布同帧累计与编队。启用观察时由 `NativePcFridaRuntime` 先监听新进程并载入官方 Gadget，经本机认证连接独立 PC 探针。连接仍等待类及静态数据初始化，标记和批次必须通过同战局、同帧验证。`--no-events` 关闭事件及启动监听。2026-11-22 已完成 CE-5 逐次结算对账及普通 worker 的探针重连验证。[实机验证状态](event-capture.md#arkdps-本机-frida-探针)

PC 启动运行时由 CLI 或 `CollectionWorker` 持有，跨越 `CollectionSession` 的连接重试；观察者只持有自己的脚本和会话。独立持有者隔离了组件的进程生命周期与脚本重连：将启动监听放进采样器会等待 `GameAssembly` 后才开始，将它放进观察者则要等采集器连接完成，两者都会错过已实测的早期加载窗口。初始化阶段不访问快照，直到启动组件就绪或明确失败。可选事件失败保留快照能力，`--require-events` 在就绪前失败。关闭时先释放脚本与会话，再停止并 join 启动监听；清理异常保留所有权及历史失败。Gadget 已载入时随游戏保留，凭据和租约留在可写 runtime，重连核对 PID、创建时间、构建、模块路径、组件及配置摘要。

新战绩的 `metadata.platform` 与 `metadata.source_build` 来自该局的采集器。PC 保存构建指纹。ADB 采集在 Android 客户端版本校验通过后保存来源和客户端版本，历史显示「Android（ADB）」。切换连接不会改写历史，缺少来源的旧战绩会显示「来源未记录」。

连接请求带版本号。自动重连仅重试仍有效的选择，新的显式应用会使排队中的旧请求失效。切换平台时清除上一平台的实时战况，待采集线程确认最新请求后才接收实时数据。历史归档通知照常处理。设置页的 MuMu 实例检测与发出时的平台和 ADB 路径绑定。平台、ADB 路径或已应用的连接设置变化后，旧检测结果失效。

HUD 在定位、重连、数据过期、战局冻结或等待新局时共享主窗的状态提示。保留旧战况时提示「上次快照」。实时统计恢复后收起提示条，正常排行高度与点击穿透按原设置运行。

桌面采集日志达到 64 MiB 后从下一条完整记录开始新分段。启动和写入需要空间时，按最后写入时间从旧到新清理自动生成的 `wpf-*.jsonl`，将日志总量限制为 512 MiB（含旧版单文件和当前分段）。占用中的文件会跳过。无法腾出空间或无法写入时，桌面 worker 发出日志告警并稍后重试，采集和战绩归档照常执行。手动采集的证据包和 `runtime/history` 使用独立的路径。

`python capture_battles.py --replay <任一分段>` 按顺序读取同一次运行的分段。文件链完整时由原有对账器复算。分段缺失时输出 `coverage: retained_only` 和仍保留的最新发布统计，供诊断使用。每段的首条敌人状态增量都保存可独立解码的前缀。

## 同包分发

长期目标为 `net10.0-windows` / .NET 10 LTS（官方支持至 2028-11-14），不以 2026-11-10 结束支持的 .NET 8 作为长期基线。支持周期依据 [Microsoft 官方策略](https://dotnet.microsoft.com/en-us/platform/support/policy)。`global.json` 要求 10.0 稳定 SDK 并允许同系列最新 feature band；本轮全局 SDK 仅到 8.0.423，因此使用 SHA-512 核对过的任务本地 SDK 10.0.403 / runtime 10.0.14 构建，没有改写全局 SDK。可用 `ARKDPS_DOTNET` 显式指定 SDK 的 dotnet.exe；不兼容工具链构建失败，不静默降回 net8。

从源码双击 `start.cmd`，由 `desktop_launcher.py` 增量构建并启动 WPF；源码构建需要 .NET 10 SDK 和现有 Python。发布用户不应依赖这两项机器级安装：

```powershell
python tools/package_windows.py --output runtime/packages
```

发布构建要求源码已提交且工作区干净，避免把未提交内容冒充 manifest 中的候选。构建的是一个可解压运行的 Windows x64 ZIP 包（不是 MSI 安装器），包含：

- self-contained .NET/WPF 程序；
- 固定 CPython 3.12.10 embeddable x64 运行时及其许可；
- 同一次构建的 Python collector 源码、采集必需 vendor 子集、离线头像；
- 标准事件版包含固定版本的 Windows 客户端、Windows x64 观察者组件、MuMu 12 的 Android x86_64 观察者服务及对应许可。选择 MuMu 并启用观察者后，程序为该实例准备服务。模拟器内 Root 和 ADB 由用户配置。构建选项 `--without-events` 生成累计快照版。

Python 和 wheel 下载分别用固定 SHA-256 核验，下载与展开位于新的隔离目录；ZIP 拒绝路径穿越/符号链接/超大展开。Python `._pth` 仅白名单包内目录，不读系统 Python、用户 site-packages 或 `PYTHONPATH`。没有全局 pip install。

`metadata/release.json` 登记发行版本、载荷文件摘要、源提交和协议。启动时核对包内 worker 摘要及协议，然后握手验证实际响应版本。支持应用内更新的标准便携版将校验完成的完整程序保存于 `.arkdps-update/versions/`。用户确认重启后，旧版先退出采集器并保存设置，原启动入口切换到新版 WPF 及与它匹配的 worker。两个版本共用原启动目录的 `collector/runtime/`。首次从旧版迁移时可通过手动解压并复制该目录保留设置和战绩。便携包需要解压到可写目录。新版桌面入口要求 Windows 管理员权限。尚未要求管理员权限的旧根启动器不能可靠启动新版，本次必须完整 ZIP 迁移一次并使用新目录入口；单次管理员运行旧版完成更新不足以修复后续启动。迁移时，退出旧版后将现有 `collector/runtime/` 整目录复制到已解压的新版中。

标准事件版还包含 ArkPie PC 组件 `ArkDPS.NativeEvents.dll`。包级 `native_pc_native_hooks` 清单记录其构建、
依赖和许可摘要。WPF 与 Python 在加载前核验这些文件，设置页可切换 Frida 与 ArkPie 后端，
启动提示从各后端真实的监听状态投影。ArkPie 后端沿用标准包的更新和状态迁移流程。
实机验收与构建规则见[PC ArkPie 事件后端](windows-release.md#pc-ArkPie 事件后端)。

## 可复核验收

运行前按 [开发代理指南](../AGENTS.md#运行与验收) 选择检查范围。交互式验收的执行条件和命令见 [Windows 验收](windows-release.md#验收)。

测试依赖由根目录 `requirements-test.txt` 固定，包含用于独立解码验证 PNG 的 Pillow；它不进入 Python worker 运行时。WPF 统计 PNG 由 .NET `PngBitmapEncoder` 生成；不能因测试导入 `PIL` 就把 Pillow 当作产品运行时依赖，也不能漏装测试依赖或跳过 PNG 断言。

```powershell
python -m pip install -r requirements-test.txt
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests/test_desktop_bridge.py tests/test_wpf_contract.py -q --assert=plain
```

单包脚本还在新目录验证清洁解压、全部文件摘要、缺少全局 Python/.NET 的运行、v1→v3 设置迁移以及 WPF 重启恢复；这只是便携 ZIP 的离线升级验收，不冒充 MSI/注册表安装或游戏验收。

每次脚本新建独立 state 目录，不覆盖产品配置；真实运行 WPF、真实 stdio 握手、切换双皮肤、保存主窗/HUD/历史/设置/完整排行 PNG，并在新进程恢复配置。对私有下层测试窗口发送 Win32 鼠标输入，核对 HUD → 下层 → HUD。包验收还断言 worker 实际使用包内 Python 3.12.10 / Frida 17.26.1；构建 smoke 将系统 Python 环境变量设为无效值，验证不依赖宿主 Python。

**这些都是明确标注的合成数据。** 私有窗口接收点击不是 MuMu/游戏接收点击；物理托盘图标不可定位时回执明确 `NOT VERIFIED`，不将原生回调或 `Show()` 冒充物理双击。实际游戏点击穿透、物理托盘恢复与真实换局已在候选 `f7d59d9` 上完成实机验证（见下节），但混合 DPI 与退出/重启仍未收口；不得把本轮离线回执或旧候选证据当作这些实机项的通过证据。独立评审及这些人工项收口前，不宣称整票/首版发布验收完成。


## 本轮离线验收结果（2026-11-10）

- 完整 Python 回归实际运行 630 项通过；WPF 专项覆盖 worker 无 Tk 导入、统一统计输入、未知值、排行/速率正交、历史淘汰/换局隔离、版本拒绝、退出清理、旧配置迁移、包解压安全和实际双主题文字对比度。严格整数协议版本的回归曾实际失败（布尔 `true` 被 Python 当作 1），修正后通过。
- 使用本地 SDK 10.0.403 实际构建 `net10.0-windows`，0 warning / 0 error；源版原生 WPF 验收通过，包含真实错误版本 worker 拒绝、握手后模拟 worker 崩溃与非零退出可观察、卡住响应的 UI 过期提示、双皮肤 HWND/位置/尺寸/透明度/锁定不变、私有窗口点击路由、合成换局及新进程配置恢复。
- 自包含发布及带 Python 3.12.10 / Frida 17.26.1 的单包已实际构建和离线清洁解压运行，完成 v1 配置升级（当时仍含 Tk 回退步骤，该入口已于 ARK-27 封存）。精确候选、包摘要和截图摘要随构建回执交付。
- 继承的 V1 红/蓝/紫/白语义色未通过通用分类色校验（蓝/紫的 CVD/正常视觉距离及白色 lightness/chroma）；不擅自改写固定语义色，也不声称色盲安全，提供文字图例和精确明细。
- 实机项已不再等待设备交接：候选 `f7d59d9` 已在 MuMu（真实 collector、96 DPI、主屏 1920×1080 + 负坐标副屏）完成穿透点击、物理托盘双击、真实换局/历史隔离、双皮肤切换与导出；`f7d59d9` 上失败的退出/重启在修复候选 `0ca9f18` 上通过。混合 DPI（125/150/200%）仍无硬件可测。

## PR #21 当前候选离线复验（2026-11-10）

- 通过 `ARKDPS_DOTNET` 指向任务本地 .NET SDK 10.0.403，`net10.0-windows` 构建成功，0 warning / 0 error。
- `python acceptance/verify_wpf.py --output <新建隔离目录>` 生成 `PASS_OFFLINE_WPF` 回执；源版 WPF 首次启动与重启恢复均通过。回执包含 stdio JSONL 错误边界、两个主题、合成历史/HUD、私有 HWND 点击路由及截图 SHA-256。
- 首次运行暴露的是验收夹具错误：fake worker 对第二个 `snapshot` 请求返回了默认 `kind=state`；将夹具响应改为 `kind=snapshot` 后完整离线验收通过。该次失败不是产品 WPF/worker 故障。
- 此验收只使用合成数据和模拟 worker；没有访问 MuMu、启动 collector 或证明游戏点击穿透。物理托盘双击在离线回执中仍为 `NOT VERIFIED`，因为离线验收无法定位真实 Shell 通知图标。

## ARK-23 历史页自动刷新离线复验（2026-11-10）

- 进入历史页自动发送 `history_refresh`；采集会话归档成功后经 worker 事件刷新 `records`，打开的历史页无需点「刷新」。
  自动刷新保留当前选中副本、详情和搜索/结果/模式筛选；没有可读记录时回到空态，手动刷新仍可用。
- 完整 Python 回归实际运行 683 项通过，新增用例覆盖：只有新落盘记录才通知且重复交接/写入失败不通知、worker 事件到 `snapshot` 的刷新、页面进入自动刷新的 WPF 契约。归档通知优先于诊断写入，日志失败不会隐藏已落盘的新局；通知用例在改动前的基线上 4 项全部失败。
- `python acceptance/verify_wpf.py --output <新建隔离目录>` 在首次启动与重启阶段均生成 `PASS_OFFLINE_WPF`；回执包含「进入历史页自动读取归档」与「归档后自动更新且保留选中/筛选」两项 UI 级检查。两项检查都用真实 `BattleRecord` 文件：进入页面检查在运行中落盘一条新记录后进入页面；归档更新检查由验收 worker 经真实 `BattleHistory.save` 写入并推送 `history` 事件，再经真实 `DesktopState`/`serve`/poll 链路送达 WPF，不是直接改写快照状态。
- 其中一次重启阶段在既有私有窗口点击路由检查处失败（`locked HUD point is not private underlay`），重新运行同一验收后完整通过；该检查与历史刷新无关，属于本机窗口层叠波动，未据以宣称通过。验收仍只使用合成数据，没有访问 MuMu 或游戏进程。

## 实机验收状态（候选 f7d59d9，2026-11-10）

同一 MuMu 实例串行执行，单实例（WPF PID 3252、HUD HWND 460528），证据为 `ARK-19-f7d59d9-live-evidence.zip`。以下“通过”均指实机回执，不是离线合成数据。

| 实机项 | 结果 | 依据 |
|---|---|---|
| 游戏实际接收锁定 HUD 穿透点击 | **PASS** | `real-click-receipt.json`：`WindowFromPoint` 命中游戏渲染子窗；点击后帧 1384→1430，第二次点击使游戏进入 PAUSE |
| Windows 通知区物理托盘双击恢复 | **PASS** | `real-tray-receipt.json`：主窗隐藏时采集继续（帧 1479→1537），原生双击真实 Shell 图标后恢复，解锁后 HUD 恢复命中 |
| 真实换局 + 历史/HUD 隔离 | **PASS** | `live-history-isolation-receipt.json`：第二局开始后 HUD 跟随新局，正在查看的第一局战绩与归档未被替换 |
| 实机双皮肤切换、历史 JSON 与完整排行 PNG 导出 | **PASS** | `08-lone-trail-live-*.png`、`11-original-history-live-*.png`、`real-export-receipt.json` |
| 正常退出 / 主题重启恢复 | **f7d59d9 未通过；0ca9f18 通过** | 见“退出有界性修复”与“修复后实机复验” |
| 混合 DPI（125/150/200%） | **未覆盖** | 仅有 96 DPI 硬件 |

退出失败的实际记录：20:46:12.81 点击「退出」，`layout.json` 已写盘；worker 审计在同一秒内写入 `{"kind":"stopped","data":{}}` 且无 `cleanup_errors`，说明采集清理已完成，问题在 WPF 退出路径。该路径有两处可复现的有界性缺陷：`BridgeClient.DisposeAsync` 用 `Process.WaitForExitAsync()` 等待 worker，它同时等待被重定向的 stderr 管道 EOF，实测中存活孙进程持有该继承句柄时该等待永不返回（已用模拟 worker 在离线验收中稳定复现）；清理失败提示又是模态 `MessageBox`，会直接阻塞 `Application.Shutdown`。修复后退出改为有界等待（quit 5 秒、进程退出 10 秒），超时写诊断并仅结束本进程持有的子进程句柄（不按进程名清理），退出期间不再弹模态框。

## 退出有界性修复（候选 0ca9f18）

- `BridgeClient.DisposeAsync` 不再使用 `Process.WaitForExitAsync()`：该 API 同时等待重定向 stderr 的 EOF，存活孙进程持有继承句柄时会永久阻塞。改为有界等待进程本身退出（quit 5 秒、退出 10 秒）；超时写 `[exit]` 诊断到 worker 日志，只 `Kill()` 本进程持有的子进程句柄（不按名称清理），随后仍继续关闭。
- `MainWindow.Quit` 以 20 秒看门狗兜底，清理失败只写 `runtime/wpf-quit.log`，退出期间不再弹模态 `MessageBox`。
- 离线验收新增两条回归并通过：`worker that ignores quit is ended by handle inside the bounded exit window and logged`、`exit follows the worker process, not EOF on a pipe held by a surviving grandchild`；`tests/test_wpf_contract.py` 同时锁定“不得使用无界 `WaitForExitAsync()`、Quit 路径不得出现 MessageBox”。
- 实际运行：`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests -q --assert=plain` **679 passed**；`acceptance/verify_wpf.py` 生成 `PASS_OFFLINE_WPF`（首次启动 + 新进程重启恢复）；`dotnet publish -r win-x64 --self-contained` 成功，0 warning / 0 error。

### 修复后实机复验（候选 0ca9f18）

同一 MuMu 实例、同一真实 collector（`--adb` 指向 MuMu adb、`--serial 127.0.0.1:16384`、`--frida-remote 127.0.0.1:27043`），状态页显示 `已连接 127.0.0.1:16384 · 游戏 2.7.91`。

- **正常退出**：点击主窗「退出」后进程 250 ms 结束（重启后的第二次为 281 ms），退出码 0；worker 审计最后一条为 `{"kind":"stopped","data":{}}`，无 `cleanup_errors`；退出后无 `desktop_bridge.py` 进程、27380 端口无监听；`runtime/wpf-quit.log` 未生成（无清理失败）。
- **主题重启恢复**：先在 `孤星 · LONE TRAIL` 会话切到「原始界面」并退出；新进程从同一 `layout.json` 启动后皮肤为「原始界面」、HUD 物理几何 `[1220,200,455,485]`、透明度 `0.83`、扩展样式 `0x8080128`（`WS_EX_TRANSPARENT|WS_EX_NOACTIVATE`，锁定）全部一致。
- **边界**：本轮退出/重启复验时的 collector 处于“已连接、等待战斗数据”状态，没有进行中的战斗；`f7d59d9` 失败那次的 worker 审计显示清理已完成，退出等待与战斗内采集数据无关。战斗中的退出未在本轮复测。

## ARK-27 Tk 封存离线复验（2026-11-11）

- `python -m pytest tests -q` → **625 passed**。封存前基线为 683 项；移除的 60 项全部是 Tk 窗口/托盘/设置对话框用例，
  新增 `tests/test_no_legacy_tk.py`（导入守卫、`archive/` 不被收集、打包白名单、移除参数退出码）。
- `python app.py --legacy-tk` / `--self-test` / `--acceptance-output` → 退出码 2，错误文案指向
  `acceptance/verify_wpf.py`；`python -c "import app"` 后 `tkinter` 不在 `sys.modules`。
- `python acceptance/verify_wpf.py --output runtime/wpf-source` → `PASS_OFFLINE_WPF`。
- `python tools/package_windows.py --output runtime/packages` → 构建成功；ZIP 内 627 个文件，无
  `tkinter` / Tcl / Tk 文件、无 `ArkDPS.exe`，内嵌 Python 冒烟断言 `"tkinter" not in sys.modules` 通过。
- `python acceptance/verify_wpf_package.py <zip>` → `PASS_OFFLINE_PACKAGE`。首次运行在本机窗口层叠下
  HUD 输入路由检查失败一次（`unlocked HUD input route`，与本节前述已知波动同源），重跑完整通过；
  该检查与 Tk 封存无关，未跳过或放宽断言。

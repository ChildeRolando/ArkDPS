# 实时事件采集入口与验收

当前 WPF 桌面由 `desktop_bridge.py` 子进程消费同一 `CollectionSession`，不修改本页采集语义；UI/worker 协议与分发见 [桌面运行时](desktop-runtime.md)。以下 Tk 入口和旧候选实机记录为历史证据，不自动认证新的 WPF。

当前主线 ARK-5 已在 `af18904ab89b417c87665f9dbfa731e2348cfa7a` 获独立 PASS，并合入 `b999a4c9a236331b95a07cfa0e90e8dd97e9d6be`。234 项回归及默认入口、启动失败降级、显式关闭的新实机证据通过。宿主机已在 2026-11-07 22:37:54Z 结束设备操作；下文早期“待返修/独占”是历史过程，当前串行占用以最新讨论为准。

ARK-6 在此基线上整合跨局所有权：GUI 和命令行启用生命周期模式，`BattleRuntime` 分配 ID，快照和 `LiveEventCapture.ingest()` 共用一个适配器。跨帧重试保留队列；新局重建基线并隔离旧事件；同进程不因换局重挂载。首个终局先处理可靠水位再冻结，累计修订与一次归档沿用同局 ID。新日志的 `lifecycle_observation.events` 保留屏障/drain，`published.native_rows` 保留原生来源，`rows` 为窗口投影；独立对账器兼容两种日志。新候选的跨局实机尚未验收，范围和当前 MAA 错误证据见 [生命周期交接](snapshot-adapter.md)。

`app.py` 的窗口、`start.cmd` 和 `--capture-seconds` 命令行模式共用 `CollectionSession`。通过进程身份和游戏版本校验后，默认向本机已验证端点 `127.0.0.1:27043` 尝试临时事件观测；`--frida-remote host:port` 可覆盖端点，`--no-events` 显式关闭。API 的默认值同样为该端点，传入 `frida_remote=None` 关闭，可供 ARK-11 设置入口使用。Frida 17.26.1 客户端、同版服务及端口转发须由调用方准备；采集器结束时卸载自己的脚本，不安装或启动服务、不使用 eternalize。V2 计划在用户显式 root 授权后随包分发并管理同版设备端 server；V1 的上述边界不变，见 [ADR 0004](adr/0004-v2-root-opt-in-frida-server.md)。

连接先校验进程与游戏版本，再准备观察器，最后定位战局。因此观察器可以在进入关卡前就绪。`Collector.connect()` 的默认行为保持同时定位；`connect(locate=False)` 允许提前挂载，之后调用 `locate_battle()`。

快照与编队暂时跨帧、`SampleUnavailable` 或 `RosterUnavailable` 会重试，保留观察器队列，不制造事件缺口。基线要求前后标记同帧同时间、序号及回调入口计数一致，并且没有在途回调。后续同样取得稳定配对，再提交有序事件和水位。真实换局/时钟回退拒绝串局；观察器丢失、来源冲突或序列缺口封存事件覆盖，正常采集入口仍可继续原生累计。未知护盾机制关闭覆盖。自然结束只有最终水位已到结束时间时才保持覆盖完整，接敌尾部按结束时间截断。

输出中的 `statistics` 是统计核心与宿主归并结果；原来的原生 `rows` 仍供现有伤害窗口使用。`event_status` 为 `unavailable`（用户关闭）、`attaching`、`ready`、`recording`、`failed`（启动失败）、`gap`（运行中断）、`finished` 或 `stopped`；`event_error`、`event_watermark` 与每行覆盖字段必须一起保留。依赖缺失、Frida 版本不符、远端不可达或挂载失败时，清理已创建的观察器，保留失败原因，继续原生伤害和治疗快照，护盾与接敌时间保持未知；运行中断只保留已观察的可靠前缀。不会在当前局重挂载，下一局新建会话时按认证条件重新尝试。未经认证的游戏版本或无法确认的进程身份仍拒绝整个连接，不发布数值。新字段的显示界面由后续 GUI/HUD 工单负责。

普通命令行采集允许上述降级，`READY.json` 在连接与可选观察器启动/清理完成后写入，并包含 `event_status` 和 `event_error`。验收使用 `--require-events` 要求事件覆盖，启动失败不写 READY，运行缺口记录失败并清理退出；该选项不能与 `--no-events` 合用。`acceptance/run_event_acceptance.ps1` 已固定传入此选项，避免把快照降级误记为事件验收通过。

## 宿主机实机操作

设备由当前取得窗口的执行角色串行操作，开测前核对兄弟运行和宿主机最新占用。先按[实验手册](experiment-playbook.md)核对关卡、零理智、实验编队和部署；保留 MAA 回调，禁止理智药、源石和养成。采集命令不导航或部署。

用 `acceptance/prepare_event_job.py <原作业> <新作业>` 派生固定作业：在原部署序列之前增加暂停、45 秒主机等待和恢复。这样首次定位扫描及编队快照有时间建立基线；游戏时间保持冻结。保留原作业和派生作业，不能将等待时间当作游戏时间。OF-8 使用 `of8-shield-break.json`，OF-2 使用纠正山技能策略的 `of2-summon-shield.json`，山 S2 仍只使用一次。

在仓库根目录对精确、干净提交运行以下命令，将占位值替换为候选 SHA 和本次路径：

```powershell
& C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -File acceptance/run_event_acceptance.ps1 -Head <SHA> -OutputDirectory <本次新目录> -Stage OF-8 -MaaJob <派生作业> -MaaLog <本次MAA回调.jsonl> -StopFile <本次停止文件>
```

上述命令不传 `--frida-remote`，使用正式默认端口；只有需要覆盖时才传 `-FridaRemote`。必须等 `READY.json` 出现再由 MAA 进入战斗。首次有效配对在控制台输出 `BASELINE`；确认暂停窗口内已产生第一条 `published` 记录后继续固定部署。`-Seconds`（默认 180）从有效基线之后开始计墙钟时间，定位另有 240 秒上限，基线有 90 秒上限。作业最后暂停且回调完成后，协调者可创建指定 StopFile，采集会在本轮前台调用中收尾。运行前不能已有停止文件。每次使用新的产物目录，失败证据也保留。

产物包含 `capture.jsonl`（原始快照、编队、屏障、drain、发布结果和错误）、`READY.json`、`summary.json`、原始 MAA 作业副本、指定的 MAA 回调副本及 `sha256.json`。`summary.error=null` 和 `--require-progress` 通过只证明采集命令完成；还须按票要求对账两组新样本的实际吸收、部分盾破、零吸收、来源/宿主与原生累计。历史数据只用于回归，不能替代新实机证据。

原候选 `1fc1c5cea400b4fc8ffc006358c31c1f7c936253` 的两组新实机证据已完成独立对账，见下文；默认启用和启动失败降级的返修另由确定性测试覆盖，尚未在返修提交上重采实机。宿主机独占设备的分工保持不变。

宿主机已在精确候选 `e58153c5165b6fbeed02794cb50c8893325bbf3a` 跑通 OF-8 导航、暂停基线和部署，报告采到 42 条伤害、8 条治疗和 7 条护盾事件，之后 mark RPC 恰好跨逻辑帧而被当作断流。该轮不是完整验收通过。原始产物位于本机 Review Loop `state/review-queue/evidence/ark-5-live-work/host-acceptance/of8-e58153c-01/product/`。窄修复已纳入作者工作树：RPC 跨帧返回 retry 标记，Python 保留队列重试；回调内读时钟失败、非法时钟及真实 error/stopped 仍关闭覆盖。宿主机报告窄修复的 207 项测试和 JS 语法检查通过，正在独立候选 `aef605125f6f02e9fe09ed3404e3f46249769f1c` 上复跑；正式作者候选的最新修改和最终两组验收仍待完成。

宿主机报告旧 PID 2798 第一次挂载与卸载有成功记录，紧接着第二次挂载于 05:46:17.300 发生 SIGSEGV / SEGV_MAPERR，fault addr `0x10`，线程 `frida-agent-emu`（tid 27666）断连并崩溃；日志为同目录下 `of8-4e97b6c-02/android-crash-buffer.txt`。新 PID 27959 和 32573 各自首次挂载均能就绪。尚未确认根因或证明重复挂载安全。后续每轮实机验收由宿主机准备新的游戏进程；用 `READY.json`、`summary.json` 的 PID 与进程实例核对，保留此生命周期限制。采集器本身不重启游戏。

新采集结束后，`python acceptance/verify_live_events.py <产物目录> --source-owner char_147_shining=char_147_shining --require-partial` 可独立核对闪灵的观察事件前缀、实际吸收证明与发布结果。凯瑟琳组使用 `--source-owner token_10041_cathy_catsld=char_4162_cathy --require-partial --require-zero`。校验器只承认该次基线之后、最后水位之前的连续事件，重复内容只计一次，缺失或冲突拒绝；检查动态盾量减少、来袭伤害、部分破盾和零吸收，并对账有完整原生快照的累计。它不能替代观察器回调完整性和 MAA 实际部署的实机审查。

失败采集默认拒绝。仅诊断已封存的可靠前缀时可加 `--allow-incomplete-prefix`；报告保留采集错误并明确 `complete=false`，即使数值对账成功也不能算完整实机验收。
## ARK-13：下一局重新尝试事件观察

`CollectionSession` 仍是临时观察器的生命周期所有者。连接时尝试一次，首次观察到的战局沿用该结果；本局失败后继续累计快照，直到 `BattleFeed` 通过可靠配对确认新的战局代次，才允许再尝试一次。显式关闭事件时不尝试；健康观察器继续跨局复用。启动失败、运行缺口，以及第一次在新局配对中发现观察器已失效，均遵守同一边界。重复轮询和跨帧重试不增加挂载次数。

`BattleRuntime.poll()` 在代次确认后让现有会话准备观察器；重试期间先接纳已确认的新局原生快照、交接旧局归档，再取得新观察器的同帧屏障。新事件水位只从该屏障开始；即使屏障暂时繁忙，也保留首次原生累计和一次归档，不重新挂载、不回填旧事件。重试失败仍保留快照，下一代次才再试。日志中的 `observer_retry` 记录代次、前状态和新状态；重放仍只从原始生命周期观察重建统计。

`tests/test_event_retry.py` 覆盖这些边界。最初五项复现于 `6d58b72` 实际失败，两项健康/关闭对照通过。确定性回归与新的实机恢复证据分开登记；旧候选的断开、倍速与护盾实机记录不改写成返修候选的实测结果。此前同 PID 重复挂载的崩溃风险仍按上文记录保留，不据此承诺全部环境安全。

## 结算帧的发布与修订

事件时间线在可靠的结束水位处关闭。此后只接纳同一战斗、同一结束帧、同一游戏时间且状态为 3 的结算累计快照；晚到的原生累计归属原局，不创建新战斗，也不延长事件覆盖或参战区间。不同会话或回退的累计仍是错误。

`snapshot_revision` 标识累计快照修订。每次发布必须同时包含当前原生累计和用该累计更新的统计结果，不能把新原生值与旧面板拼成一次发布。重复的稳定结算快照不增加修订号。发生修订的来源没有可用的修订发生时间，其近期速率置为未知；未修订来源保留已有历史。事件序号、水位、结束时间和已观测事件累计保持冻结。原生累计变化不自动证明事件覆盖完整，离线验收仍须独立比较原生增量与事件和。

宿主机反馈的 OF-8 候选 `aef605125f6f02e9fe09ed3404e3f46249769f1c` 已从基线第 20 帧运行至第 2964 帧、序号 120，跨帧 RPC 读取重试有效。闪灵归属 `char_147_shining`，事件 95、100 均为盾值 316.5、入射 372、剩余 0 的部分破盾，闪灵吸收累计 753.5000018。MAA 在最后暂停动作前自然结算，因此不能把该轮记作暂停成功。该轮同时暴露煌在第 2964 帧的原生累计从 28985.8828125 补齐至 31899.103515625，而面板仍冻结旧值的问题；上述发布语义及回归针对这一缺陷。此处为宿主机报告，尚待在修复候选上重新验证及独立原始日志对账，不能作为整票验收通过结论。OF-2 验收另行进行。

## 已完成的原候选实机证据

宿主机随后在 `1fc1c5cea400b4fc8ffc006358c31c1f7c936253` 使用 MuMu 12、官服 Android 2.7.91、MAA 6.21.4、Frida 17.26.1，分别以新游戏进程完成两组采集。产物位于本机 Review Loop `state/review-queue/evidence/ark-5-live-work/host-acceptance/` 的 `of8-1fc1c5c-01` 与 `of2-1fc1c5c-01`。每组保留原始事件、快照、逻辑帧、水位、作业、MAA 回调、SHA-256 清单与 `reconciliation.json`。独立评审已核对这两组文件哈希及对账结果。

- OF-8：事件 1–123，结束帧 2964，闪灵 S2 吸收 753.500002，部分破盾序号 95、100。煌原生伤害增量 31899.103516，事件增量 31899.100678；晚到结算的原生值与统计发布一致。
- OF-2：事件 1–92，凯瑟琳装置吸收 155.779993，归宿主 `char_4162_cathy`，部分破盾序号 51，11 条零吸收回调不增加累计。

复核命令分别为 `python acceptance/verify_live_events.py <OF-8目录>/product --source-owner char_147_shining=char_147_shining --require-partial` 和 `python acceptance/verify_live_events.py <OF-2目录>/product --source-owner token_10041_cathy_catsld=char_4162_cathy --require-partial --require-zero`。这两组来自显式指定端点的原候选，不能外推为返修提交的默认启动或缺依赖实机验证。`tests/test_collection_session.py` 覆盖默认启用、关闭、认证拒绝、启动失败清理后继续快照与运行中断；`tests/test_capture_cli.py` 覆盖正式参数默认值及普通/严格验收模式的降级行为。

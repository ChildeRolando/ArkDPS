# 官服 PC 非 Frida 实时采集候选

## 当前结论

**便携启动测试候选 rc.2026112401 已暂停分发。** 同机实际启动在观察者 READY 前发生加载确认失败，
截图报告线程终止值 `0xffffffff`；rc.2026112401 当时的默认日志不足以区分进程退出、未找到映射及模块返回值不匹配。
后续 rc.2026112402 的一次真实诊断启动已确认：验证时进程仍活、模块查询成功，但没有找到组件映射；仍未定位其原因。
rc.2026112403 校验前置后首次启动成功，但同一运行内第二次重启再次失败；当前仍不具备稳定启动或分发资格。
rc.2026112404 随后的两次真实启动与握手均成功，旧实例清理后建立了新fence；用户又报告卫戍持续等待可靠采样。
启动恢复与卫戍采样活性、正常终局、性能必须分别验收，仍不放行分发。
此前离线检查及包摘要仍有效，但不能当作实机启动成功。原 ZIP 保留作证据，不反复加载或无证归因安全保护。

本候选新增真正的进程内采集组件，不再要求外部程序先提供事件 JSONL。
它不是纯 C++ 重写：现有三段探针的元数据核验、结算、归属、技能和敌情逻辑原样运行在 QuickJS，
自研 C++ 适配层用 MinHook 观察原生调用，并提供有界内存读取、调用上下文与命名管道。
运行该来源不导入 Frida Python 包、不加载 Frida Gadget，也不依赖 ArkFramework。
对账的另一侧仍需要现有 Frida 探针，这是独立对照，不是新来源的依赖。

**2026-11-24 已完成官服实机采集和同局逐笔对账，但不是全机制完整验收。** 用户操作客户端，
最初的 476 条共同窗口对账、独立局 513 条采集之后，追加完成了 1480 条同局完整源前缀对账，
正吸盾、元素爆发和一般嵌套结算均取得相同实机证明；再以无 Frida 解释器独立取得 1519 条完整源事件，
其中真实补取了 3 条销毁尾笔并确认 ACK。随后独立针对性局完整收齐 2406 条源事件，取得 6 组父 HP 结算后子元素击杀证明；
当时另有 10 笔非致死元素子结算的父证明被共同运行时保留为未知，旧记录仍为 partial。
共同修复后的新同局复验取得 2681 条完整源事件全等、10 组新非致死父证明和 374 笔完整结算；
同版无 Frida 独立局随后收齐 2622 条事件、10 组非致死证明和 379 笔完整结算；
MuMu Frida 同版共享业务的普通关对照也已取得 10 组非致死证明及经典终局核验；
PC 卫戍已取得 16 次真实跨帧采样及 1 例技能冷初态恢复；但阶段重建基线时有 370 条已确认源记录未保留原文，
旧阶段终点仍为 gap，不能认证整场完整。跨帧窗口内新增事件、默认配置稳定性及发布门禁仍未完成，不能直接上线。
源端从零到原始终局的完整性，与主机独立累计完整性分开登记；晚采基线或缺最终累计修订时，后者仍保持未知。
特殊技能效果结束及未触发的其它机制没有因此自动通过。
未实测性能，不宣称更快、更稳定或所有保护环境兼容。完整证据和局限见文末实机记录。
当前只支持指纹匹配的官服 `win-x64:cf7956bf88a61986859ef55c026d231713e2d12cf69ffd9a19779ab491cf76ff`；
不将 B 服或其他构建自动放行。

## 构建与单来源采集

在仓库根目录执行，使用 Windows x64 Python 和 MinGW-w64 GCC/G++：

```powershell
python native_pc/native_event_runtime/build.py
python app.py --platform native_pc --native-game-root 'D:\Games\Arknights' --native-pc-event-backend native --capture-seconds 300 --output-dir 'D:\Captures\native-new' --require-events --require-progress
```

自研模式不填写合作文件时使用新组件；显式填写 `--native-pc-event-feed` 时仍走原合作 JSONL 路径。
CLI 显式传入 `--native-pc-event-backend native` 且不传文件时选择原生 Hook，不沿用旧合作文件；
未显式切换内核时仍使用已保存的文件配置。桌面设置中需清空合作文件来选择 Hook。
先启动采集入口，再正常重启客户端；独立 `NativeHookRuntime` 等待同安装路径的新进程出生，
先普通加载 DLL，待 IL2CPP 初始化与只读元数据核验通过后才安装 Hook。权限须允许普通 Windows 加载和读取。
拒绝访问时保留诊断，不自动提权、不停用保护、不修改游戏数值。
本次验证采用启动期加载；大厅暖加载曾失败，不据此承诺已有进程任意迟接可用。

专用独立验收入口额外拒绝已安装 Frida 的解释器，并禁止导入 Frida/旧 Frida 运行时；
分别在 READY 和停止前盘点同一游戏实例的模块。目录必须新建，看到 READY 后再操作游戏：

```powershell
python -m acceptance.capture_pc_native_only --game-root 'D:\Games\Arknights' --output-dir 'D:\Captures\native-only-new' --seconds 900 --wait-seconds 600
```

构建下载并按固定 SHA-256 核验官方 QuickJS 2025-09-13、MinHook 1.3.4 和 nlohmann/json 3.11.3。
缓存同样每次验摘要；`--offline` 要求缓存齐备。输出为
`runtime/native-pc/native-event-runtime/ArkDPS.NativeEvents.dll`，旁边的 `.build.json` 记录 DLL、六个源码输入、依赖与许可摘要。
启动时拒绝源码已变但未重建的 DLL。

标准便携包及旧合作事件包仍拒绝直接加载实验 DLL。
另有显式 `native_pc_native_hooks` 清单的独立启动测试变体；其加载门禁核验 DLL、六源、构建清单与许可，
不将启动测试准入等同于性能、卫戍完整性或正式发布验收。
原 `--with-native-events` 合作候选不因此升级为官服实时已验收版本。

## 同一对局对账

先用管理员 PowerShell（仅当目标游戏权限需要时）启动下面的前台命令。
输出目录必须不存在；脚本不控制鼠标、不启动或退出游戏，不消耗理智。
两套 Hook 共存仍可能导致初始化失败或游戏异常，先保留游戏现状再按测试安排重启。

```powershell
python -m acceptance.capture_pc_probe_pair --game-root 'D:\Games\Arknights' --output-dir 'D:\Captures\native-frida-new' --seconds 300 --wait-seconds 300 --stage LS-1 --allow-dual-hooks --preload-native
```

1. 看到 `WAITING_GAME` 后正常重启官服 PC，等待大厅初始化。
2. 官服已验证的启动方式增加 `--preload-native`：先普通加载自研 DLL，再准备 Frida；VM 就绪后串行安装 Frida 和自研 Hook，避免同时改写目标入口。
3. 看到 **`BOTH_READY`** 后再进入用户选定的测试关卡与模式，开局前不能已有未采到的交战。关卡名称含“演习”不能证明它是免理智的练习模式。
4. 手动完成对局并等待结算。采集到时间后自动关闭各自会话；也可创建输出目录内的 `STOP` 请求结束。
5. 检查 `pair-receipt.json`、两侧 `summary.json` 与 `capture.jsonl`，然后执行：

双探针必须按安装相反顺序拆除：先禁用自研 Hook 并等待在途调用结束，再卸载 Frida。
如果看到 `FATAL_KEEPALIVE`，脚本会保持 Frida 存活，请先从游戏界面正常退出官服 PC，**不要先结束采集器进程**。
这是异常清理保护，不是已经证明两套 Hook 在实机中安全共存。

```powershell
python -m acceptance.compare_pc_live_probes 'D:\Captures\native-frida-new\native' 'D:\Captures\native-frida-new\frida' --left-source native-hooks --right-source frida --output 'D:\Captures\native-frida-new\comparison.json'
python -m acceptance.verify_live_events 'D:\Captures\native-frida-new\native'
python -m acceptance.verify_live_events 'D:\Captures\native-frida-new\frida'
```

两侧各自持有采集会话、快照句柄、游标、原始日志、历史目录；不会共用一份事件队列来制造相等。
`candidate.json` 记录提交、分支、工作树差异摘要及未提交文件摘要，区分本次候选与历史提交。
`captured` 只代表采集命令结束；对账器本身不连接游戏、不认证文件来源。
两个独立序号不强求相等，比较同进程出生、同构建、同战局、同帧的逐笔语义、重复次数、金额和顺序。
迟接首帧与无终局尾帧从共同闭合窗口排除；没有零帧空基线、真实终局或完整覆盖时只能 `partial`，不能用空交集通过。
还须由人确认本次原始记录确实来自这场实机对局。

先测普通伤害和治疗，再单独安排护盾、完整技能起止、嵌套元素击杀、召唤归属、卫戍范围；
暂停/恢复、1×/2×、换局、迟接、断连、队列溢出与退出清理均需留实机回执。
`verify_live_events --pc-settlements` 要求同一局确实出现伤害、治疗、实际吸盾及闭合技能窗口，不能拿无盾普通局硬套或放宽要求。

## 架构与不变量

- **边界**：新增实现位于 `native_pc/native_event_runtime/`、`native_hook_source.py`；仍实现已有 `EventProbe`，不修改统计口径。
- **权威数据**：原生累计快照仍是累计权威。事件须经 `NativePcBinding` 和同帧前后水位核验，不按事件重复累加累计。
- **来源**：直接复用 `assemble_probe_source('native_pc')`，记录三段源码及实际提交字节摘要；不是把历史日志转成实时输入。
- **唯一业务逻辑**：平台与采集后端是独立维度。PC/MuMu 适配仅提供经过验证的原生布局、ABI、身份、时钟和能力；Frida/自研提供钩子、字段读取与调用上下文。
  父子结算、HP/护盾、元素、技能判断继续执行同一份共同 JS，不新增平台专属结算算法或第二统计核心。回调内事实必须同步保存，不能事后由 Python 推算。
- **隔离**：配置绑定 PID/出生时间；管道限本机同用户，随机令牌认证，客户端另核对 server PID；同进程命名互斥体拒绝第二自研 Hook 所有者。
- **资源**：I/O 有界，超时通道不重用；Windows 未完成的 I/O/加载仍保留资源所有权。停止禁用 Hook，但 DLL 保持映射至游戏退出，防止回调返回到已释放代码。
- **兼容边界**：通用 Win64 包装器最多转发 16 个参数（现有业务最高访问下标为 8）；保留浮点与大结构返回。单原生地址多个监听器及异常跨 Hook 跳出尚不支持，前者拒绝安装，后者保留活跃缺口；不能据自有 ABI 检查推断这些场景通过。
- **共享加载器**：普通 Windows DLL 加载逻辑从 Frida 启动器提取至 `windows_dll_loader.py`，旧公开类名继续兼容，不让新来源导入 Frida 运行时。
- **未知处理**：字段/签名、时钟、归属或连续性不足就降级，不补造终局和逐笔记录。现有探针覆盖缺口也会被继承，不因换 runtime 自动消失。
- **新增实现的必要性**：旧 C++ 宿主只接收已有事件，不能拥有游戏回调；新 DLL 只承担这一缺失职责。合作文件路径和现有 Frida 路径仍可替换、独立验收。

## 验证边界

专项测试覆盖选择器、真实源码提交、无 Frida 导入、指纹/初始化拒绝、进程出生变化、ACK、超时和清理重试；
双来源对账器另测独立序号、缺笔、重复、乱序、金额、错局和不完整窗口。
`native_pc/native_event_runtime/smoke.py` 只在新建自有 Python 子进程加载测试 DLL，检查实际 Hook ABI 和管道，不接触官服进程。
具体运行结果保存在本轮 runtime 回执中；自有 ABI 检查不能替代下面的官服实机证据。

### 2026-11-24 实机前已执行检查（历史候选）

- 当时 DLL SHA-256：`9fa1d00be074304532d19bc819f669627e60bb1857d94cb0070e1e053ae6a1c8`；六个源码与三份许可摘要匹配构建清单。后续实机使用下述补诊断的 `76d0676a…`，不混用候选摘要。
- `runtime/native-pc/native-event-runtime/smoke-final-v3.json`：新自有子进程实际 Hook 通过整数、浮点、32 字节结构返回、嵌套和多线程；错误令牌、错进程出生、第二所有者被拒绝。400ms 原生函数仍在执行时停止，确认排空与代码恢复；fatal 回调和断连也恢复原函数。
- `runtime/native-pc/native-event-runtime/smoke-packaged-python-dual.json`：使用已有标准候选中的 Python/Frida 17.26.1，只连接新自有子进程。两套真实 Hook 都观察到 6 次比较回调，原生先停、Frida 后卸载，返回值与原代码均恢复。**不是官服对局**，回执 `game_connected=false`。
- WPF 源码构建成功，0 警告、0 错误；设置文案静态检查无问题。没有运行鼠标交互式 WPF 验收，没有构建或发布新的便携包。
- 实机前一轮 592 项专项回归通过，覆盖新运行时输入、来源选择、双探针对账、协调清理、既有 Frida/worker 生命周期、PC 事件契约及发布边界；该计数不是之后所有修改的最终回归计数。

这些历史检查只排除了已测的传输、ABI 和基础共存错误，不单独证明官服采集可用。

## 2026-11-24 用户配合实机：启动链问题

用户负责正常退出、启动客户端及后续对局操作，小怪不接管鼠标。当前实机系统记录为 Windows 11 25H2 / 26200.7623，
鹰角启动器官服 PC 的三个磁盘指纹匹配上述已知构建。候选仍为 `012acf4` 加未提交改动，各轮 `candidate.json` 单独保存源码摘要。
ACE 版本和运行状态为**待资料／待补充**；下面的 Windows 错误不能据此归因为 ACE，也不能由参考项目可用推断所有 ACE 环境都兼容。

| 证据目录（均在 `runtime/native-pc/`） | 实际操作与结果 | 战斗样本 |
| --- | --- | --- |
| `live-pair-20261124-022646` | 新实例 58604；Frida 启动后立即采样时模块尚未唯一就绪。补上借用父级 bootstrap 的模块等待路径。 | 0 |
| `live-pair-20261124-023216` | 新实例 67540；读取模块文件名返回 `GetModuleFileNameExW(6)`。新增限定 API、限定时间及同实例存活核验的重试，不吞掉稳定失败。 | 0 |
| `live-pair-20261124-023942` | 同实例大厅暖重连；Frida 观察者真实就绪，自研 DLL 普通加载线程返回 `0xffffffff`，尚未连接初始化管道或安装 Hook。按顺序结束会话，客户端仍运行。 | 0 |
| `live-pair-20261124-025231` | 新实例 69244；提前加载成功，但只读 VM 门禁把尚未初始化的 NULL 字段表过早判为 fatal。修成只对已核验类身份、字段总数且未缓存类的 NULL 表等待，错误类或布局仍拒绝。 | 0 |

第三轮 DLL 为 `76d0676a44ff66350d44a3ff85c70e5ccf2ac61250e6fc93cc323aad9ebad0b1`，相较上一版只补白名单阶段诊断。
`0xffffffff` 是线程退出 DWORD，**不是目标线程的 GetLastError**，当前不足以确定是否进入过 LoadLibrary、哪一步失败。
没有把以上 0 样本结果称为官服采集成功。

追加自有对照使用同一 DLL 和真实 `WindowsDllLoader` 跨进程加载，而不只用子进程内 `ctypes.WinDLL`：
`owned-remote-loader-live-diagnostic.json` 核验成功；`owned-gadget-order-1795459545795190600/receipt.json` 中，
先加载 Gadget、以及先加载 Gadget 再连接无害 Frida 脚本两种顺序也均成功。
这只排除了自有环境中必现的普通加载／顺序故障，仍不能解释官服的线程结果。

### 用户提供的 ArkFramework 参考

已固定核对 [Mon4terTeam/ArkFramework 的 a2ae65f](https://github.com/Mon4terTeam/ArkFramework/tree/a2ae65f37282c9ba2d64b759ce918ecaa82be87d)，
仅阅读，没有把 GPLv3 源码复制进本项目：

- `plugins/damage_meter/native_capture.cpp` 使用类型化 C++ `BattleLogger.LogModifier/LogEpModifier` 等七个 Hook；
  我们当前候选则是原探针业务逻辑加 QuickJS 兼容层，不能说是直接模仿其采集核心完成的 C++ 移植。
- 双方都使用 MinHook，并都校验 unboxed `Modifier`；参考实现的每目标锚点、owner 链和排空后保留跳板值得借鉴，
  但其四 GP 参数通用 gate 不能直接替代本候选对栈参数、XMM 和大结构返回的处理。
- `src/loader/remote_thread_loader.cpp` 经普通 `LoadLibraryExW` 调用，并另存完整模块句柄、同线程错误和阶段，
  随后显式调用启动导出；`src/core/framework.cpp` 的 DllMain 只保存模块句柄。
  我们目前直接使用 LoadLibraryW 线程入口，且 DllMain 创建 worker，加载与诊断边界确有改进空间。

用户同意继续一次启动期加载对照。新 `--preload-native` 只改变加载时机：同一新进程先普通加载自研 DLL，再准备对照组件；
不在 VM 就绪前认证或执行 JS。准备对象独立于 Frida，绑定进程出生、路径、构建与源码，120 秒过期即失败而不重复加载。
本轮关闭双方敌人 HP 合并，避免独立 mark 时机造成逐笔对账的合并边界差异；不是性能测试。
启动期对照之后取得实机结果，见下节；不能将先前四轮零样本尝试回写成成功。

## 2026-11-24 官服实机结果

### 实验设置与干预

- 用户自行正常退出、重开官服客户端，进入原始关卡 ID `Obt/Weekly/level_weekly_killcost_1`，
  完成两场对局并停在结算页。仓库关卡目录将该 ID 映射为 LS-1「遭遇战演习」，不是 CE-1；目录摘要记录在最终验证回执。
  小怪不接管鼠标，不修改游戏数值，不停用保护。日志 `practice=null`，用户反馈称“演习”，但不能由关卡名称或该反馈认证练习模式、理智消耗。
- Windows 11 25H2 / 26200.7623；构建指纹为本文顶部限定的官服版本。当前没有可核实的 ACE 版本资料。
- 两轮均使用 DLL `76d0676a44ff66350d44a3ff85c70e5ccf2ac61250e6fc93cc323aad9ebad0b1`。
  源码候选为 `012acf4cbcec8e36ce7c4eb080f73672bab09f79` 加未提交修改；各轮 `candidate.json` 冻结实际工作树摘要，不能把 HEAD 单独当作完整候选。
- 双源局先提前加载 DLL，再启动 Frida；两侧独立队列、快照句柄和历史目录。均关闭敌人 HP 合并，以便比较原始逐笔事件，**不是性能实验**。
- 独立局使用 `F:\Code\python\python.exe`，启动时确认无 Frida 发行包，随后阻断 Frida 及旧 Frida 运行时导入。
  由 `PcEventSource → NativeHookRuntime → PreparedNativeHooks → NativeHookEventProbe` 启动，不借用双探针观察器或 Frida 启动器。

### A. 同局双探针对账

证据目录：`runtime/native-pc/live-pair-20261124-030258/`，进程出生为 `[72576, 134399342194759909]`。

| 项目 | 实际结果 |
| --- | --- |
| 发布采样 | 自研 655，Frida 696；独立采样节奏不同，不要求快照数量相等 |
| 逐笔 | 两侧各 476；共同闭合窗口 frame 2–3480 内，完整语义和证明字段、金额、重复次数、顺序全部一致；缺笔及差异 0 |
| 类型 | damage 148，damage_settlement 37，hp_damage 37，healing 23，enemy_enter/finish 各 35，skill_start 83，skill_end 76，enemy_hp 2 |
| HP 与治疗 | 37 笔敌方实扣合计 29,720；23 笔直接治疗均有 UID/前后 HP/realDelta 证明，实际恢复 2,559.9999428，与 HP 差一致 |
| 原生累计 | 两侧逐笔与各自已发布累计窗口核验通过，沿用既有浮点容差；104 笔友方/自伤仍按原 Stats 契约排除，没有修改产品统计口径 |
| 终局与停机 | 两侧真实状态 3、frame 3481、time 116.0332227、最终序号 476；先禁自研 Hook 并确认排空，再停止 Frida；无清理错误，无需异常保活等待 |
| 严格完整性 | **partial**；自研基线 frame 1、Frida 基线 frame 0，没有共同零帧基线或原始终局 fence，不能称全局从头到尾逐笔完整认证 |

回执：`dual-probe-comparison-revised.json`、`verify-native-revised.json`、`verify-frida-revised.json`、
`observed-live-journal-analysis.json`、`pair-receipt.json`。原始 `capture.jsonl` 未改写，初版验证失败回执也保留。
修订对账器允许有界 read 的当前时钟晚于同源快照，但事件前缀、through、归属和事件时钟仍严格校验；
修订单侧验证器使其与 Stats 既有友方伤害排除口径一致，并把 `damage_settlement` 视作独立证明记录，不重复计金额。
两项工具修正均有回归，未放宽金额容差或无盾机制门槛。

### B. 新客户端纯自研独立采集

证据目录：`runtime/native-pc/native-only-live-20261124-031323/`，进程出生为 `[68336, 134399349442868850]`。

- 619 个发布采样，逐笔序号 1–513 连续；最后状态为 3、胜利，frame 3626、time 120.8665514、watermark 513，
  `event_status=finished`，`event_error=null`，`summary.error=null`。用户结束后创建 STOP，采集程序正常退出，退出码 0。
- 类型为 damage 167、damage_settlement/hp_damage 各 43、healing 11、enemy_enter/finish 各 35、skill_start 89、skill_end 82、enemy_hp 8。
  43 笔 HP 结算全部完整，实际扣血 29,720；11 笔直接治疗均有完整 HP/realDelta 证明，实际恢复 2,841.5999365，全部与前后 HP 差一致。
  113 笔友方/自伤沿用既有统计排除口径；逐笔与各来源原生累计、已发布统计窗口核验通过。
- Python 未安装 Frida，guard 全程存在，结束时 `frida_modules_imported=[]`；READY 和停止前对同一进程实例各完成 223 个模块的枚举，
  未发现文件名包含 `frida` 的模块。**这仅是两次边界盘点，不覆盖重命名模块，也不是所有时刻或所有模块内容的签名认证。**
- `native-only-receipt.json` 的 `captured` 只表示采集完成，`live_acceptance_passed=false` 保持不变；
  状态 3 和发布层 finished 不代替尚缺的原始 terminal fence。不能把两场不同操作的事件金额强行跨局比较。
- 正式校验回执为 `native-only-guarded-source-validation.json`、`native-only-counter-verification.json`、
  `native-only-strict-mechanism-verification.json`、`native-only-evidence-analysis.json`。前者核对同出生/构建/战局和连续前缀，
  累计对账通过，严格机制回执因 0 正吸盾如实失败。没有改写原始日志或其候选摘要。
- 独立局未单独落盘原始 stop ACK；正常停止由严格排空实现、无清理错误及退出码间接支持，不能把它写成已经保存的原始 ACK 证据。

### 未覆盖与交付范围

- 已实测：该指纹官服构建上，启动期普通加载、无 Frida 独立实时伤疗/技能/敌情事件、同局共同窗口双源逐笔齐平和正常停止。
- 仍未通过：正值吸盾、元素爆条与嵌套击杀、完整原始终局封口、换局/断连/溢出等实机矩阵。`--pc-settlements` 因本局没有正吸盾真实失败，不删门槛。
- 技能事件同样不自动等于效果持续窗口；例如新约能天使 `skill_active_seconds=null`、`skill_coverage_complete=false`，不能用两侧相同的未知冒充完整验证。
- 实录 `speed=0/1/2` 与 `paused=true/false`；双源局有中局暂停，独立局的 paused=true 只在终局。
  没有受控倍速、FPS/CPU 对照；不能据枚举值认证实际倍率等价，或据事件一致声称无性能损失。
- 尚未运行 WPF 鼠标交互验收，未构建便携发布包，未提交、推送或发布。Frida 仍是产品默认来源，实验 Hook 继续排除在便携分发外。

### 实机后的接线修复与最终检查

实机停止后修正 WPF 提供卫戍偏好回调导致 Prepared 源码不等的问题：预加载和接管都比较完整的无 UI 前缀业务源码，
配置、环境和 DLL 摘要门禁不变；偏好通过原有 typed mark 在 READY 前发送，以后动态发送。
初次发送失败不报 READY，不读/ACK 事件，也不推进基线。已开始交战的 effective mode 仍由探针既有范围锁定规则决定，不伪称即时改写。
该 Python 适配修复发生在实机之后，只有专项测试，没有再次宣称其 WPF 交互或卫戍实机通过；DLL 与业务探针未变。

最终一轮 **548 项聚焦回归通过（25.41 秒）**：原生 Hook/独立启动器/普通加载器、内核选择、两种验收入口、双源对账、
累计验证、CLI、冷启门禁、既有 Frida 与会话生命周期、事件身份、合作文件来源、构建摘要及无旧 Tk 约束。
未跑全量套件或鼠标交互测试。完整变更仍留在当前工作树，没有提交或发布。

### 变更控制记录

- **范围与局部性**：进程内 runtime 只提供原有 EventProbe 所需能力；Python 独立启动所有者承担早载与生命周期，
  选择器和 CLI/worker 接线是共享入口所必需，统计和历史格式未另起一套。WPF 只保留既有设置入口。
- **不变量**：原生累计权威、同帧同局绑定、消费后 ACK、未知降级、先停止观察器再释放启动所有者均由对应专项测试保护；
  VM 冷启只等待已核验类的 NULL lazy 字段表，未放宽类身份、字段总数或偏移核验。
- **可追溯与替换性**：每轮 candidate、原始 journal、来源摘要与派生回执分开保存；两种 EventProbe 和合作文件来源可替换，统计层不绑定 QuickJS/MinHook。
- **恢复与语义负担**：新增 Prepared 是为解决实测大厅晚载失败而隔离“DLL 已加载、JS/Hook 尚未初始化”阶段；
  既有观察器不能代表这一阶段的资源所有权。它只增加单次、有时限、可验证的移交，不形成第二套统计或协议。
  超时、同出生变化、源码变更或清理失败就拒绝并保留必要资源，不能后台循环加载或伪造成功。
- **剩余缺口**：完整原始终局、未覆盖机制、WPF 交互、保护版本与性能都没有升级为已验证结论。

## 2026-11-24 首尾与结算机制追加验收（进行中）

用户继续配合操作官服客户端，可用闪灵二技能测试实际吸盾、塔露拉触发元素爆发。以下是新候选实现与验收安排，
**不是已完成的实机结果**，也不修改之前两局的原始日志或 `partial` 回执。

- **首帧源端见证**：在真实 `BattleController.set_state` 进入 PLAYING 的返回回调中，全部 Hook 已安装、
  同一五根/关卡/世代/结算 scope 稳定且实际 frame/time/sequence 均为零、无在途回调时，保存 `initial_boundary`。
  非零帧、已有事件、重复 setter 或未知初态都不能生成零起点。历史见证不因正常事件增长被改写。
- **首次 ACK 前的完整前缀**：专用单局验收会额外读取 `read(0, first_accepted_sequence)` 并写 `source_initial_prefix`，
  记录实际读区间、提交游标、原始队列与 closing mark，不 ACK、不喂给以晚快照建基线的 Stats。
  验证器只选日志中早于首次已接受样本且完整 mark 相同的候选；错误或未被接受的尝试不能冒充初态。
  首次快照前使用 10ms 轮询提高实际零帧快照的捕获机会，之后恢复原有 100ms；两个间隔均写入日志，不据此保证读到零帧。
- **两种完整性分开**：`event_prefix_from_initial_boundary_complete` 需要真实源端零起点、连续前缀及真实原始尾封口；
  `native_cumulative_from_zero_complete` 另外需要独立主机零帧/零累计的充分证据。源端见证不能把晚快照改为零，
  也不替代未知初始累计；旧 `complete_battle_comparison` 门槛不因此静默降低。
- **真实终局**：仍以 `OnDestroy` 的回调证书为最终封口，不将 `state=3` 直接升级为不可撤销的原始 fence。
  离场后即使此前已发布 finished，也独立读取并记录真实 fence。只按已消费、独立核验的同出生/五根/关卡/时钟/
  序号/epoch/scope 水位绑定；外层必须明确无在途回调。错配只留原始候选和拒绝诊断，不重新累加或 ACK。
- **嵌套结算**：原先仅有 `settled_after_nested_kill` 标记的路径补充实际父子证明，必须同目标、同帧、真实完整子结算及
  爆发记录，父级零实扣和子级结算后 HP 均来自实读，不能用名义伤害回填 HP。

新验证器可按测试目标要求明确的正证据，不修改旧 `--pc-settlements` 的四类事件门槛：

```powershell
python -m acceptance.verify_live_events 'D:\Captures\new-pair\native' --pc-mechanisms shield,elemental,nested --output 'D:\Captures\new-pair\native-mechanisms.json'
python -m acceptance.verify_live_events 'D:\Captures\new-pair\frida' --pc-mechanisms shield,elemental,nested --output 'D:\Captures\new-pair\frida-mechanisms.json'
```

盾门槛证明支持 buff 的实际正吸收与入伤/盾余额关系，不额外声称受盾目标 HP 守恒。
元素必须真的出现正元素条与爆发；嵌套必须有完整同组父子证明，不以“同帧击杀”替代，证据不得被多组重复使用。
计划先同局双源逐笔对照，再以新客户端、无 Frida 解释器独立重复关键机制。
每个入口按新观察者单局验收；当前 opening 审计不能用作同一观察者多局都已留证的证明。

### 追加实机第一轮：首尾通过，发现晚于 FINISHED 的尾笔

证据目录为 `runtime/native-pc/parity-pair-20261124-034723/`，新进程出生 `[46004, 134399370917742702]`。
本轮运行前 1287 项相关回归通过（34.11 秒），自有进程原生/Frida 双 Hook ABI 检查通过；它们不替代下面的实机事实。
两侧实际提交业务源码均为 `d89546de1f808b0f0455472d8fce091df490954bf5d6409c5f46a9ca34adbbb8`，DLL 仍为 `76d0676a…`。

用户第一局忘带闪灵，约 5.77 游戏秒主动退出，随后已进入第二局。没有删除或拼接原始记录；
离线工具增加显式 `--native-session`，只在内存按已校验的唯一 generation/原生战局身份选择观察与发布记录，
保留原始文件摘要、原始行号、原始全局样本数及选择/丢弃计数。默认仍拒绝混合多局。

- **第一局（generation 1，session 尾段 `fc60e28b…`）**：两侧均真实 frame/time/sequence=0，源端零帧证书及首次 ACK 前读取均完整。
  真实 OnDestroy 封口为 frame 173、time 5.766661167、sequence 2、epoch 4、scope 1。
  两笔入场事件逐笔相同，正式回执 `case1-dual-probe-comparison.json` 为 `matched_complete_battle`，
  源端起止前缀与独立零累计完整性均通过。**这是提前退出的短局，不能代替任何正伤害或机制样本。**
- **第二局（generation 2，session 尾段 `7840fbc1…`）**：同一观察者的全局序号已从 2 起，源端正确拒绝伪造新的 seq0 初证。
  两侧实际主机初采均为 frame/time=0、seq2；已读 seq3–1740 共 1738 条真实事件，逐笔一致。
  正吸盾 7 次，均为闪灵 `shining_s_2`，合计约 105.675002，均未破盾；元素条 104 笔，酒神 79、塔露拉 25。
  4 笔元素爆发均实扣正 HP 至零，爆发来源字段为 null，不能据混合贡献条唯一归给某位干员。
  `settled_after_nested_kill` 和 `proof.nested_settlement` 均为 0，**爆发致死不等于已证明嵌套结算**。
- **真实尾笔缺口**：第二局双方最后发布水位为 frame 5834、time 194.4664812088、seq1740、epoch5546；
  真正 OnDestroy 证书却为相同时钟及 scope 3 的 seq1743、epoch5549。接收门禁正确拒绝错位封口，
  未读到的 3 条事件种类未知，不从旧日志补造。第二局完整性仍为 `partial`，不能以两侧相同缺口称完整通过。
- **共同数值验证缺口**：旧 f64 逐笔求和与原生 binary32 累计在山和塔露拉上超过旧近似阈值，两侧相同失败回执均保留。
  原生读取器确实用 `<6f` 读分类型计数；按真实事件顺序逐次 `f32(acc + f32(amount))` 的诊断，对自研 34310 个、
  Frida 33325 个实际发布分类型值均精确吻合，无需放宽容差。这是本样本对数值模型的精确支持，不声称已认证闭源客户端更新指令。

停机回执为 captured、error=null、原生 Hook 禁用确认 true，无清理错误或异常保活。
下一候选将使用真正的两阶段终局：FINISHED 暂不关闭事件流，销毁 fence 下读取真实有界尾笔，消费成功后 ACK，再封口；
缺独立最终累计时须保留累计未知。该修复和无 Frida 机制复验在本段记录时仍待完成。

### 追加第二轮：完整源对账与独立非空尾笔

新 DLL 为 `38e706b3b0210e0d6d7907cb7283ac28e99167301ac51cd851ce8a9da86951d5`，新增专用 `terminal_read` 的桥接分派。
READY/mark 能力为 `terminal_read_version:1`，结束观察的有界尾部证据为 `terminal_tail_version:1`，二者是不同层字段。
普通 mark/read 的活对象绑定门槛未放宽。

- 新流程先保存真实 state3 累计，但不结束事件入口；销毁后只读同出生、同五根、同关卡、同精确时钟与 scope 的真实队列。
  新终点序号/epoch 必须单调，读前后 fence 必须相同，消费完成才 ACK，确认后才声明封口。
  不能拿旧 rows 假装最终累计，实际 snapshot/totals 完整性字段会降为未知，而非仅写一条诊断。
- 仅对明确的源端忙/等待销毁票据，保留至多 2 秒、20 次读取准入；真正拒绝、换出生、传输错误立即保留 gap，超限不合成终局。
- 采集端统一 1535 项相关回归通过（34.22 秒）；新增原生 ACK/stop 白名单审计的入口组合 112 项通过；离线工具专项 216 项通过。
  它们是相关检查的不同轮次，不伪称一条全量套件结果。
- 新 DLL 的自有进程 ABI/双 Hook/停止检查通过；额外实测 4 个 400ms 回调在途时 terminal_read 不分派、读水位保持 0，
  静默后才分派，停止返回 active0/native_quiet=true 并恢复代码。该自有检查不冒充官服尾笔正例。

本轮开启 `ARKDPS_TRACE_HP_CONTEXT=1`、`ARKDPS_TRACE_NESTED_PARENT=1` 且关闭敌人 HP 合并，两端实际提交源相同：
`4e6ebfc673c19de5a368befb4b0ff5019a0ab30c7b1a1854bc6e65e0396bfedd`。诊断有界，不作性能比较。

#### 同局双探针

目录 `runtime/native-pc/parity-tail-pair-20261124-041758/`，出生 `[32004, 134399388030681655]`：

- `dual-probe-source-prefix-comparison.json` 核对完整源序列 1–1480，逐笔字段、金额、完整证明及顺序无差异。
  源端真实零帧证书、首次 ACK 前 read0..1、真实 OnDestroy 终点均通过；主机实际首采 frame3/seq1 未改写为零。
  `event_prefix_from_initial_boundary_complete=true`，独立零累计和旧全局 complete 仍为 false。
- `verify-{native,frida}-f32-all-mechanisms.json` 均成功：自研 36510、Frida 35145 次实际发布分类型值精确检查；
  正吸盾 11、元素爆发 3、一般嵌套 1 组均过严格门槛。2 条父事件 proof 代表同 1 组，不当作两组。
- 真正嵌套组为 frame5502、目标 UID199、`enemy_1035_haxe`：塔露拉父 HP 4154.40530335→2890.88256253、实扣1263.52274082；
  子元素再扣2890.88256253至零。7字段旧证明与同组实际子结算/爆发一一匹配，无 `settled_after_nested_kill` 标记，
  因而只认证“父先扣 HP→子击杀”，不混称新增 after-kill 路径。
- 销毁终点 frame5712、time190.3998184204、seq1480/epoch5295/scope1；终局专用读取真实成功，但区间1480→1480为空。
  ACK复用此前已确认的同水位；native侧只有对应确认状态，不能称另存了这一局的独立 wire ACK。
- 46笔直接治疗的UID/HP/delta证明有效，实际回血约833.459046；正吸盾合计约160.900004，均未破盾。
  新约能天使仍缺完整技能效果结束，旧 `--pc-settlements` 全技能门槛失败回执保留。

#### 纯自研独立运行

目录 `runtime/native-pc/native-only-tail-live-20261124-043048/`，出生 `[27864, 134399395026895363]`：

- F盘Python无Frida发行包，安装import guard后才导入采集链；实际source/DLL与上面的双源候选一致。
  首个独立快照真实frame0/time0/seq0，完整源序列1–1519连续无冲突。
- **真正非空尾笔实机正例**：末主机累计水位seq1516/epoch5083，OnDestroy同帧5558/time185.2664899826/scope1的终点为seq1519/epoch5086；
  专用读实际取得1517、1518、1519三笔塔露拉 `skchr_oxi_2` 技能结束记录。
  原始end在第16044行先落盘，第16045行收到实际ACK1519，第16046行归档finished；没有补回旧局未读的三笔。
- 源前缀完整，最终累计则缺独立重采：`final_counter_status=unknown`、所有行 `counter_snapshot_complete=false`、`totals_complete=false` 实际生效。
  数字窗口严格止于seq1516，34615次f32分类型检查通过。
- 正吸盾10、元素爆发击杀6；盾合计约140.000002，均未破盾。40笔直接治疗HP证明有效，实际回血约1651.949936。
  本局6次爆发父层均为酒神type0/target5元素条回调，没有HP父作用域，强制嵌套门槛如实失败。
- 原生管道白名单审计记录347次ACK，均与请求及先落盘同源前缀匹配；停止真实回包error=false、active0、native_quiet=true。
  READY/停止前两次完整盘点各223模块，未发现文件名含Frida的模块；结束时Python相关模块列表为空。
  此结论不覆盖改名模块、模块内容签名或所有瞬间。

两局原始stage ID均为 `Obt/Weekly/level_weekly_killcost_5`，本地目录映射LS-5。
沟通及用户完成反馈曾称CE-5，报告保留该差异，未用口头标签替换原始ID；练习模式与理智消耗仍未独立认证。

显式 `--pc-f32-counters` 按实际 `<6f` 存储与真实事件顺序精确复现每个发布分类型值，
再按既有公开 `burst_added_settlement` 契约逐水位核对source-null爆发的名义补充，不把实际HP混为名义量。
不修改全局near，不用误差放宽兜底；旧f64近似失败及初版补充口径失败回执全部保留。
本样本支持该数值模型，不声称反汇编认证了闭源更新指令。

下一局仅做一次有干预的独立嵌套复现：酒神移出/不部署，塔露拉单独打存活高血量目标，代码与DLL不变。
该针对性局在本段记录时仍进行中，不用双源旧证书代替独立正例。

### 追加第三轮：独立嵌套正例与共同非致死缺口

目录 `runtime/native-pc/native-only-nested-live-20261124-044206/`，出生 `[9412, 134399402521234420]`，
DLL 与共同源码仍为上一轮候选。实际关卡已变为 `Obt/Hard/level_hard_07-04`，不是仅移除酒神的单变量对照。
初始编队仍包含酒神，但本局原始来源/元素条均无酒神贡献；塔露拉独占 220 笔元素条，不将“不部署”说成已证实移出编队。

- 源序列 1–2406 连续，无丢序；源端零帧初证、真实销毁终点及最终 ACK 均有证据。
  终点 frame8232、time274.3997383117676、seq2406/epoch7553/scope1。
- `verify-f32-nested-elemental-window-revised.json` 对实际发布值完成 50040 次精确 f32 检查，
  验证 6 组父 HP 先结算、子元素再击杀，16 个来源空且具有元素条见证的爆发及 20 个显式原生元素贡献。
  后两者不混称 36 次新爆条；此局无盾正例，不能补称本局已覆盖盾。
- `native-only-absence-wire-validation.json` 核对 491 次 ACK；末水位 ACK 已在空终局读取前真实确认，
  空尾复用同 fence 票据，不称终局后另发了新 ACK。停止实际 active0/native_quiet=true，原始 receipt error=null。
  两个完整模块盘点边界各 223 模块、无 Frida 文件名；未安装 Frida 的解释器导入阻断有效，仍不宣称全时或改名模块认证。
- 10 笔父结算（seq281、314、327、335、542、673、691、822、1217、1732）仍为 complete=false/amount0。
  独立子元素结算真实非致死，父 signed delta、子进入/返回 HP 的三阶段诊断自洽，但共同运行时仅保存元素击杀证书。
  因而严格全局语义完整性仍为 partial，不是传输漏笔，原记录和失败回执不改写。

历史核对区分了两项问题：MuMu `e29a385` 已修普通 type1/2/3 非致死嵌套，当前两端已共用；
元素 type5 的旧 `3ca23f1` 和共同提取 `a7d2c22` 仍为 kill-only。本次须修唯一共同逻辑，不做 PC/MuMu 两份补丁。

## 上线前剩余门禁（2026-11-24，继续开发）

以下是工作状态，不是发布批准或新的实机通过证书；旧轮次测试计数不代表之后的源码。

| 门禁 | 当前状态与完成条件 |
| --- | --- |
| 共用非致死元素证明 | 共同运行时和验证器已修，新增 99 项生产回调/统计回归。PC Frida、PC 自研、MuMu Frida 三条路径各取得 10 组完整非致死父证明；PC 双源/独立及 MuMu 分别 374/379/344 笔 settlement 全 complete，原 partial 事件不变。仅认证 source-null/type5/唯一完整子结算，after-kill 零父量路径不放宽。 |
| MuMu/PC 已修差异 | PC 卫戍 event-only 跨帧路径复用 MuMu 共同规则，新增 45 项 PC 回归。实机 224 个独立 guard 样本全部通过，防守/协防各 8 次真实跨帧；这些窗口的事件序号均未推进，不能认证跨帧新增事件竞态。普通累计及终局门槛未放宽。 |
| 公共技能初态遗漏 | 已选择性复用主线 `a12a0a7` 的 3 个公共业务文件和 2 份回归，保留本分支终局累计修订。修复前新增用例 10 failed/79 passed；修复后相关 8 文件 358 passed。卫戍实机 1 例 actor 在真实 effect-start 后恢复分类，先前 4 笔仍未知；不等同全 actor 或整局技能完整。 |
| 冻结候选后实机复验 | 普通关三路径完成指定范围。卫戍局部跨帧及技能恢复已有新证据，但 8 次 scope rebase 跳过了 370 条原始留证，16 个旧 scope 均保留 `new_battle/gap`。先补每次 rebase 的真实队列审计再定向实测，不用新基线冒充旧终点。MuMu 观察窗/原始能力差异保持不变。 |
| 运行稳定性与性能 | 旧局启用高密度诊断且关闭 HP 合并，不是默认配置性能结果。还需默认配置长局/多局、暂停与倍速、重连与切源、退出清理；故障注入与实机证据分别登记。 |
| 发布集成 | 新增独立 `--with-native-hooks` 启动测试变体，包级默认官服 PC / 自研 / 空 feed，不带 Frida，并核 DLL、六源、构建清单及许可。标准包及旧 `--with-native-events` 合作文件候选不变；实际包内检查、用户启动、性能及完整发布仍须分开登记。 |
| 正式交付 | 已从隔离干净提交生成独立启动测试 ZIP，但未取得用户启动、包内实机性能、完整卫戍交接及正式发布验收。开发工作树与候选快照分开，不把本地 ZIP 当公开 Release；交互验收仅在隔离 CI 或用户另行交桌面时进行。 |

首版可支持的机制及仍未知项目需明确列出：本轮未触发的 after-kill 新顺序、特殊技能终点、破盾/全吸收、召唤与卫戍新路径不能由其它正例自动认证。
共同业务已复用不代表原始能力已相同：MuMu 尚未实现 PC 新增的 `terminal_read` 协议；首帧证书、时钟回退与代次策略还需能力审计及对应实机证据。
这些差异不预设为已证实的错误，也不能为表面对齐而把能力标记全设为 true 或复制 PC 五根到 Android。
当前只承诺已验证构建的启动期加载；大厅任意迟接、其它构建及保护环境普遍兼容仍无证据。

### 本轮修复后的离线整合检查

`runtime/native-pc/prelaunch-integrated-20261124.json` 记录 71 个相关测试文件、命令及本轮源码摘要；
实际 **2429 passed in 44.73s**，测试前后相关源码摘要不变，`git diff --check` 通过。
该检查覆盖共同探针、MuMu/PC 事件窗口、统计与技能、原生读取/Hook 宿主、首尾/ACK、验收工具和发布边界，
不是整个仓库全量测试，不含新的官服/MuMu 实机、便携包构建或鼠标交互验收。
原生 DLL 仍为 `38e706b3…`，当前六个 C++/bridge 构建输入与清单匹配；JS 在初始化时由宿主组合提交，
所以新共同 JS 的离线通过不能借旧 source SHA 的实机回执升级为修后实机通过。

本轮沿用既有所有权：源内证明仍只在共同 JS，平台仅采原始事实；宿主窗口复用 `EventProbe`/`BattleFeed`，
公共技能初态复用上游 `Statistics`，不新增事件总线、启动器或统计权威。证据不足仍局部未知，
丢序/错身份/原生 guard 不一致仍拒绝；未降低旧原始终局、普通累计或 after-kill 门禁。

### 修后新同局实机复验

目录 `runtime/native-pc/prelaunch-parity-live-20261124-053350/`，出生 `[70960, 134399432836029699]`，
实际关卡 `Obt/Hard/level_hard_07-04`、胜利，练习模式和资源消耗仍未独立认证。用户操作客户端，未控制游戏 UI。
两侧 assembled SHA 为 `d47a1d832b8033c8dc324e4acf0944f92b8ddabf8f98d3d1c1f8d95b485da164`，
诊断前缀后的 submitted SHA 同为 `371b13b377e72747a598cec4ed2b5f0e84fa1ec42f4d674efa2afa0a09aa1d56`；
DLL 仍为 `38e706b3…`，不是沿用旧 source SHA 的实机证明。

- 2681 条源事件逐笔语义、金额、完整 proof、重复次数及顺序完全一致；374 笔伤害结算均完整。
  14 组父子证明中，10 组为 source-null/type5 子元素结算后仍存活，4 组为子击杀；每组均独立核对原生 entry/return/parent-return trace，
  不拿普通 type1/2/3 或击杀替代新非致死正例。旧局 10 条 partial 原始字节不改。
- 酒神无来源贡献，221 笔元素条均为塔露拉；14 个 source-null 爆发与 24 个显式原生元素贡献分列。
  正吸盾 7 笔、合计约 2133.119996，其中 6 笔盾余额归零；不能将破盾样本说成全吸收或零吸收已覆盖。
- 双方源端都有真实 frame0/seq0 初证；native 主机首采为 frame0/seq0，Frida 为 frame2/seq2，后者额外 source prefix 只含两笔入场。
  因此源前缀完整，但旧双源整局累计门槛仍为 partial，不能倒填 Frida frame0 快照。
- 真实 OnDestroy 终点为 frame8376、time279.19973373413086、seq2681/epoch8408/scope1；终局读取为空尾，
  counter/event 水位同 2681，最终累计 verified_at_final_watermark、totals_complete=true，归档 finished/error=null。
  Frida 有真实 ACK2681 时序；native 本 pair 没有单独 wire 审计，确认来自 owner 状态及清理回执，不声称保存了其 raw ACK/stop 包。
- 采集器正常反序关闭，native_hook_disable_confirmed=true、protected_game_exit_wait=false，两 summary error=null；
  DLL 按设计保留映射，不将 FreeLibrary 当作验收成功条件。本局没有 event-only anchor，不能认证卫戍跨帧新路径。

原始失败仍保留在 `verify-{native,frida}-f32-nested-elemental.json`：
一是验证器将已被 seq323 消费的结算 seq322 再列入同帧同额 seq344 候选，误报不唯一；
真实 322→323 与 343→344 有独立非重叠 scope、连续 HP 链，不能合并或去重。
二是全原生行 f32 模型包含 `trap_011_ore`，但共同探针的伤害采集域本来只包含 `char_`/`token_`，地图装置只有原生累计而无对应伤害事件。
原生装置计数 500→1000 的变化不能由缺失域的事件补出；不能删原始行或宣称全原生源计数已通过。
后续修订须分别解决通用的一对一消耗，以及显式有界计数验证域，保留完整原始记录、失败回执和所有排除项。

#### 同一原始实机记录的验证器修订

未修改采集器、共同 JS、DLL 或本局原始日志。两处元素配对仅在构造候选时排除已消费的证书，之后仍要求唯一剩余、完整 HP/身份/时钟；
两张未消费证书的真歧义、重复 burst、缺少第二证书及错字段继续拒绝，不按最近序号猜配。
数值验证新增显式 `--pc-f32-source-domain owned`，`owned` 只指共同探针 `char_`/`token_` 命名空间，不代表本家玩家归属。
该模式保留全部原始事件、验证所有原生行格式，所有合格命名空间来源每个发布点均需覆盖；
仅已有公共分类器明确证明的非队伍源可透明排除，未知源、贡献事件缺行、晚出现正累计却无该源事件等继续拒绝。
排除源的真实 seed/final/last-seen/全部变点单列，`full_native_row_counter_model_verified=false`；每源 seed 之前未建模的区间不冒充零起点证明。
默认 `all` 不变，重跑仍保留地图装置 500→1000 的失败，未把有界域成功升级为全原生行成功。

修订工具及相关入口组合实际 **543 passed in 8.52s**，118 个冻结文件中仅授权的验证器及其测试两文件变化，检查期间无源码漂移。
修订回执 `strict-revised-verification-summary.json` 与 `verify-{native,frida}-f32-owned-all-mechanisms-revised.json` 分别保存当前工具及采集时旧工具摘要：
两侧 11 个命名空间来源的发布窗口分别完成 50540、52105 次精确 f32 检查，逐水位名义补充均通过；
盾 7、元素 38、嵌套 14（其中新非致死 10）均无无效证据。2681 条源逐笔仍完全一致。
这些结论是指定采集域和机制的实机复验，不是全机制、全原生源计数、卫戍跨帧或便携包上线通过。

```powershell
python -m acceptance.verify_live_events 'D:\Captures\new-pair\native' --pc-f32-counters --pc-f32-source-domain owned --pc-mechanisms shield,elemental,nested
```

### MuMu / PC 同版共享业务的普通关实机对照

目录 `runtime/native-pc/mumu-shared-live-20261124-061004/`。用户先退出 PC，再打开 MuMu“官服16384”实例；
实际 Android 12 / SDK32、宿主 ABI x86_64，Frida READY 实际为 `arm64` / `emulated` realm，游戏 2.7.91 及构建元数据核验通过。
用户确认 Root 开关开启并手动执行 `adb root` 后，实读 uid0；没有自动提权或重启 adb server。
日志里的模拟器版本 1.0.0.0 来自宿主可执行文件，不将其解释为 MuMu 市场发行版本。

共同 runtime 原始 SHA 仍为 `9d76c508…`、context 为 `e159f0df…`，与 PC 相同。Android adapter 为 `df12f9df…`，
实际注入布局 logger0xe0 / level0x160 / state0x228，组合加布局和诊断前缀后的 submitted SHA 为
`15a3f9a4afa6e79bb6d5da1d4f9234fd3699a82c7d2db01210b623356821b4f1`。平台 adapter/完整提交源码不要求与 PC 相同。

- 实际 Hard07-04、胜利；283 个发布样本、281 个带 drain 的屏障观察。原始 seq1–2511 连续无冲突，
  主机基线为 frame30 / time0.9999990463 / seq3。前三笔 frame1/2/3 入场保留在 raw，不倒灌晚基线统计；接纳观察窗为 seq4–2511 共 2508 笔。
- 344 笔 settlement 全完整；12 组父子证明中，10 组 source-null/type5 子爆发后仍存活、2 组子击杀。
  Android scope ID 为 null，不据此归组；逐组以实际线程、父子 Modifier 地址、UID/实例、时钟及 entry/child-return/parent-return 顺序独立匹配。
- 酒神无来源贡献，塔露拉 204 笔元素条。36 笔元素分为 12 个 source-null 条触发与 24 个显式原生元素贡献。
  正盾 13 笔、合计约 3821.369986，其中 10 笔盾余额归零；不推断未观测的全吸收、零吸收或 PC 专属直接治疗 HP 证书。
- 经典 MuMu OnDestroy 原始候选确实存在：末独立 mark、raw fence、ACK 同为 frame8245 / time274.83307123184204 /
  seq2511 / epoch8849 / scope0 / active0，五根与独立样本逐项一致，OnDestroy entered/left/captured 各 1。
  第2412行终局 drain20笔 → 第2413行真实 ACK2511 → 第2421行 raw fence → 第2422行 finished 归档。
  196 个真实 ACK 均与对应 journal 顺序及请求整数一致。独立审计认证本局经典 scope 封口，不赋予 MuMu PC `terminal_read` 或非空尾读能力。
- 观察器卸载/分离成功、无 cleanup error。Collector 自有27381与本次端点27043的 registry 路径清理后，转发表为空；
  未记录每条 ADB remove 的 wire 回复，不虚称逐命令回执。端点服务准备时的 `/proc/<pid>/exe` SHA 与固定 Frida17.26.1 二进制匹配；
  服务按复用策略不主动终止，停止后是否存活未额外取证，不将它误报为仍在采集游戏事件。

`derived-mumu-host-lifecycle-audit-v1.json`、`derived-mumu-shared-trace-audit.json` 分别认证宿主与原生字段范围；
未把 archive.finished 当作无条件原始封口证明。没有 PC producer 首帧证书、PC专用尾部 RPC，也没有卫戍跨帧样本。
本轮使用高密度诊断且关闭 HP 合并，样本数差异和处理耗时不是默认配置 FPS 结论。

#### 验证器复用与原失败保留

旧默认 MuMu 数值入口仍真实失败：治疗原生增量58935.58673095703，对比名义和58935.59994801879；旧入口也漏算24个显式元素贡献。
旧 PC 命名的共享 proof helper 原先只识别 ApplyModifier 结算，故拒绝了 MuMu 24 个真实 `Entity.OnAfterOutputDamage` 证书；
burst 自身仍来自 ApplyModifier，原始方法不改写。两类首版失败回执保留，没有放宽 `near` 或覆写原始日志。

按同一共同生产者的 recipient 合同，只在元素候选中新增完整 owned type5 / 正 UInt32 UID / 合法名义量的输出回调证书识别；
source-null、嵌套父和 burst 方法门槛不扩大。两处配对使用同一谓词，仍要求原 HP/时钟/身份、唯一未消费证书，
混合两张未消费证书、单侧布尔 UID、错额及未知方法均拒绝。冻结24组真实字段只作回归材料，不冒充另一场新实机。

数值工具把既有数学体抽成唯一 `check_native_f32_counters`；PC旧入口只是固定 reader_profile 的兼容包装，CLI指纹门槛不变。
Android reader_profile 只选择真实 `collector.py` 的 `<6f>` 读取证据及范围标签，不另写计算公式。
纯模型 `source_identity_verified=false`，派生调用方先绑定同一份 bytes/hash 的 MuMu 宿主审计并重新通过逐批身份、屏障、质量与连续性校验。

`mumu-shared-generic-reverification-summary.json` 新回执：12个 char/token 命名空间来源完成12700次逐发布分类型精确检查，
逐水位名义补充5849次通过；共享元素36、盾13、嵌套12（非致死10）均无无效证据。
这只支持本观察窗口的数值模型，不证明 Android 闭源更新指令、零起点或本家玩家归属。
通用模型 all 域仍因地图装置500→1000变化失败，显式 owned 域完整报告排除源30个变点、seed500和末值14461.8798828125，
`full_native_row_counter_model_verified=false`。不把此限定域成功包装成全原生源通过。

工具修订后的相关组合实际 **631 passed in 8.10s**，检查中无源码漂移；采集/业务源码/DLL未改变。
三条实时路径现均验证同一处非致死修复，不维护平台专属子结算算法；后续优先实测 PC 卫戍跨帧及技能初态恢复。

## PC 卫戍阶段复验：局部正例与跨 scope 缺口（2026-11-24）

用户正常操作官服 PC 卫戍；本次不操作桌面、不要求中途退赛。观察器为无 Frida 的 Python 3.10.10，
PID/出生为 `[72632, 134399473473640581]`，构建仍为本文唯一已核验的 `cf7956bf…`。
工作树 HEAD 为 `012acf4…`，未提交改动由采集时 124 份文件摘要另行冻结，不能把 HEAD 当成全部候选内容。
证据目录为 `runtime/native-pc/pc-autochess-native-only-live-20261124-063553`。

### 设置、介入与真实结果

- 业务源码使用无诊断前缀的默认组合 `d47a1d832b8033c8dc324e4acf0944f92b8ddabf8f98d3d1c1f8d95b485da164`，
  DLL 仍为 `38e706b3…`。READY 实核 HP 合并开启、HP 上下文 trace 关闭；宿主另开验收 debug 审计和四家范围。
  因宿主准备轮询及高密度日志仍在，本次不当默认产品性能实验。
- 最终日志 77,138,045 字节、74,870 行，SHA-256 为
  `0198496aa272a864fb0cef8e60bfb21d37ac22bb47a39deb3c93ed82f012ef7a`。
  224 个发布样本的出生、当次五根、battle serial、构建、关卡、Q32 与共用窗口重验全部通过。
- 共 16 次真实 opening/closing 跨帧，防守与协防各 8 次；独立快照 anchor 保留，事件专用累计没有被反填。
  但这 16 个窗口的 opening/closing 序号均相同，anchor 也均等其中一端；不认证窗口内新增事件或严格三时钟各异的竞态。
- 技能冷初态恢复取得真实正例：同一 actor 首次接入为 frame5/seq3793，先前 4 笔伤害 2120 保持未分类；
  seq3874 `Character.OnSkillCastSucceed` 后，4 笔已观察技能伤害合计 3443.60015191976。
  后续实际发布 total5563.60015191976、skill3443.60015191976、non-skill0，完整拆分仍为 false。
  对原 5 个样本的确定性 owner 状态机重放与原发布逐字段相等；这是本份实机资料的派生重放，不是第二场实机。

### 未通过：阶段交接与原始内容

各 scope 自身基线之后的有界序列连续，但不能据此认证整个 match：8 次重新建基线后，
源序号已 ACK 到 8593，日志只保存了 8223 个唯一源事件。缺少原文的 370 条分布在
`443–479`、`1666–1713`、`3786–3793`、`3961–4237`。
这些记录没有进入新 scope 的统计；旧 scope 也没有取得可认证的原始尾部。无法恢复其事件类别、金额或归属，
因此不能说“只是少了日志”，也不能无证说丢了 370 笔伤害。

原因位于既有共同宿主基线策略及验收留证：PC Frida 与自研都按 `read(seq, seq)` 建立新 scope，
避免旧前缀重复读取、误归属和累计；这是原有隔离策略，不是自研新增的传输丢包证据。
验收 `PrefixAuditSession` 当时只额外保存首次基线之前的原始队列，后续 rebase 没有对应留证。
不能把产品的空基线直接改为消费整条旧队列，也不能拿新 scope 的 mark 当旧 scope 的终局。

16 个已归档 scope 全部诚实保留 `new_battle / gap / 未取得最终事件水位`，总量与 DPS 仍未知。
另有 61 次 `battle_object_class_mismatch` 读取拒绝（37 次在入场前）；它们不是成功快照，
本份材料不能把所有拒绝归因为故障或正常切换。最终 scope 由主动 STOP 冻结，不是自然终局 finished。

### 停止与后续门禁

21 次实际 ACK 与对应请求整数精确匹配，均晚于相应已接纳样本 journal；ACK 没有 active 字段，不补零。
真实 stop 回复 `active` 为严格整数 0、`native_quiet=true`、无错误，随后 `stopped {}`。
READY 与停止前两次完整模块清单均 223 项、未见 Frida 文件名；Python 无 Frida 发行包且阻止导入。
这只是两个模块边界文件名扫描，不认证全时、改名模块或安全保护兼容。

最终宿主审计见 `derived-autochess-final-host-audit-v1.json`，技能证据见
`derived-a12-cold-recovery-prefix-v2.json` / `.md`（其输入恰为最终日志）。原失败、未知与初版派生报告均保留。
本次不满足“采集无问题后进入性能”的前提：先补每次 rebase 在 ACK 前保存真实队列的**验收留证**，
不注入统计、不造旧阶段终点；再定向观察实际阶段交接。旧缺失内容不能由新代码追补。
如要认证完整业务交接，还需要按真实 scope 与独立最终 fence 接纳旧尾；只有留证改善不等于该能力已实现。

之后的性能观察采用外部同 PID/出生的 CPU 差分、内存、句柄/线程/IO，配合已安装 PresentMon 的有界真实呈现帧时间。
先测默认配置基线，分开战斗、暂停、加载；逻辑帧不是 FPS，不同阶段不作因果 A/B。
目前没有执行该性能测量，也没有接入或构建新便携包，更没有发布启动测试包。

### 留证窄修及检查（发生在上述实机停止之后）

只修改 `acceptance/source_prefix_capture.py` 和对应测试：`PrefixAuditSession` 显式启用后续 rebase 审计，
每次新基线前先读取并 journal 真实的 `(host_committed_sequence, new_baseline]` 原始批次，再调用原空基线接口。
使用已提交游标而非冒充 wire 已确认游标；后者单独记录，ACK 超时不会倒退并重复接纳统计。
原始批次只校验有界连续 safe-int 序号，保留跨 scope 的原始时钟和身份，不套当前 scope 帧范围，也不要求生产者回显不存在的范围字段。
读取、序号或日志写入失败会阻止该基线的 ACK；重复候选照实保留，既不注入 Stats，也不制造 frame0 或最终 fence。
生产端、Frida/自研的空基线、统计公式、scope 隔离与终局门槛均未改变。

新增回归先红 40 failed / 7 passed，再绿；独立审查的四文件组合 161 passed。
根代理相关 12 文件组合最终 **473 passed in 16.05s**，124 份冻结源码在检查期间无漂移；
另有 10 项本地入口 mock 通过，未启动真实采集器或设备，没有 WPF 鼠标交互。
该组合首轮 472 passed / 1 failed，是新建 runtime 控制目录复制了同名检查工具导致 collect-only 导入冲突；
仅将新工具改名 `check_helper_mock.py` 后重跑，未改 pytest 范围或产品断言，原失败回执保留。

新入口控制目录为 `runtime/native-pc/pc-autochess-rebase-control-20261124-070929`，
回执见 `root-focused-tests-v2.json`、`helper-mock-check.json`、`root-preflight.json`。
DLL 六份嵌入源及 124 份采集文件已核验，准备了新输出目录但尚未创建、未采集；用户仍在正常比赛，等待整场结束后再协调。
此修改只修**未来验收的原始留证**，旧 370 条原文无法补回，业务旧尾交接及新的实机结果仍待证。

## 独立便携启动测试候选（2026-11-24）

用户随后明确要求在正常对局期间先制作便携启动包，供其他用户测试启动；这改变工作顺序，
**不撤销**上述性能与卫戍阶段交接限制，也不是公开发布授权。

- 新变体参数为 `--with-native-hooks`，ZIP 后缀为 `-native-hooks-startup-test.zip`；
  不包含 Frida 发行包、Gadget、Android server 或 PC Frida 启动运行时，不改标准发行版默认。
- WPF 编译标记、发布清单及 Python 包级 profile 必须匹配。有效连接固定为官服 PC / 自研 / 空 feed，
  不静默改写已有保存配置；拒绝 MuMu、Frida、合作文件、ArkFramework 和内部回放路线。
  界面明确显示启动测试候选并禁用不可用来源，禁止自动更新或复用旧版本激活。
- 使用本文实测的 DLL `38e706b3…`，不重编译或改变本轮六份原生源码。
  便携加载与打包输入使用同一个 `validate_native_hook_payload` 验证器；包内还核发布清单与关键 Python/JS 字节。
  摘要只证明载荷一致性，不是数字签名或对本机主动篡改的访问控制。
- 「自研监听已就绪，请启动游戏」来自真实 `waiting_for_process`、已建立出生水位和活跃线程，
  此时尚未预载 DLL；新游戏进程出现后才预载，VM 初始化和观察者 READY 仍是独立阶段。
  不借用 Frida 的端口认证状态，也不从等待时间推断就绪。
- 构建必须从隔离候选分支的干净提交进行，不放宽 `candidate_head`，不提交或清理正在测试的开发工作树，
  不打包运行日志、历史、会话配置或认证信息。没有推送或创建公开 Release。
- 仅执行不显示窗口、不发输入的 C# 控件/状态测试，以及包内 Python 无 Frida 导入、离线 worker 握手、清洁解压和摘要检查。
  不在用户正在对局时启动 WPF 交互验收或真实 Hook；这些检查不是用户机器上的实际启动、兼容性或性能结论。

分发说明及构建命令见[Windows 启动测试变体](windows-release.md#官服-pc-自研-hook-启动测试变体)。
实际 ZIP、源提交、摘要和构建回执以构建输出为准；尚未完成的用户启动反馈、包内实机采集、性能与阶段交接继续保留。

### 本地实际构建与静态复核

隔离分支 `codex/native-hooks-startup-test-20261124` 的干净提交为
`7c1a4656b2bdf0cc45c87a537462bd787ad2130b`，包含此前采集候选及本轮打包接线的明确源码快照。
原开发分支 HEAD 和 index 保持不变，没有提交、清理或推送原工作树。
使用本地已核验的 Python 3.12.10 嵌入 ZIP 和已实测 DLL，不重新下载或重编译 DLL。

实际产物位于 `runtime/packages/native-hooks-startup-test-20261124/`：

- `ArkDPS-1.0.0-rc.2026112401-win-x64-native-hooks-startup-test.zip`，111,697,211 字节。
- SHA-256：`af32a75314ad701004c7f954a6aa763194b702e383dc9c1c43cb5fe79d84fc11`。
- 随附 `.zip.sha256` 和 `.zip.acceptance.json`；后者明确 `desktop_started=false`、`game_connected=false`，
  只认证实际包内 Python 导入、manifest 校验及离线 `hello/snapshot/quit`。

隔离提交的相关 19 文件实际 **764 passed / 7 skipped in 45.72s**，检查中无源码漂移、工作树仍干净。
跳过项为本账户无创建符号链接权限 1 项，及本变体不使用、隔离树未构建的旧合作受控 C++ 宿主 6 项；没有将跳过项计为通过。
最终 C# headless 检查 14 组全部通过，包含自研监听状态 52 断言；未显示窗口或发送输入，不替代完整 WPF 启动验收。

独立 ZIP 纯字节审计核对 167 个文件及嵌入标准库 ZIP，97 份代码与干净候选完全相同；
逐文件摘要、DLL/build/六源/三许可及 AMD64 DLL 头均一致。
危险路径、链接、大小写重复、未入清单文件、Frida 载荷、runtime/会话认证配置及用户数据均未发现。
回执目录为 `runtime/packages/native-hooks-startup-test-20261124-inputs/`，包括
`isolated-source-snapshot.json`、`root-isolated-focused-tests.json`、`root-csharp-headless-final.json`、`independent-zip-audit.json`。

构建工具已完成产包及内部检查；外层记录器随后按 UTF-8 读取混合编码日志时失败。
原始日志、实际产物和工具回执均保留，另以 `actual-package-build-recovered.json` 区分记录器失败和真实构建结果，
没有把未保留的子进程退出码补成 0，也没有为此盲目重建包。
这些均不撤销前述性能、实机用户启动、WPF 交互及卫戍交接的未验状态。

### 实际包内解释器独立复核

从真实 ZIP 再次安全解压，使用包内 Python 在无效系统 Python 环境及 Frida 导入阻断下实际运行：
导入、组件清单校验和离线 `hello → snapshot → quit` 全部通过。
MuMu、Frida 后端、绝对 JSONL 路径、Frida 端点及内部回放五类不支持参数均以 code2 明确拒绝，且未创建状态目录。
对三个独立副本分别修改 PC adapter、共同 runtime、AutoChess JS 一个字节，加载门禁均拒绝；原 ZIP 和原解压载荷没有改动。
进程中额外禁止 DLL 加载、线程启动及 socket 创建，检查均未触发这些入口，也没有运行 EXE 或界面。

回执 `actual-embedded-recheck-revised.json` 的 `accepted=true`，SHA-256 为
`cd96d00b42558274c99742d2d26482599e3a825262a9a9d33dd36ac0ee624857`。
首版回执保留：相对文件参数先被 argparse 正确拒绝，但不满足该负测要求的 profile 文案；
修订检查另用合法绝对路径验证 profile 拒绝，未改产品代码或重写首版结果。
当时产物仅可作为**用户启动测试候选**，未取得包内 WPF 实际启动、游戏采集或性能认证。
后续首次同机启动已出现加载确认失败，当前暂停分发，见本文顶部状态；不升级为正式上线通过。

## 启动候选实际失败与有限排查（2026-11-24）

用户启动实际生成的 WPF 包并提供截图。第二次 WPF 运行在 07:34:45 建立启动所有者，
07:35:01.674 进入 loading，07:35:02.879 进入 `process_failed / loader_failed`，`ready_generation=0`。
随后同一运行取得官服指纹并完成 VM 初始化，但观察者只报 `native_not_ready`，没有 `observer_ready` 或 JS 认证成功。
UI 窗口能够启动与实时采集能够启动必须分开；本候选不能分发为已通过采集启动的版本。

### 实际已知与仍未知

- 包内 97 份源码、包 DLL/build 及唯一 staged DLL 均未变化；DLL 仍为 `38e706b3…`，2,176,089 字节。
- 实际 staged DLL 路径为 220 个 UTF-16 单元，同 stem 配置为 221；不能归因于超过传统 260 字符限制。
  DLL 仅依赖 Windows/UCRT，静态导入复核及本机导出检查未发现外置运行库缺件；没有 Zone.Identifier。
- 用户管理员 CMD 执行一次只读模块查询，核实当前 `[31672, 134399505015732153]` 出生实例前后相同、
  process status259、零等待返回258，完整 222 模块里无 exact staged DLL。
  这证明当前组件确未映射，不证明加载瞬间的进程/模块状态；原默认日志没有保存该次 lease/birth 与模块验证分支。
- 用户另执行一次只读公共系统入口对照：同出生、`KERNEL32.DLL` 的 `LoadLibraryW` RVA260032，
  前32字节与本地对照相同。仅限这处当前字节，不能推广为没有任何保护、系统调用或进程策略干预。
- `GetExitCodeThread` 报线程终止状态，不是远端 `GetLastError`；
  原报错合并了未找到模块、返回值不符及进程退出等条件。不能将 `0xffffffff` 解释成“缺 CRT”或已证 ACE 拦截。
  API 语义见 [GetExitCodeThread](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getexitcodethread)
  与 [LoadLibraryW](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-loadlibraryw)。

### 自有子进程对照，不替代官服验收

未再加载当前游戏。另分别由 Python3.10.10、包内 Python3.12.10 使用**原包 loader**，
对新建且以随机回执核实所有权的包内 Python 子进程加载同一 DLL，路径同为220 UTF-16单元。
明确不创建配置文件：DLL 的前认证 worker 因缺配置返回，既不初始化 JS 也不安装 Hook；本对照只核模块映射。
两组都实际找到模块、完整基址低32位与线程返回一致，子进程正常退出0。
新增安全诊断后的 loader 又在独立自有子进程通过同样检查，并取得真实的阶段计时和模块诊断。
这些受控正例排除部分基础包/路径/解释器映射问题，不等于官服兼容、战斗或性能通过。

回执均位于 `runtime/packages/native-hooks-startup-test-20261124-inputs/`：
`actual-startup-failure-host-audit.json`、`actual-startup-failure-knowns-unknowns.json`、
`current-startup-readonly.json`、`current-loader-entry-readonly.json`、
`owned-loader-parity-f310.json`、`owned-loader-parity-embedded312.json`、`owned-loader-parity-f310-current.json`。
只读脚本只接受已报告 PID/出生，不自动寻找新实例；没有读取会话认证 JSON、修改或执行被比较的系统入口。

### 本轮修改只补诊断，尚未修复实际加载失败

拆分未确认映射、已找到模块但返回不符、验证时进程已退等失败；保留真实线程值、process exit/alive、
模块查询三态、基址/低32位、公共 loader 模块/RVA及独立时钟，不把未知补零，也不放宽成功条件。
运行时补齐准备开始、完成、失败和关闭四类安全 trace，失败事实在清理前保存，回调不持有所有者锁。
桌面主日志与默认采集 journal 共用深层白名单；不记录 token、源正文、配置或完整路径。
关键加载线程只入有界队列，不做同步日志 I/O；溢出明确保留诊断不完整计数，不影响加载或清理策略。
原 DLL、共同 JS、加载方法、权限、每出生一次及清理所有权均未改变。
后续只有在检查通过并与用户协调后，才安排一次带这些事实的新启动；不循环重试，不把加日志说成已修好。

### 唯一一次诊断启动及退出结果

内部诊断版 rc.2026112402 来自干净提交 `7204076026e30c62d7c0b1f4a4e5565488b2a7a1`。
相关隔离回归 661 passed / 1 skipped（本账户符号链接权限），构建、包内离线及安全日志字段检查通过；它没有更换 DLL 或加载方式。
用户正常重启一次到大厅，不进战斗。真实 `[3988, 134399520342676916]` 在出生后39.6ms开始准备、1.385s报错；
验证时 `alive=true / exit259`，模块查询成功但 `module_found=false`，线程状态仍为 `0xffffffff`。
这次明确是未找到映射分支，不是模块基址低32不匹配或验证时进程已退。没有第二次加载尝试或观察者 READY。
用户随后从托盘正常退出，journal `native_prepare_closed / worker_stopped` 均 `cleanup_ok=true`，worker 实际退出码0。

有限磁盘基准（未控制缓存、固定顺序、F3.10两轮及实际包内3.12一轮）发现：游戏三文件270.6MB校验约174–189ms，
开发载荷校验53–57ms，便携完整载荷校验992–1126ms，JS组装约1.1–1.5ms。
新增完整包校验仍在发现新游戏进程后的准备路径上；这是明确的主机开销差异，不是测得的游戏帧率或失败因果。
历史两次同DLL普通 CLI 的真实出生至准备完成为0.365s、0.730s；新失败约1.243s才开始加载，
但局次、目录、校验分支、缓存及解释器不同，没有做匹配干预，不能仅凭这些时间认证“提前一定能加载”或 ACE 因果。

报告为 `diagnostic-single-startup-host-audit.json`、`diag-loader-comparison.json`、
`diagnostic-host-validation-cost.json`、`historical-startup-timing-comparison.json`。
该次排查结束时的下一步方案为：将完整载荷校验和不可变字节准备前移到实际监听就绪之前，不删除哈希、出生/路径绑定或清理门禁。
两版包继续不具备采集启动验收，旧包暂停分发；性能与卫戍完整交接门禁没有因此放行。

### 完整校验前置候选：本地实现，待新包实机证据

本轮只调整主机准备顺序，不重编 DLL、不改三段 JS、普通 Windows 加载方法或权限。
`NativeHookRuntime` 在完整游戏构建及便携载荷校验完成后，才建立出生 fence、发布监听状态。
校验期间出生的客户端视作旧实例，不在准备尚未完成时误报“请启动游戏”。
校验失败等待显式重试，不再每25ms反复完整校验；取消、被较新请求覆盖的校验结果不能重新设防。
显式重试不清空已尝试出生集合，也不释放旧观察者或未完成加载器的所有权。

既有载荷校验器捕获同次实际读取并验证的 DLL、清单、三段 JS 字节，组装仍只有 `assemble_probe_source` 一个入口。
当前运行时持有不可变物料及目录、有效环境参数和摘要绑定；新进程准备直接使用其中的 image/source，
不是缓存路径或成功标记后再次读取可被替换的文件。换目录或显式重试重新完整校验，没有全局缓存。
出生后的官服三文件指纹、PID/FILETIME/路径/唯一性、提交门禁及每出生一次仍保留；
移交时仍核当前 JS/环境和 staged/config 摘要。UI 卫戍偏好继续用 typed mark，不重写准备时的程序。
关键准备路径所需 Python 模块已在源模块顶层导入；本改不执行随包构建源码，
也不宣称覆盖其它采集流程的全部 lazy import、签名认证或消除 Windows 按 staged 路径映射的既有替换窗口。

变更控制：范围限于既有 native 准备所有者、唯一 JS 组装器及相应回归；统计业务和平台分流边界不变。
不可变物料是原准备所有者内部值，用于分离出生前的文件验证与出生后的 OS 资源所有权，不引入新的业务后端。
完整哈希、类型/目录/环境、校验中变化、晚到取消、重试覆盖、监听状态和旧资源保留均有定向测试。
本地结果与包摘要写入原 inputs 下新 `prearmed-*` 回执，不覆盖旧失败证据；无实机 READY 之前不解除分发暂停。

### ArkFramework 启动方式静态对照

按用户要求只读核对了 [Mon4terTeam/ArkFramework 的固定提交 a2ae65f](https://github.com/Mon4terTeam/ArkFramework/commit/a2ae65f37282c9ba2d64b759ce918ecaa82be87d)，未构建、运行或复制其实现。
其常规启动同样先等新出生进程、失败不重载同一实例；
[加载器](https://github.com/Mon4terTeam/ArkFramework/blob/a2ae65f37282c9ba2d64b759ce918ecaa82be87d/src/loader/remote_thread_loader.cpp)
分开记录库加载、解析显式启动入口及调用结果，并保存独立的模块值、阶段和远端错误；
[核心](https://github.com/Mon4terTeam/ArkFramework/blob/a2ae65f37282c9ba2d64b759ce918ecaa82be87d/src/core/framework.cpp)
的 DllMain 仅保存模块句柄，初始化由显式启动函数在 loader lock 之外安排，
[Ready/Failed 事件](https://github.com/Mon4terTeam/ArkFramework/blob/a2ae65f37282c9ba2d64b759ce918ecaa82be87d/src/loader/framework_loader.cpp)
另外确认核心初始化，而不是把“模块映射”当作采集就绪。
这些是实际实现差异，不证明它们导致本次失败或构成保护兼容保证；没有直接替换本项目加载方法、套用规避逻辑或同时更改 DLL 入口。
参考源码与 HTTPS raw/git 字节对照留在 `arkframework-startup-reference-audit.json`。

### rc.2026112403：一次成功、随后重启复现失败

用户自行启动并重启游戏，第一次真实 `[66564,134399536195624916]` 在出生后26.6ms开始准备、361.8ms完成。
模块查询成功且找到组件，完整低32返回核对成功；随后真实 `observer_ready`。运行包166个不可变文件摘要全符，DLL/JS仍为38e706/d47。
本局有事件派生统计，但用户明确为**主动退出**，不是正常通关；归档保留 `event_status=gap`、最后state2及尾部拒绝，累计完整性未知。
默认日志没有全量raw/read/ACK，不能据汇总证明完整逐笔或正常终局。

同一 WPF 运行中，用户再次正常重启游戏。旧观察者清理未完成期间，新 `[52688,134399539391473853]` 已于08:32:19.147出生。
旧清理两次 `stopped` 间隔5.215s；08:32:23.150才开始第二次准备，已是出生后4.003s。
这次没有重复完整物料校验，loader本身约196ms，但再次出现 `0xffffffff / module_found=false`，验证时仍存活259，模块查询成功。
首次成功不是稳定修复。先后不是匹配因果实验，不能把4秒当作已证保护窗口或错误值的生成原因。

已确认独立的准入缺口：旧owner清理结束后仍沿用最初 `_armed` fence，把清理盲区内早已启动的新实例补选加载。
ArkFramework [每轮重新建立baseline/fence](https://github.com/Mon4terTeam/ArkFramework/blob/a2ae65f37282c9ba2d64b759ce918ecaa82be87d/src/loader/process_watch.cpp#L154-L184)
可作为这个所有权不变量的对照，但不据此替换我们的加载方式或删掉旧Hook排空。
用户随后正常退出 ArkDPS；真实 native owner `cleanup_ok=true`，但worker保留旧观察者管道232错误，聚合cleanup为false、实际worker退出码1。
这两个结果均保留，不把清理前缓存 `handle_closed=false` 当作新泄漏证据，也不把聚合失败改写为干净退出。

本地窄修保持原生DLL/共同JS/加载方式和权限不变：
- 已持有的旧进程handle、准确PID/FILETIME、前后两次出生查询一致、严格DWORD退出码非259，才可确认该旧进程已结束并免无效远端stop RPC。
  异常、缺handle、身份变化、bool/None/越界值或259都不能冒充死亡；活进程仍必须原active0/native_quiet证明。
  本地pipe的OVERLAPPED取消完成和loader.close不能省略或吞错。依据 [GetExitCodeProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getexitcodeprocess)。
- 旧lease确已结束且全部旧资源释放后，才一次性提交fresh fence；清理期间已经出生的实例只报需要重启，不再迟到补加载。
  不设置猜测的毫秒窗口、不清attempted集合、不重复物料校验；未知存活状态继续保留owner。
- 四类既有安全trace增留真实armed FILETIME、watch代次和material就绪布尔；缺失不补零，不增加同步加载线程日志I/O。

回执为原inputs下 `prearmed-live-startup-audit.json`、`prearmed-live-artifact-audit.json`、
`prearmed-live-battle-prefix-audit.json`、`prearmed-second-startup-audit.json`、`prearmed-restart-admission-review.json`。
这些真实结果和新离线修复分开登记；修后仍需新的受协调实机启动/退出/再启动证据，性能、正常终局和分发继续暂停。

### rc.2026112404：重启正例与卫戍可靠采样过期

同包真实两个出生实例71780、53724均映射确认、返回值核对及观察者握手成功，出生至准备完成约0.342s、0.406s。
第二次记录真实 `watch_generation=2` 和fresh fence，旧观察者 `stopped.cleanup_ok=true`，未复现此前5秒失败清理等待。
这只是当前同机的两个正例，不保证其它启动环境或长期稳定性。

用户明确过期提示为“等待下一次可靠采样”，不是“等待 worker 响应”。默认日志已有新sample/历史，但不足以定位每次拒绝原因。
用户开启调试后，冻结保存了一个有界实际前缀（8,371,407字节、949完整行；SHA `0387e1136908fbd9a1cbe2ca795aac4d3dd84acb958574c813684670a0a449aa`）。
它包含local模式交战sample80条及34条重试汇总，后者有 `frame_or_lifecycle_changed` 和事件来源时钟变化；这些是观察记录，不等于桌面全部接纳或源全序证明。
670条 `preparing / empty_roster` 均为phase3 PREPARE，是正常准备流程，不能靠放宽阶段门禁让它们进入金额统计。
历史063553实机样本实际是all模式、独立高速读循环，不能据其元数据中的旧默认布局声称已覆盖local模式的WPF默认采样。
私有原文只留在不分发的runtime inputs；派生回执 `rc2404-autochess-stale-debug-prefix-audit.json` 不输出账号或昵称。

代码对照发现自研路径漏接两项既有采样能力：
- 自研observer未声明 `native_autochess_identity_sampling`，因此无法进入共享的 `native_identity_sampling_mark → NativePcCollector.read_lifecycle_with_event_identity`。
  新声明只授权现有入口：opening必须已绑定、available且phase4/5/6，现场阶段/时钟/根再次相符才省去冗余roster/presence/actions。
  actor记录、五根、Q32、session、严格新基线及后续窗口守卫不变；partial、未知或失败mark不借这个flag变为已证。
- 桌面有限时钟重试原先硬限Frida。自研默认运行时现在也可显式接入同一逻辑，且核对当前native startup owner、READY、空feed、非自定义/ArkFramework、精确contract和cap。
  仍只处理未消费观察的typed native busy；最多3次的原40ms准入预算和5ms间隔、owner/revision/cancel检查不变。
  普通ObserverReadRetry和看似时钟变化的字符串不获加速；Frida原分支不额外收紧。

本次不改2秒过期阈值、不刷新旧样本采集时间，不伪造归属/补零、不修改DLL或三段JS、也不改变phase3与金额/ACK处理。
两个接线缺口的离线正反例分别验证后再组合；它们是有证据的功能遗漏，但尚未证明是本次持续stale的全部原因。
修后必须在local模式WPF实机比较可靠样本持续推进、同作用域有效覆盖和终局；调试日志开销不能拿来评分正常配置的性能。

### rc.2026112405 卫戍防御实测与采样修复

2026-11-24 官服 PC 的本家模式记录中，第 11 回合防御有三个可重放的样本，帧号为 3712、3725、3744。
原始记录 SHA-256 为 `aa5c3fd1a65375a442b9ba7b5df121ba614739c153f2b5e7d730b2a83b18adbc`。
三帧各有 34 条角色归属、本家 8 条干员记录，读到的事件序号都停在 3622，批次均为空。
从末个样本到末次详细重试至少相隔 66.516 秒。旧日志缺少逐次拒收位置和这段时间的命中明细。

离线复现覆盖了完整名单每次读取都跨帧的情况。旧实现会一直重新解析名单。
PC 现在只保留待复核的候选名单，下一次采样须重新通过两次字段比对和原有战局、时钟校验才能使用。
成员或回合变化会触发重新解析。采样起点与独立快照相差一帧时，已核对战局身份的交战采样可以省去重复编队读取。
这两处改动都在事件读出和确认之前。新版的实际防御刷新间隔、实战伤害和卡顿仍需同场景复测。

2026-11-24 实测 `9de95da` 仍出现过期。调试记录保留了 19 个有效防御样本，事件批次均为空；
采样重试同时出现了 `mark / clock_changed`、`read / clock_changed` 和独立快照跨帧。
本地归档还记录到同一名单含 16 个宿主未能确认的召唤物。缺失的事件金额无法由这段记录重建。

PC 交战采样跨帧后，把候选名单的第一次复核提前到下一次标记或批次读取的时钟窗口之前。
窗口内仍逐项完成第二次复核，战局身份、时钟和结算范围校验继续决定能否提交。
当读取已成功、唯一的不确定项是已比对字段所确定的宿主缺失或冲突时，这些字段也可以参加下次复核；
对应召唤物继续标记为未知。复核之间改变宿主会使该次候选失效，随后重新读取归属。
离线回归覆盖连续跨帧、16 个未归属召唤物、保留队列的重复读取及确认。本次修改的实战刷新间隔待验证。

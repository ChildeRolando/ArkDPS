# PC 自研事件后端内部候选

内部候选在独立的 Windows x64 进程中管理事件缓存、序号和终局标记。Python 通过版本化的标准输入输出协议调用该进程，并由现有 PC 事件契约核对每次事件批次和受控快照。统计、卫戍归属、战绩和 WPF 复用产品链路。

桌面和 CLI 共用 PC 来源选择。采集会话释放自己的观察者后才能切换来源。外层启动组件清理失败会保留所有权，供下次清理重试。卫戍省略旧编队见证的读取仅在已声明能力的观察者提供可核验标记、且原生采样的关卡、时钟和战局根均匹配时启用。其余采样保留旧编队见证。内部候选的受控快照独立声明战局及卫戍范围，以便测试拒绝不匹配的事件证据。

## 从源码验证

需要 Windows x64 的 Python、支持 C++17 的 MinGW-w64 `g++` 和构建 WPF 所需的 .NET 10 SDK。内置场景都使用自有测试数据。

```powershell
python native_pc/internal_event_host/build.py
python app.py --internal-capture normal --state-dir .\internal-normal
python app.py --internal-capture autochess --state-dir .\internal-autochess
```

使用新建的独立状态目录。受控采样写入该目录的战绩和诊断记录。观察者握手代表传输已就绪，实际覆盖仍由同帧快照、事件和终局证据决定。

## 独立便携包

从已提交的干净工作树构建内部候选。打包器从同一提交构建宿主，并用解压后的包运行验收。

```powershell
python tools/package_windows.py --internal-events
```

解压后在包根目录运行桌面或 CLI。场景可选择 `normal` 或 `autochess`。

```powershell
.\ArkDPS.Desktop.exe --offline --internal-capture autochess --state-dir "$PWD\internal-desktop"
.\python\python.exe -I -B .\collector\app.py --internal-capture normal --state-dir "$PWD\internal-cli"
```

包清单记录事件宿主的协议、来源版本、路径和摘要。内部候选使用无设备模式与独立状态目录。命令行回执和采集日志记录输入摘要，战绩保存在相同状态目录下的 `history`。

普通关和卫戍的细分矩阵使用自有记录覆盖可控的事件、快照和错误组合。ArkFramework V2 的冻结片段只用于核对两边都有的逐笔字段。官服实时接入与各游戏构建的兼容性需要独立的实时来源证据。

## 离线对账现有 Frida 探针

构建宿主后，从仓库根目录回放产品详细采样日志中的单局连续片段：

```powershell
python -m acceptance.replay_pc_probe_batches 'D:\Captures\battle-segment.jsonl'
```

命令只读磁盘上的日志，不启动或连接游戏。它将原有的 PC Frida 水位及逐笔记录送入独立 C++ 宿主，逐批读回比较并确认。水位与有界读取必须对同一战局、精确时钟及共同质量字段达成一致；只存在于读调用的诊断及基线技能见证不在这次传输对账范围。不会根据事件反推中间帧水位或拆分超过宿主上限的旧批次。

回执的 `unverified_through` 是未回放的起始游标；`terminal_verified` 仅表示核验并读回了原有的同水位终局边界，不代表完整战局、统计覆盖或来源真实性。没有原始终局边界就不封口。该命令不生成可冒充实时来源的 JSONL，也不替代产品统计回放。

2026-11-24 的离线对账使用一段已留档的 PC 卫戍战斗：73 个采样水位、38,778 条逐笔记录（含 817 条伤害结算、125 条治疗、105 条技能开始/结束）；全部原样读回。片段首条序号为 44,580，末条为 83,357，没有终局证据。终局路径另由现有生产探针脚本在自有 Node 夹具中生成边界进行验证，不是新实机证据。

## PC 实时事件内核

2026-11-24 新增[非 Frida 实时 Hook 实验来源](native-pc-native-hooks.md)：从源码运行且不填写合作文件时加载新组件。
它与下述合作 JSONL 路径不同：已在官服取得 1480 条完整源前缀同局对账，无 Frida 独立局取得真实非空终局尾笔；
另一次独立局收齐 2406 条源事件并验证 6 组嵌套击杀，但 10 笔非致死元素父证明仍未知。
源序号收齐不等于全局语义完整；不属于已验收便携候选。最新修复、实机待证和发布门禁见该实验来源文档。

「设置 → 连接 → PC 事件内核」可选「Frida」和「自研」。Frida 是旧配置和新安装的默认值。选择合作来源时，填写其正在写入的本地 JSONL 的绝对路径，再应用设置；留空为新的实验 Hook 来源。MuMu 使用自己的观察者设置。关闭逐次事件时，PC 只读采样，所选内核留在配置中。

自研模式按流读取已经发布的记录。源缺失、证据不一致或断流时保留原生累计，受影响的事件指标显示未知。改选内核时先释放旧会话及其资源，清理失败会阻止切换。每份输入关联一个游戏进程和一场战斗。开始新场战斗时，选择生产者为该局创建的新文件并重连。

并列候选同时包含 Frida 组件、自研 Windows x64 宿主和包内 CLI，使用 `--with-native-events` 从干净提交构建。

```powershell
python tools/package_windows.py --with-native-events
.\python\python.exe -I -B .\collector\app.py --platform native_pc --native-game-root 'D:\Games\Arknights' --native-pc-event-backend native --native-pc-event-feed 'D:\EventFeed\battle.jsonl' --capture-seconds 10
```

从源码运行时先执行 `python native_pc/internal_event_host/build.py`，再用 `python app.py` 启动 WPF 或运行相同的 CLI 采集参数。CLI 参数只覆盖当次启动，设置页「应用」才写入偏好。

### 合作生产者输入

使用 UTF-8 JSON Lines，每个对象以换行结束。首行必须在连接前写完，包括 `format: "arkdps-pc-events"`、整数 `version: 1`、`producer`、正整数 `producer_version`、`process_instance: [pid, creation_filetime]` 和 `source_build`。

后续每行提供 `mark` 和 `events`，末行可同时提供 `terminal_mark`。生产者在一帧的事件写完后发布水位。标记包括 `frame`、`game_time`、`sequence`、`epoch`、`active`、`native_frame`、十进制字符串 `native_fixed_time`（Q32.32）、`native_generation`、`native_process_pid`、`native_stage_id` 和 `native_identity`。同时附上支持的事件覆盖字段，卫戍事件附交战范围和归属证据。

序号连续，水位单调。停帧时须在 5 秒内重复发布同一水位作为心跳，终局封口后停止写入。读取器限定记录和文件大小，校验进程实例、战局及事件前缀，并检测读到的文件替换或截断。未写完的行等待后续发布，持续无法与快照对齐时回退到累计。

ArkDPS 将生产者标记与独立采样的进程、构建、战局、关卡和逻辑帧配对，再接纳事件前缀并确认。同一事件宿主管理有界缓存，已有统计层解释伤疗和卫戍归属。ArkFramework V2 冻结记录的交集仍可用于对照，官服实时逐次齐平需要能提供上述证据的实时生产者和实机回执。

### 接入已授权的遥测源

Python 调用方可使用 `native_pc.live_event_producer.CooperativeEventWriter`。每局排他创建一个新文件；在上游确认一帧已结算后调用 `publish(mark, events)`，停帧且上游仍健康时调用 `heartbeat()`，收到真实终局后以 `publish(mark, events, terminal=True)` 封口。已发布帧不得补写事件。迟接且没有历史前缀时，首个水位可以带已有序号和空事件列表；这不是此前完整覆盖的证明。关闭、出错或断开都不会合成终局。

非 Python 调用方可启动下面的工具，将 UTF-8 JSON Lines 直接写入其标准输入：

```powershell
python -m native_pc.live_event_bridge --help
.\python\python.exe -I -B -m native_pc.live_event_bridge --help
```

后一条适用于 `--with-native-events` 便携候选。启动参数填写新输出路径和**上游报告**的生产者名称、版本、进程实例及构建。输入为 `{"mark":…,"events":[…]}`；心跳为 `{"heartbeat":true}`；最终完整帧附加 `"terminal":true`，工具写完终局后退出。工具不会因上游沉默自行生成心跳；输入提前结束或无效时以非零状态退出，已发布的前缀保持未封口。写入后在 ArkDPS 选择该文件，不需要修改统计口径。

这个写入器和桥接工具只传递上游已有的事件及证据，不从游戏取得事件，不补造身份、序号、时钟、结算、质量或卫戍归属字段。目前验证使用自有测试数据和独立受控快照。尚无已确认可接入本协议的官服授权逐次遥测接口、脱敏样本及实机回执，不能将通过自研宿主联调当作官服接入完成。

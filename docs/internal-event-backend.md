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

## PC 实时事件内核

「设置 → 连接 → PC 事件内核」可选「Frida」和「自研」。Frida 是旧配置和新安装的默认值。选择「自研」后，填写合作生产者正在写入的本地 JSONL 的绝对路径，再应用设置。MuMu 使用自己的观察者设置。关闭逐次事件时，PC 只读采样，所选内核留在配置中。

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

序号连续，水位单调。停帧时须在 5 秒内重复发布同一水位作为心跳，终局封口后停止写入。读取器限定记录和文件大小，拒绝旧进程、旧战局、文件替换、截断和跳号。未写完的行等待后续发布，持续无法与快照对齐时回退到累计。

ArkDPS 将生产者标记与独立采样的进程、构建、战局、关卡和逻辑帧配对，再接纳事件前缀并确认。同一事件宿主管理有界缓存，已有统计层解释伤疗和卫戍归属。ArkFramework V2 冻结记录的交集仍可用于对照，官服实时逐次齐平需要能提供上述证据的实时生产者和实机回执。

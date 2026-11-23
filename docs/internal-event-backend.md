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

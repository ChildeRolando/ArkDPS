# PC 自研事件后端内部候选

内部候选在独立的 Windows x64 进程中管理事件缓存、序号和终局标记。Python 通过版本化的标准输入输出协议调用该进程，并由现有的 PC 事件契约核对每次事件批次和受控快照。统计、卫戍归属、战绩和 WPF 复用产品链路。

## 从源码验证

需要 Windows x64 的 Python、支持 C++17 的 MinGW-w64 `g++` 和构建 WPF 所需的 .NET 10 SDK。内置场景都使用自有测试数据。

```powershell
python native_pc/internal_event_host/build.py
python app.py --internal-capture normal --state-dir .\internal-normal
python app.py --internal-capture autochess --state-dir .\internal-autochess
```

使用新建的独立状态目录。受控采样写入该目录的战绩和诊断记录。观察者握手代表传输已就绪，实际覆盖仍由同帧快照、事件和终局证据决定。

## 独立便携包

从已提交的干净工作树构建内部候选。构建会在包内 Python 运行两个场景。

```powershell
python tools/package_windows.py --internal-event-host .\runtime\native-pc\internal-event-host\ArkDPS.NativeEventHost.exe
```

解压后在包根目录运行桌面或 CLI。场景可选择 `normal` 或 `autochess`。

```powershell
.\ArkDPS.Desktop.exe --offline --internal-capture autochess --state-dir .\internal-desktop
.\python\python.exe -I -B .\collector\app.py --internal-capture normal --state-dir .\internal-cli
```

包清单记录事件宿主摘要。内部候选只运行受控事件路径，与标准发行和更新分离。受控验收的结果仅说明指定输入和协议能通过现有统计链路。官服实时事件来源与 `155010` 兼容性需要独立证据。

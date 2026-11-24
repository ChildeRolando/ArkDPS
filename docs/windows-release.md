# Windows WPF 便携包构建与发布验收

产品 UI 只有 WPF（`desktop/ArkDPS.Desktop`）。Tk 面板已封存，其构建、自检与 ARK-14 发布记录整体归档在
[`docs/archive/legacy-tk/windows-release-ark14.md`](archive/legacy-tk/windows-release-ark14.md)，不再产出、不得运行，
也不得作为当前候选的验收证据。决定与日期见 [ADR 0005](adr/0005-seal-legacy-tk.md)。

## 状态与权威来源

Windows x64 便携包的已发布版本和各版说明见 [GitHub Releases](https://github.com/ChildeRolando/ArkDPS/releases)。

便携包的清单位于 `metadata/release.json`。清单记录发行版本、源提交 `head`、载荷文件的逐文件 SHA-256、协议和运行时版本。启用应用内更新的标准包还包含 `update_protocol: 1`。复核时记录源提交、ZIP 摘要、命令、原始日志和截图。

## 下载与更新

适配环境、解压与启动步骤见 [README](../README.md#下载与启动)。可用 `Get-FileHash <zip> -Algorithm SHA256` 对比随包发布的 `.zip.sha256` 文件。验证未发布的候选时，从对应提交的 CI 运行下载 `windows-wpf-candidate-<sha>`。

支持应用内更新的标准便携版启动后会在后台检查并下载新版，设置页也提供「检查更新」。下载完成后，用户可点击「重启并更新」确认切换。下载使用 GitHub Release 的完整 Windows x64 ZIP，先按 GitHub API 提供的 SHA-256 校验 ZIP，再按包内清单校验解包后的文件。稳定版只接收稳定版本，预发行版可接收更新的预发行版或稳定版。手动下载入口仍指向对应的 GitHub Release。

### 观察者

标准便携包内置 PC ArkPie 组件，以及 [Frida 17.26.1](https://github.com/frida/frida/releases/tag/17.26.1) 的 Windows 客户端、PC Gadget 与供 MuMu 12 x86_64 Android 使用的设备端服务。观察者默认开启。MuMu 在选定实例、游戏版本通过校验且游戏完成初始化后，自动准备并启动包内服务；已有可用服务时复用。官服 PC 需要先启动 ArkDPS，再正常启动游戏，在设置页确认自动就绪。首次准备使用本地包内文件，无需联网或手动下载。

如果服务准备失败，设置页的连接诊断会显示原因。实时页仍可显示伤害与治疗累计，缺少事件证据的指标显示为未知。修改连接设置后可点击「应用设置并重连」。[事件采集与验收](event-capture.md)

### 升级与回退

rc.14 起，程序的 Windows manifest 固定为 `requireAdministrator`，取消 UAC 提示不会启动。尚未要求管理员权限的旧根启动器使用不同的进程创建方式，不能可靠启动新版，所以本轮须手动下载完整 ZIP，按下面步骤迁移，并从新目录启动。单次以管理员身份运行旧版完成应用内更新，不会替换旧根启动器，不能消除以后的权限启动失败。

应用内更新沿用原来的 `ArkDPS.Desktop.exe` 启动入口。新版本使用独立的程序目录，继续读写原目录的 `collector/runtime/`。新版本启动校验失败时，程序恢复至上一个可用版本。

首次从旧版迁移或需要手动更新时，退出旧程序并备份 `collector/runtime/`。把新 ZIP 解压到新目录，复制备份的整个 `collector/runtime/` 后启动。需要手动回退时可重新启动保留的旧目录。布局与连接格式迁移由现有 store 处理，损坏或未来版本的诊断见[设置与布局](settings-and-layout.md)。游戏升级到非 2.7.91 时拒绝实时统计，仍可浏览已有历史。

## 构建候选

正式包从 `main` 的干净已提交工作区构建。明确标注的独立实验变体可从隔离候选分支的干净提交构建；
仍须通过相同的 HEAD 精确及工作区干净校验，不能把开发目录的未提交改动直接拼入分发包。
Windows CI 使用 CPython 3.10.10 与 .NET 10 SDK。

```powershell
python -m pip install -r requirements-windows.txt -r requirements-test.txt
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests -q --assert=plain
python tools/package_windows.py --output runtime/packages
```

构建器先验证「HEAD 精确 + 工作区干净」，构建并核验 ArkPie DLL，再 `dotnet publish` 自包含 WPF 单文件、解包固定 SHA-256 的
CPython 3.12.10 embeddable 运行时、下载并核验固定的 Frida 17.26.1 wheel、PC Gadget 与 Android x86_64 服务端，核验生成 EXE 的管理员 manifest，然后按 `BACKEND` 白名单复制
worker 源码与资源。`BACKEND` 不含任何 Tk 模块，构建结束前还会断言包内 manifest 不存在
`tkinter` / Tcl / Tk 文件，并用包内 Python 执行 `import desktop_bridge` 且断言 `"tkinter" not in sys.modules`。

已发布的便携包使用 `ArkDPS-<版本>-win-x64.zip` 命名，RC 在 GitHub 标为预发行。复现某个发布包时，切到该版本的构建提交，再按该提交的构建器参数与清单格式操作。构建器可用 `--version` 指定包版本。

`requirements-test.txt` 单独固定 pytest 与 Pillow（Python 包名 `PIL`）：后者仅用于测试中独立解码 PNG，
不是产品运行时依赖。`requirements-windows.txt` 只保留事件客户端 pin。

### PC ArkPie 事件后端

标准 Windows x64 包同时包含 Frida 与 ArkPie 事件后端。ArkPie 后端使用 `ArkDPS.NativeEvents.dll` 执行共用探针，
设置页可直接选择「ArkPie」。若遇“检测到环境存在异常”报错，选择此选项，应用设置后按提示重启游戏。
后端选择与游戏目录保存在既有连接配置中，更新继续沿用设置和战绩。

构建标准包时会自动构建 ArkPie DLL。也可显式提供已构建的组件：

```powershell
python native_pc/native_event_runtime/build.py
python tools/package_windows.py --native-hook-dll runtime/native-pc/native-event-runtime/ArkDPS.NativeEvents.dll --output runtime/packages
```

DLL 的相邻 `.build.json`、六份运行时源码及 QuickJS、MinHook、nlohmann-json 三份许可均随包分发。
`metadata/release.json` 的 `native_pc_native_hooks` 记录 provider、协议、路径、摘要及受支持的客户端构建。
组件在加载前核对发布清单、构建清单和实际文件；客户端构建匹配后才启用观察。
停止观察会撤下 Hook，DLL 保留映射至游戏正常退出。

ArkPie 后端于 2026-11-24 获得用户实机验收通过，包含卫戍实际输出和流畅度反馈。
具体构建、历史对账与实测范围见[PC ArkPie 事件后端](native-pc-native-hooks.md)。
构建检查继续核对载荷摘要、包内 Python 和 worker 握手；交互式 WPF 检查由隔离 CI 执行。

## 验收

日常开发按 [开发代理指南的本地验收规则](../AGENTS.md#运行与验收) 选择专项检查。下面的命令用于交互式验收，优先在隔离的 Windows CI 中运行。`--offline` 和独立的 state 目录只隔离游戏连接和数据，脚本仍会占用当前 Windows 桌面的鼠标。

```powershell
python acceptance/verify_wpf.py --output runtime/wpf-source
python acceptance/verify_wpf.py --bundle runtime/packages/<解压目录> --output runtime/wpf-bundle
python acceptance/verify_wpf_package.py runtime/packages/<构建包>.zip --output runtime/package-validation
```

`verify_wpf.py` 驱动真实 WPF 与真实 stdio 握手，覆盖合成历史、双主题、HUD 几何/锁定/透明度恢复、
历史页进入自动刷新与归档后自动更新、事件不可用原因与恢复入口、PNG 导出和新进程重启恢复。
`verify_wpf_package.py` 在新目录验证清洁解压、载荷摘要、使用包内 Python/.NET 运行、v1 → WPF v3 布局迁移和 WPF 重启恢复。
WPF 验收用模拟 GitHub 响应检查版本、下载入口和网络错误。

`verify_hot_update.py <当前ZIP> <新版ZIP> --output <目录>` 使用两个实际构建的标准便携包。在模拟 GitHub 响应下验证正常更新、经原入口再次启动、新版启动失败或旧版退出失败时回退、切换中断后仍启动旧版，以及拒绝摘要不匹配的 ZIP。检查包括运行版本、worker 握手、设置与战绩、原程序文件和新版载荷摘要。CI 中两份 ZIP 均由打包器从已提交的工作区构建，第二份使用更高的验收版本号。

**这些都是明确标注的合成数据。** 私有窗口接收点击不是 MuMu/游戏接收点击；物理托盘图标不可定位时回执明确
`NOT VERIFIED`。混合 DPI 与实机项仍以独立验收为准，不得把离线回执当作实机通过证据。

## CI

`.github/workflows/windows.yml` 在 Windows runner 上：安装固定依赖 → `tools/prepare_windows_ci.py` 设定隔离桌面 →
全量 pytest → 构建 WPF → 生成便携候选 ZIP → `verify_wpf.py` → `verify_wpf_package.py` →
用第二份仅供验收的 ZIP 运行 `verify_hot_update.py` → 上传私有候选 artifact。第二份 ZIP 留在 CI 工作目录，候选 artifact 只包含正式候选 ZIP 及验收回执。

## 许可与分发边界

完整本地许可和上游来源见 `LICENSE` / `THIRD_PARTY.md`，非 MIT，包含非商业限制。
头像仍属鹰角；不能将其学术/交流来源说明解释为任意商业分发授权。候选随包保留 .NET、Python、Frida 与
typing_extensions、QuickJS、MinHook 和 nlohmann-json 的许可文本到 `licenses/`。Tcl/Tk 与 PyInstaller 文本随封存归档保留，不再进入分发。
ADB、MuMu、游戏客户端和 MAA 由各自的渠道提供。未签名程序可能被 SmartScreen 提示。不要指导用户关闭安全防护。

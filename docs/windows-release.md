# Windows WPF 便携包构建与发布验收

产品 UI 只有 WPF（`desktop/ArkDPS.Desktop`）。Tk 面板已封存，其构建、自检与 ARK-14 发布记录整体归档在
[`docs/archive/legacy-tk/windows-release-ark14.md`](archive/legacy-tk/windows-release-ark14.md)，不再产出、不得运行，
也不得作为当前候选的验收证据。决定与日期见 [ADR 0005](adr/0005-seal-legacy-tk.md)。

## 状态与权威来源

Windows x64 便携包的已发布版本和各版说明见 [GitHub Releases](https://github.com/ChildeRolando/ArkDPS/releases)。

便携包的清单位于 `metadata/release.json`。清单记录发行版本、源提交 `head`、载荷文件的逐文件 SHA-256、协议和运行时版本。复核时记录源提交、ZIP 摘要、命令、原始日志和截图。

## 下载与更新

适配环境、解压与启动步骤见 [README](../README.md#下载与启动)。可用 `Get-FileHash <zip> -Algorithm SHA256` 对比随包发布的 `.zip.sha256` 文件。验证未发布的候选时，从对应提交的 CI 运行下载 `windows-wpf-candidate-<sha>`。

从 Releases 下载的便携版在设置页提供「检查更新」。启动后会在后台检查一次，发现新版时可点击「查看下载」打开对应的 GitHub Release。

### 观察者

便携包内置 **Frida 17.26.1 客户端**。设备端服务需要另行准备。从 [Frida 17.26.1](https://github.com/frida/frida/releases/tag/17.26.1) 获取与模拟器 Android 系统架构匹配的 `frida-server-17.26.1-android-*.xz`，解压后放到模拟器的 `/data/local/tmp/ark_dps/frida-server-17.26.1`，并赋予执行权限。

在连接设置开启观察者并应用设置。ArkDPS 会尝试启动已准备的服务并建立转发，默认主机端口为 `27043`，设备端口为 `27864`。端点不可用时，实时页仍可显示伤害与治疗累计，缺少事件证据的指标显示为未知。[事件采集与验收](event-capture.md)

### 升级与回退

退出旧程序并备份 `collector/runtime/`；把新 ZIP 解压到新目录，复制备份的 `collector/runtime/` 后启动。
只迁移数据，不混用两版 worker；保留原备份可回退。布局与连接格式迁移由现有 store 处理，
损坏/未来版本诊断见[设置与布局](settings-and-layout.md)。游戏升级到非 2.7.91 时拒绝实时统计，
诊断说明实际/期望版本；仍可浏览已有历史。

## 构建候选

从 `main` 的干净已提交工作区构建。Windows CI 使用 CPython 3.10.10 与 .NET 10 SDK。

```powershell
python -m pip install -r requirements-windows.txt -r requirements-test.txt
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests -q --assert=plain
python tools/package_windows.py --output runtime/packages
```

构建器先验证「HEAD 精确 + 工作区干净」，再 `dotnet publish` 自包含 WPF 单文件、解包固定 SHA-256 的
CPython 3.12.10 embeddable 运行时、下载并核验固定 Frida 17.26.1 wheel，然后按 `BACKEND` 白名单复制
worker 源码与资源。`BACKEND` 不含任何 Tk 模块，构建结束前还会断言包内 manifest 不存在
`tkinter` / Tcl / Tk 文件，并用包内 Python 执行 `import desktop_bridge` 且断言 `"tkinter" not in sys.modules`。

已发布的便携包使用 `ArkDPS-<版本>-win-x64.zip` 命名，RC 在 GitHub 标为预发行。复现某个发布包时，切到该版本的构建提交，再按该提交的构建器参数与清单格式操作。构建器可用 `--version` 指定包版本。

`requirements-test.txt` 单独固定 pytest 与 Pillow（Python 包名 `PIL`）：后者仅用于测试中独立解码 PNG，
不是产品运行时依赖。`requirements-windows.txt` 只保留事件客户端 pin。

## 验收

```powershell
python acceptance/verify_wpf.py --output runtime/wpf-source
python acceptance/verify_wpf.py --bundle runtime/packages/<解压目录> --output runtime/wpf-bundle
python acceptance/verify_wpf_package.py runtime/packages/<构建包>.zip --output runtime/package-validation
```

`verify_wpf.py` 驱动真实 WPF 与真实 stdio 握手，覆盖合成历史、双主题、HUD 几何/锁定/透明度恢复、
历史页进入自动刷新与归档后自动更新、事件不可用原因与恢复入口、PNG 导出和新进程重启恢复。
`verify_wpf_package.py` 在新目录验证清洁解压、载荷摘要、使用包内 Python/.NET 运行、v1 → WPF v3 布局迁移和 WPF 重启恢复。
WPF 验收用模拟 GitHub 响应检查版本、下载入口和网络错误。

**这些都是明确标注的合成数据。** 私有窗口接收点击不是 MuMu/游戏接收点击；物理托盘图标不可定位时回执明确
`NOT VERIFIED`。混合 DPI 与实机项仍以独立验收为准，不得把离线回执当作实机通过证据。

## CI

`.github/workflows/windows.yml` 在 Windows runner 上：安装固定依赖 → `tools/prepare_windows_ci.py` 设定隔离桌面 →
全量 pytest → 构建 WPF → `tools/package_windows.py` 产出唯一候选 ZIP → `verify_wpf.py` → `verify_wpf_package.py` →
上传私有候选 artifact。CI 不再构建 `ArkDPS.exe`，也不再运行 `--self-test` 等 Tk 路径。

## 许可与分发边界

完整本地许可和上游来源见 `LICENSE` / `THIRD_PARTY.md`，非 MIT，包含非商业限制。
头像仍属鹰角；不能将其学术/交流来源说明解释为任意商业分发授权。候选随包保留 .NET、Python、Frida 与
typing_extensions 的许可文本到 `licenses/`。Tcl/Tk 与 PyInstaller 文本随封存归档保留，不再进入分发。
无 ADB、MuMu、游戏客户端、MAA 或 Frida server 随包分发。未签名程序可能被 SmartScreen 提示；
不要指导用户关闭安全防护。

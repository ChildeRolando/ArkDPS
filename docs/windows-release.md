# Windows WPF 便携包构建与发布验收

产品 UI 只有 WPF（`desktop/ArkDPS.Desktop`）。Tk 面板已封存，其构建、自检与 ARK-14 发布记录整体归档在
[`docs/archive/legacy-tk/windows-release-ark14.md`](archive/legacy-tk/windows-release-ark14.md)，不再产出、不得运行，
也不得作为当前候选的验收证据。决定与日期见 [ADR 0005](adr/0005-seal-legacy-tk.md)。

## 状态与权威来源

当前版本是 **1.0.0-rc.1 / Windows x64 便携版候选**，不是 MSI 安装器，不注册服务、不自动升级、无代码签名。
目标环境为 Windows 10/11 x64、MuMu Player 12、官方 Android 游戏 **2.7.91**、Root 开启。
Windows 10、干净 Windows 安装、多开、其他游戏版本/模拟器及原生 PC 未因构建成功而获得认证。
本工单仅准备候选；不得创建 GitHub Release、公开下载页或发布 tag，实际对外发布须另行授权。

产物身份以包内 `metadata/release.json` 为准。它记录发行版本、源提交 `head`、载荷文件的逐文件 SHA-256、
.NET SDK 与运行时、协议版本及内置 Python/Frida 版本。`build-manifest.json` / `artifact.json` 属旧的
PyInstaller 候选，已随归档停用。独立验收必须同时固定候选 SHA、ZIP SHA-256、命令、原始日志/回执和截图，
不能用以前候选的 PASS 冒充。

## 用户快速开始（便携产物）

1. 取 CI 上传的 `windows-wpf-candidate-<sha>` artifact，用 `Get-FileHash <zip> -Algorithm SHA256` 对比 `.sha256` 文件。
2. 解压到用户可写的新目录，例如个人应用目录；不要放进 Program Files。
3. 启动 MuMu 12，开启 Root，启动游戏 2.7.91。双击 `ArkDPS.Desktop.exe`；无需系统 Python、pip、MAA 或 Pillow。
4. 主窗设置中选择 MuMu 实例/ADB。默认自动检测唯一认证实例；多实例必须明确选择。
5. 首次默认一块伤害面板，无 HUD；新增面板并按需“打开到 HUD”，HUD 在 WPF 中锁定/解锁。
6. 事件观测默认尝试 `127.0.0.1:27043`。ZIP 自带 **Frida 17.26.1 客户端**，但不安装、启动或打包设备端 Frida server。
   无端点时伤害/治疗快照仍可用，护盾与接敌指标未知，不是零；设置可关闭事件观测。
7. 关闭主窗是隐藏到托盘，采集/HUD 继续；从托盘或主窗选择“退出 ArkDPS”才真正清理退出。
   日志、战绩和配置位于程序旁 `collector/runtime/`。
8. 设置页显示当前版本，提供「检查更新」。便携版启动后会在后台检查一次。发现新版时，在主界面点击「查看下载」打开对应的 GitHub Release。

### 升级与回退

退出旧程序并备份 `collector/runtime/`；把新 ZIP 解压到新目录，复制备份的 `collector/runtime/` 后启动。
只迁移数据，不混用两版 worker；保留原备份可回退。布局与连接格式迁移由现有 store 处理，
损坏/未来版本诊断见[设置与布局](settings-and-layout.md)。游戏升级到非 2.7.91 时拒绝实时统计，
诊断说明实际/期望版本；仍可浏览已有历史。

## 构建候选

在干净已提交的 Windows x64 checkout，使用 CPython 3.10.10 与 .NET 10 SDK：

```powershell
python -m pip install -r requirements-windows.txt -r requirements-test.txt
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests -q --assert=plain
python tools/package_windows.py --version 1.0.0-rc.1 --output runtime/packages
```

构建器先验证「HEAD 精确 + 工作区干净」，再 `dotnet publish` 自包含 WPF 单文件、解包固定 SHA-256 的
CPython 3.12.10 embeddable 运行时、下载并核验固定 Frida 17.26.1 wheel，然后按 `BACKEND` 白名单复制
worker 源码与资源。`BACKEND` 不含任何 Tk 模块，构建结束前还会断言包内 manifest 不存在
`tkinter` / Tcl / Tk 文件，并用包内 Python 执行 `import desktop_bridge` 且断言 `"tkinter" not in sys.modules`。

解压根目录包含启动 EXE、README 和 `collector/`、`python/`、`metadata/`、`licenses/`。GitHub Release 标签使用
`v<版本>`，附上 `ArkDPS-<版本>-win-x64.zip` 和同名的 `.sha256` 文件。RC 在 GitHub 标记为预发行。
正式版只提示正式更新，RC 也提示较新的 RC。上传完成的标准 ZIP 是版本提醒识别的下载资产。
`--without-events` 的 ZIP 带 `-snapshot` 后缀。

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
`verify_wpf_package.py` 在新目录验证清洁解压、根目录布局、全部载荷摘要、缺少全局 Python/.NET 的运行、
采集器不匹配时的启动拒绝、复制旧版数据后的战绩与连接设置、v1 → WPF v3 布局迁移和 WPF 重启恢复。
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

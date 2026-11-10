# WPF 桌面端与 Python worker

这是当前桌面入口和分发契约；统计及历史格式仍由 [panel-model](panel-model.md)、[statistics-core](statistics-core.md) 与 [snapshot-adapter](snapshot-adapter.md) 拥有。技术取舍见 [ADR 0003](adr/0003-wpf-python-worker.md)。

## 进程与所有权

- `desktop/ArkDPS.Desktop` 是 .NET 10 / C# / WPF 原生 UI：主窗、统计面板、历史页、连接表单、固定干员报告、HUD、托盘与 PNG 均由 WPF 绘制；不是 Tk 换皮，也没有 WebView。
- WPF 启动一个 `desktop_bridge.py` 子进程。只有 Python 可以调用 `ConnectionConfig` / `CollectionSession` / `BattleRuntime` / `BattleHistory`；C# 不读游戏内存，不执行 ADB，不重新计算统计或占比。`DesktopState.view` 只调用既有 `render_panel`。
- GUI 隐藏时两个进程仍运行，HUD 跟随当前局。显式退出发送 `quit`，worker 停止并 join 采集线程、释放自己拥有的观察器及转发，再发 `bye`；UI 等待进程实际退出。stdin EOF 也触发同一清理路径。最终清理在审计流关闭前最多重试一次；最终 `stopped` 审计写入失败纳入 `cleanup_errors`，即使资源已释放也必须报告。若重试仍未能释放资源，则保留所有权并令退出失败。没有按进程名称杀进程，也不因协议错误静默切换到模拟数据。
- 重定位必须先成功释放旧会话；释放异常保留会话供重试。成功重试后的历史清理异常仍保留并令退出结果非零，不能把“当前句柄释放”误报为“本次没有清理失败”。每次 worker 运行以独占方式创建唯一审计 JSONL 文件，不追加到先前运行的记录。
- Python 保留原有单实例设备通道锁；明确的离线验收不占用该设备通道，必须指定独立配置目录。旧 Tk 模块仅供 `python app.py --legacy-tk` 和既有回归诊断使用，正常启动不创建 Tk 窗口。命令行 `--capture-seconds` 路径不变。

关卡名称由 Python 统一解析：`stage_names.py` 读取随包的 `vendor/ark-source/game_data/catalogs/stage_names.json`，把原生 `stage_id`（如 `Obt/Main/level_main_01-07`）翻译成玩家可读名称（`1-7`）。实时 `stage`、历史 `labels[0]`、PNG 与导出的 `display.stage` 共用同一解析；原始 `stage_id` 原样保留在历史、`stage_raw`、`stage_detail` 与导出中，UI 不改写它。目录缺失、无法解析或与 manifest `data_version` 不一致时，`warnings`/`history_notice` 明确提示版本问题并退回可读短码（如 `rogue6_1-3`），不显示内部路径，也不影响战绩读取。

合入 ARK-14 发布工具后，`app.py --self-test <新目录>` 先进入旧 Tk 离线自检；`--acceptance-output` 先验证有限正时长，再进入旧 Tk 测量。PyInstaller 冻结的旧 `ArkDPS.exe` 仍用 Tk，不尝试构建或启动源码 WPF；普通源码入口和 WPF 同包入口保持不变。旧构建/自检契约见 [Windows 旧候选](windows-release.md)，不继承其真实设备验收结论。

## 唯一协议：stdio JSON Lines v1

依照 ARK-19 最新修正，当前始终是一对父子进程，因此使用重定向的 stdin/stdout；**不使用 Named Pipes、TCP 或第三方 IPC 依赖**。只有未来有独立 worker 生命周期或多客户端需求时才重新评估 Named Pipes。

每行一个 UTF-8 JSON 对象（无 BOM），命令含整数 `version: 1`、递增 `id` 和 `op`。UI 串行化请求/响应；响应携带相同 version/id。不兼容版本（包括字符串/布尔版本）、越界消息或序号不符都拒绝，不维护兼容协议分支。单条上限 32 MiB；命令等待有超时，错误会标记“最后有效记录”，不冒充仍然实时。请求可能已发送后若遇超时、I/O、JSON 解码或序号/版本错误，UI 将通道标记为不可用并拒绝后续命令，不自动重放；不在失步流上发送 `quit`，而关闭 stdin 触发 worker 的 EOF 清理。收到完整、序号匹配的 worker `error` 响应不视为通道失步。

| op / 响应 kind | 语义 |
| --- | --- |
| `hello` / `hello` | 必须先握手；返回协议、transport、实际 Python 版本/路径和可选 Frida 客户端版本。 |
| `snapshot` / `snapshot` | 约每 200ms 请求；包含状态/错误文字、战局标识、既有模型的完整面板视图、配置及所选历史副本。 |
| 面板、主题、位置、历史、连接与导出命令 / `state` | Python 校验并持久化，再返回新状态；没有第二套 C# 配置文件或统计源。 |
| 任意失败 / `error` | `ok: false`、可读 error；状态不伪造为成功，UI 保留最后有效值并提示。 |
| `quit` / `bye` | bye 只在采集清理完成后发送；清理失败返回 error，UI 等待退出并显示失败。 |

stdout 专用于协议；worker 将普通日志重定向到 stderr。WPF 保存 stderr 至 `runtime/wpf-collector-*.log`（发布包位于 `collector/runtime`），原始采样审计仍在 `runtime/captures`。窗口生命周期、主题及历史选择只改变展示，不改变采集口径。一次性 CLI 连接覆盖在本次重定位中保留；只有显式应用设置才改长期偏好。`connection_apply` 先保存候选布局，再保存连接配置；若连接配置写入失败，会尝试恢复原布局并报告回滚失败。两份文件分别原子替换，但不是跨进程崩溃原子事务。

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
- 默认包含固定 Frida 17.26.1 Windows abi3 客户端 wheel 及许可。`--without-events` 是明确的累计快照版变体，不是下载失败时的静默降级；客户端不等于设备 Frida 服务，包不安装/启动服务，不随包分发用户的 ADB。

Python 和 wheel 下载分别用固定 SHA-256 核验，下载与展开位于新的隔离目录；ZIP 拒绝路径穿越/符号链接/超大展开。Python `._pth` 仅白名单包内目录，不读系统 Python、用户 site-packages 或 `PYTHONPATH`。没有全局 pip install。

`release.json` 同时登记 UI/worker/运行时/资源文件摘要、源提交和协议。启动时核对包内 worker 摘要及协议，然后握手验证实际响应版本；两端不一致明确拒绝。完整升级整个目录，不单独替换旧 worker。用户配置和历史不打进构建产物；已有配置可在退出应用后迁移到包内 `collector/runtime`，不要覆盖历史。运行目录必须可写；保护目录下会明确报错，而不是偷偷改存储位置。

## 可复核验收

测试依赖由根目录 `requirements-test.txt` 固定，包含用于独立解码验证 PNG 的 Pillow；它不进入 Python worker 运行时。WPF 统计 PNG 由 .NET `PngBitmapEncoder` 生成，旧 Tk 导出由 Win32 GDI 与标准库生成；不能因测试导入 `PIL` 就把 Pillow 当作产品运行时依赖，也不能漏装测试依赖或跳过 PNG 断言。

```powershell
python -m pip install -r requirements-test.txt
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests -q --assert=plain
python acceptance/verify_wpf.py --output runtime/wpf-source
python acceptance/verify_wpf.py --bundle runtime/packages/<解压目录> --output runtime/wpf-bundle
python acceptance/verify_wpf_package.py runtime/packages/<构建包>.zip --output runtime/wpf-install-upgrade-rollback
```

单包脚本还在新目录验证清洁解压、全部文件摘要、缺少全局 Python/.NET 的运行、v1→v3 设置迁移及显式 Tk 回退后再进 WPF；这只是便携 ZIP 的离线升级/回退，不冒充 MSI/注册表安装或游戏验收。

每次脚本新建独立 state 目录，不覆盖产品配置；真实运行 WPF、真实 stdio 握手、切换双皮肤、保存主窗/HUD/历史/设置/完整排行 PNG，并在新进程恢复配置。对私有下层测试窗口发送 Win32 鼠标输入，核对 HUD → 下层 → HUD。包验收还断言 worker 实际使用包内 Python 3.12.10 / Frida 17.26.1；构建 smoke 将系统 Python 环境变量设为无效值，验证不依赖宿主 Python。

**这些都是明确标注的合成数据。** 私有窗口接收点击不是 MuMu/游戏接收点击；物理托盘图标不可定位时回执明确 `NOT VERIFIED`，不将原生回调或 `Show()` 冒充物理双击。实际游戏点击穿透、物理托盘恢复与真实换局已在候选 `f7d59d9` 上完成实机验证（见下节），但混合 DPI 与退出/重启仍未收口；不得把本轮离线回执或旧候选证据当作这些实机项的通过证据。独立评审及这些人工项收口前，不宣称整票/首版发布验收完成。


## 本轮离线验收结果（2026-11-10）

- 完整 Python 回归实际运行 630 项通过；WPF 专项覆盖 worker 无 Tk 导入、统一统计输入、未知值、排行/速率正交、历史淘汰/换局隔离、版本拒绝、退出清理、旧配置迁移、包解压安全和实际双主题文字对比度。严格整数协议版本的回归曾实际失败（布尔 `true` 被 Python 当作 1），修正后通过。
- 使用本地 SDK 10.0.403 实际构建 `net10.0-windows`，0 warning / 0 error；源版原生 WPF 验收通过，包含真实错误版本 worker 拒绝、握手后模拟 worker 崩溃与非零退出可观测、卡住响应的 UI 过期提示、双皮肤 HWND/位置/尺寸/透明度/锁定不变、私有窗口点击路由、合成换局及新进程配置恢复。
- 自包含发布及带 Python 3.12.10 / Frida 17.26.1 的单包已实际构建和离线清洁解压运行，完成 v1 配置升级、显式 legacy 回退再进入 WPF；未删除 Tk 回退入口。精确候选、包摘要和截图摘要随构建回执交付。
- 继承的 V1 红/蓝/紫/白语义色未通过通用分类色校验（蓝/紫的 CVD/正常视觉距离及白色 lightness/chroma）；不擅自改写固定语义色，也不声称色盲安全，提供文字图例和精确明细。
- 实机项已不再等待设备交接：候选 `f7d59d9` 已在 MuMu（真实 collector、96 DPI、主屏 1920×1080 + 负坐标副屏）完成穿透点击、物理托盘双击、真实换局/历史隔离、双皮肤切换与导出；`f7d59d9` 上失败的退出/重启在修复候选 `0ca9f18` 上通过。混合 DPI（125/150/200%）仍无硬件可测。

## PR #21 当前候选离线复验（2026-11-10）

- 通过 `ARKDPS_DOTNET` 指向任务本地 .NET SDK 10.0.403，`net10.0-windows` 构建成功，0 warning / 0 error。
- `python acceptance/verify_wpf.py --output <新建隔离目录>` 生成 `PASS_OFFLINE_WPF` 回执；源版 WPF 首次启动与重启恢复均通过。回执包含 stdio JSONL 错误边界、两个主题、合成历史/HUD、私有 HWND 点击路由及截图 SHA-256。
- 首次运行暴露的是验收夹具错误：fake worker 对第二个 `snapshot` 请求返回了默认 `kind=state`；将夹具响应改为 `kind=snapshot` 后完整离线验收通过。该次失败不是产品 WPF/worker 故障。
- 此验收只使用合成数据和模拟 worker；没有访问 MuMu、启动 collector 或证明游戏点击穿透。物理托盘双击在离线回执中仍为 `NOT VERIFIED`，因为离线验收无法定位真实 Shell 通知图标。

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

# WPF 桌面端与 Python worker

这是当前桌面入口和分发契约；统计及历史格式仍由 [panel-model](panel-model.md)、[statistics-core](statistics-core.md) 与 [snapshot-adapter](snapshot-adapter.md) 拥有。技术取舍见 [ADR 0003](adr/0003-wpf-python-worker.md)。

## 进程与所有权

- `desktop/ArkDPS.Desktop` 是 .NET 10 / C# / WPF 原生 UI：主窗、统计面板、历史页、连接表单、固定干员报告、HUD、托盘与 PNG 均由 WPF 绘制；不是 Tk 换皮，也没有 WebView。
- WPF 启动一个 `desktop_bridge.py` 子进程。只有 Python 可以调用 `ConnectionConfig` / `CollectionSession` / `BattleRuntime` / `BattleHistory`；C# 不读游戏内存，不执行 ADB，不重新计算统计或占比。`DesktopState.view` 只调用既有 `render_panel`。
- GUI 隐藏时两个进程仍运行，HUD 跟随当前局。显式退出发送 `quit`，worker 停止并 join 采集线程、释放自己拥有的观察器及转发，再发 `bye`；UI 等待进程实际退出。stdin EOF 也触发同一清理路径。最终清理在审计流关闭前最多重试一次；最终 `stopped` 审计写入失败纳入 `cleanup_errors`，即使资源已释放也必须报告。若重试仍未能释放资源，则保留所有权并令退出失败。没有按进程名称杀进程，也不因协议错误静默切换到模拟数据。
- 重定位必须先成功释放旧会话；释放异常保留会话供重试。成功重试后的历史清理异常仍保留并令退出结果非零，不能把“当前句柄释放”误报为“本次没有清理失败”。每次 worker 运行以独占方式创建唯一审计 JSONL 文件，不追加到先前运行的记录。
- Python 保留原有单实例设备通道锁；明确的离线验收不占用该设备通道，必须指定独立配置目录。旧 Tk 模块仅供 `python app.py --legacy-tk` 和既有回归诊断使用，正常启动不创建 Tk 窗口。命令行 `--capture-seconds` 路径不变。

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

**这些都是明确标注的合成数据。** 私有窗口接收点击不是 MuMu/游戏接收点击；物理托盘图标不可定位时回执明确 `NOT VERIFIED`，不将原生回调或 `Show()` 冒充物理双击。WPF 的实际游戏点击穿透、物理托盘恢复、实际 Windows 混合 DPI、游戏实机换局仍须当前候选的人工验证，不能沿用 Tk 的旧证据。独立评审及这些人工项收口前，不宣称整票/首版发布验收完成。


## 本轮离线验收结果（2026-11-10）

- 完整 Python 回归实际运行 630 项通过；WPF 专项覆盖 worker 无 Tk 导入、统一统计输入、未知值、排行/速率正交、历史淘汰/换局隔离、版本拒绝、退出清理、旧配置迁移、包解压安全和实际双主题文字对比度。严格整数协议版本的回归曾实际失败（布尔 `true` 被 Python 当作 1），修正后通过。
- 使用本地 SDK 10.0.403 实际构建 `net10.0-windows`，0 warning / 0 error；源版原生 WPF 验收通过，包含真实错误版本 worker 拒绝、握手后模拟 worker 崩溃与非零退出可观测、卡住响应的 UI 过期提示、双皮肤 HWND/位置/尺寸/透明度/锁定不变、私有窗口点击路由、合成换局及新进程配置恢复。
- 自包含发布及带 Python 3.12.10 / Frida 17.26.1 的单包已实际构建和离线清洁解压运行，完成 v1 配置升级、显式 legacy 回退再进入 WPF；未删除 Tk 回退入口。精确候选、包摘要和截图摘要随构建回执交付。
- 继承的 V1 红/蓝/紫/白语义色未通过通用分类色校验（蓝/紫的 CVD/正常视觉距离及白色 lightness/chroma）；不擅自改写固定语义色，也不声称色盲安全，提供文字图例和精确明细。
- ARK-14 尚未交接设备，当前严格没有启动第二个实机采集器，也未操作 MuMu。WPF 的实际游戏点击、物理托盘双击和真实 DPI/游戏换局仍待设备释放后串行验收；这不是整票/首版发布完成声明。

## PR #21 当前候选离线复验（2026-11-10）

- 通过 `ARKDPS_DOTNET` 指向任务本地 .NET SDK 10.0.403，`net10.0-windows` 构建成功，0 warning / 0 error。
- `python acceptance/verify_wpf.py --output <新建隔离目录>` 生成 `PASS_OFFLINE_WPF` 回执；源版 WPF 首次启动与重启恢复均通过。回执包含 stdio JSONL 错误边界、两个主题、合成历史/HUD、私有 HWND 点击路由及截图 SHA-256。
- 首次运行暴露的是验收夹具错误：fake worker 对第二个 `snapshot` 请求返回了默认 `kind=state`；将夹具响应改为 `kind=snapshot` 后完整离线验收通过。该次失败不是产品 WPF/worker 故障。
- 此验收只使用合成数据和模拟 worker；没有访问 MuMu、启动 collector 或证明游戏点击穿透。物理托盘双击仍为 `NOT VERIFIED`，实机与正式 Multica Review Loop 仍未闭环。

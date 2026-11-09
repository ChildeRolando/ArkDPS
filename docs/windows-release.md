# Windows 候选构建与发布验收

## 状态与权威来源

ARK-14 的 ready-for-agent 发布规范。`release.json` 拥有候选版本、平台、兼容目标及对外发布授权状态；
`build-manifest.json` 由构建器产生，拥有该产物的精确 HEAD、工具版本和逐文件 SHA-256；
外置 `artifact.json` 拥有 ZIP 文件名、大小与 SHA-256。不可用 ZIP 自身的摘要反填其内部 manifest。
原始底层 Demo 记录 `VALIDATION.json` 不代表本候选发布验收；机制结论仍归既有实测文档。
独立验收需要同时固定候选 SHA、ZIP SHA-256、命令、原始日志/回执和截图，不能用以前候选的 PASS 冒充。

当前版本是 **1.0.0-rc.1 / Windows x64 便携版候选**，不是 MSI 安装器，不注册服务、不自动升级、无代码签名。
目标环境为 Windows 10/11 x64、MuMu Player 12、官方 Android 游戏 **2.7.91**、Root 开启。
Windows 10、干净 Windows 安装、多开、其他游戏版本/模拟器及原生 PC 未因构建成功而获得认证。
本工单仅准备候选；不得创建 GitHub Release、公开下载页或发布 tag，实际对外发布须另行授权。

## 用户快速开始（便携产物）

1. 从本票附件或本票 CI artifact 取 ZIP 与 `artifact.json`；用 `Get-FileHash <zip> -Algorithm SHA256` 对比。
2. 解压到用户可写的新目录，例如个人应用目录。**完整保留目录，不要只复制 ArkDPS.exe**，不要放入 Program Files。
3. 启动 MuMu 12，开启 Root，启动游戏 2.7.91。双击 `ArkDPS.exe`；无需系统 Python、pip、MAA 或 Pillow。
4. 主窗设置中选择 MuMu 实例/ADB。默认自动检测唯一认证实例；多实例必须明确选择。
5. 首次默认一块伤害面板，无 HUD；新增面板并按需“打开到 HUD”，HUD 在 GUI 中锁定/解锁。
6. 事件观测默认尝试 `127.0.0.1:27043`。ZIP 自带 **Frida 17.26.1 客户端**，但不安装、启动或打包设备端 Frida server。
   需要自行准备与版本匹配、仅本机可达的测试端点；参见[事件采集说明](event-capture.md)。
   无端点时伤害/治疗快照仍可用，护盾与接敌指标未知，不是零；设置可关闭事件观测。
7. 关闭主窗是隐藏到托盘，采集/HUD 继续；从托盘或主窗选择“退出 ArkDPS”才真正清理退出。
   日志、战绩和配置位于程序旁 `runtime/`，不要随支持请求无差别公开原始捕获。

### 升级、回退与卸载

退出旧程序并备份旧目录的 `runtime/`；把新 ZIP **解压到新目录**，先运行离线自检，再复制备份的
`runtime/` 到新目录后启动。只迁移数据，不混用两版 EXE/DLL/vendor。保留原备份可回退。
布局和连接格式迁移由现有 store 处理；损坏/未来版本诊断见[设置与布局](settings-and-layout.md)。
游戏升级到非 2.7.91 时拒绝实时统计，诊断说明实际/期望版本；仍可浏览已有历史。不要靠改版本字符串绕过门禁。
卸载仅在正常退出后删除该便携目录；需要保留的战绩先另存。不删除模拟器、游戏或其他工具资源。

## 构建精确候选

在干净已提交的 Windows x64 checkout 使用 CPython **3.10.10**（含 Tcl/Tk）：

```powershell
python -m venv .venv-build
.venv-build/Scripts/python.exe -m pip install -r requirements-windows.txt pytest==8.3.5
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
.venv-build/Scripts/python.exe -m pytest tests -q --assert=plain
$head = git rev-parse HEAD
.venv-build/Scripts/python.exe tools/build_windows.py --output runtime/release-build --expect-head $head
```

输出目录必须不存在；脏 checkout 或 HEAD 不符直接失败。不创建 commit/tag/Release，不修改现有安装。
PyInstaller onedir 的 `--contents-directory .` 保持资源和 `runtime/` 的原布局，不能擅自改为 `_internal`。
仅将 git 跟踪的 vendor/assets/docs 和显式的产品文档/事件探针加入数据文件，不含测试、设置、捕获或凭据。
二进制绑定 manifest HEAD；便携命令行采集无需安装 Git。源码采集仍要求 Git，并保留 `--expect-head` 的脏树门禁。

## 产物自检（不等于实机验收）

```powershell
# 在解压出的新目录运行；NEW_OUTPUT 必须不存在，路径含空格时正常加引号。
$p = Start-Process ./ArkDPS.exe -ArgumentList '--self-test', './NEW_OUTPUT' -Wait -PassThru
$p.ExitCode
Get-Content ./NEW_OUTPUT/receipt.json
```

在无游戏访问的明确离线模式，核对 manifest 全部摘要、Frida 客户端版本、设备读取器初始化、
头像资源、真实 Tk GUI/HUD 一致性、布局读回、真实托盘、历史幂等读写、JSON/PNG 导出和退出报告。
数据仅写 `NEW_OUTPUT`；不连接游戏、不安装观察器、不改已有配置/战绩。
回执必须是本次新生成的 `frozen=true/status=PASS/head=<candidate>` 且 `errors=[]`；
该模式不是游戏数值、真实跨进程升级或性能验收。`.github/workflows/windows.yml` 在 Windows 上运行全量回归、
构建和解压自检，仅上传私有候选 artifact，不对外发布。

## 同一候选的实机验收矩阵

独立验收者对解压后的同一 ZIP 操作，不切换源码版本：

| 场景 | 操作与必须保存的证据 |
| --- | --- |
| 启动/连接 | 记录 Windows build、CPU、显示器/DPI、MuMu 完整版本、游戏版本、设备、manifest HEAD、ZIP 摘要；保存设置页诊断和截图 |
| 默认事件 | 在匹配端点上从开局采集，确认 recording、护盾/接敌只在已覆盖区间有值；保存原始 capture 与截图 |
| 快照降级 | 另一次启动指定 `--no-events`，或不可达的 `--frida-remote`；伤害/治疗继续，事件指标明确未知，退出清理可核对 |
| 游戏不支持 | 实机非认证版本若可用，检查拒绝数值且仍可读历史；若不可用，明确只以 `tests/test_connection.py` 等故障注入覆盖，不改游戏版本来伪造实测 |
| 换局/双视图 | 一局结束只归档一次；同图重开/换图 GUI 与 HUD 跟新局；历史窗口固定旧局不跳；未知结果不猜胜负 |
| 设置/升级 | 两次独立进程启动核对连接、面板、HUD/锁定/透明度/几何；复制 runtime 的升级流程、损坏/未来布局诊断；物理显示变化另记人工证据 |
| 退出 | 托盘隐藏后恢复，真正退出；保存日志 `退出清理核对 ok=true`，核对仅自有转发释放、观察器 detach、进程结束，游戏仍运行 |
| 性能 | 同一关卡各跑 1×/2× × 单面板/多面板（含 HUD），每格至少 30 秒有效战斗；记录采样间隔、读取耗时、采样至渲染完成延迟 p50/p95、样本数、CPU 与私有内存，并写明统计定义 |

### 原生产物测量入口

在新解压的候选目录使用真实 GUI（会正常连接、创建 HUD，并在定时后正常退出）：

```powershell
$p = Start-Process ./ArkDPS.exe -ArgumentList '--acceptance-output', './live-single', '--acceptance-seconds', '90', '--acceptance-panels', '1', '--expect-head', '<candidate-sha>' -Wait -PassThru
# 第二次改用新的目录和 --acceptance-panels 3；游戏内分别选择 1×/2×。
Get-Content ./live-single/measurement.json
```

使用独立验收安装目录，测量模式会像正常 GUI 一样保存其布局，不在日常安装上跑。
`measurement.json` 记录逐样本逻辑帧、原生 `speed_level`、采样单调时钟、Tk idle 完成延迟、
每秒私有内存/工作集和进程 CPU；CPU 以单逻辑核 100% 计，不按机器核数归一化。
汇总只使用同战局内逻辑帧和游戏时间均前进的相邻样本，暂停/菜单/跨局不算战斗；
`RECORDED` 只表示有有效点且正常退出，不代表独立发布 PASS。`INCOMPLETE` 明确表示缺少战斗或诊断失败。
不控制游戏速度、不制造战斗样本，时长至少覆盖每格 30 秒有效战斗；全局资源指标不假称按速度分组。

采样间隔必须由实际连续发布的单调时钟求差，不拿配置的 200ms 当实测；渲染延迟包含队列与 Tk 更新完成，
不能用采集 `read_ms` 替代。记录速度的原生 `timeScale`，暂停和菜单样本不得充当 1×/2×性能样本。
不虚构原设计未给出的性能阈值；异常、掉帧和资源增长必须说明。当前端到端结果缺口见本票候选验收附件；
缺证据不能宣称首版已认证或已发布。

## 许可与分发边界

完整本地许可和上游来源见 `LICENSE` / `THIRD_PARTY.md`，非 MIT，包含非商业限制。
头像仍属鹰角；不能将其学术/交流来源说明解释为任意商业分发授权。构建打包 Python/Tcl/Tk、
Frida、typing_extensions 与 PyInstaller bootloader 的许可文本到 `licenses/`，来源及 Tcl 文本摘要见 THIRD_PARTY。
无 ADB、MuMu、游戏客户端、MAA 或 Frida server 随包分发。未签名程序可能被 SmartScreen 提示；不要指导用户关闭安全防护。

## 作者验证记录（不是独立认证）

2026-11-10：初始封装候选的真实连接暴露 `tools.*.__file__` 与 vendor 数据目录不一致，
缺少模块相对路径的 memsrv/game_data；已将非 Python 资源映射到冻结模块路径，并将该条件固化为
`tests/test_windows_release.py` 与产物自检。初始错误产物不得交付。另已复现旧源码在无 Git 的
便携采集环境中抛出 FileNotFoundError，冻结采集现在从 manifest 读取精确身份。

修复封装的候选 `5994c5e5a75f8d2c248b5e82ff7f7d61461f83ff` 在本机 Windows 11 build 26200、
MuMu 12、游戏 2.7.91、MAA 6.22.0 上完成便携原生自检和 OF-1 实机作者测量。
单面板/三面板（各含一个 HUD）都取得原生 speed_level=1/2 的有效推进样本，
同一进程两局具有不同 ID，并各写入一份战绩；未知结果保持 unknown，没有假定胜负或演习。
窗口截图已实际检查。两个正常测量进程退出报告均 ok=true，前后自有映射为空。
此前一次缺少山的阵容尝试、导航错误和一次并发 Python 子进程未进入任务的超时均保留失败记录；
后改为前台进程内 MaaCore、确认零理智节点、自动编队，再完成作者测量。失败尝试不算 PASS。

该候选事件观测下采样 p95 曾达约 1.9–7.1 秒，明显慢于 200ms 等待配置；这是真实的发布风险，
不能称为稳定 5Hz。读取本身 p95 约 4.4ms，Tk 完成年龄 p95 约 0.30–0.44 秒，不能混为一个指标。
具体样本、资源序列、产物摘要、最终候选的再次验证及独立结论以本票附带的新鲜候选回执为准。
代码不设不存在的性能验收阈值；若独立验收认为上述采样间隙不可接受，需要在发布前处理，不能隐藏。
干净 OS/Windows 10、实体显示器变化、物理托盘菜单、非 2.7.91 真机与不同地图仍须明确标为未重新覆盖。

### 历史重验阻塞（f9ff8f6）：不得冒充通过

随后候选 `f9ff8f67c83146f764c4fe97feabbb4634558436` 的解压产物离线原生自检通过，
从前一候选复制 runtime 后，实际新进程核对布局完全相等、两份历史可读、退出清理正常。
但其完整实机重验没有取得任何战斗样本：连接日志出现 `仅支持 memsrv v5，设备返回: 无握手`，
MAA Copilot 返回 taskchain error，测量回执明确为 `INCOMPLETE/published_samples=0`。
这不能用前一候选 `5994c5e` 的成功测量替代。

已核实环境呈现“ADB get-state=device，但设备命令不返回”：`nx_main/adb.exe` 对
`127.0.0.1:16384` 与别名 `emulator-5554` 的 `shell echo` 均在 8 秒超时；
`MuMu Player 12/shell/adb.exe` 的 `exec-out cat` 在 6 秒超时。没有执行 kill-server、
清缓存、重启模拟器或覆盖游戏。底层通道/设备无响应的触发原因尚未确认，不断言与产品无关。
此状态下不能继续验证游戏或可靠清理设备进程，需宿主机所有者恢复 MuMu 后再进行同一候选验收。

本轮自有本地 27043 转发已经释放，forward --list 为空，候选 GUI 进程正常退出；
自建观察器的本地 adb bridge PID 36288 已按命令行核对后停止。
设备侧本轮 Frida server PID **15646**（监听 **27864**）的存活状态无法再读取。
恢复后先读取 `/proc/15646/cmdline`，只有仍精确匹配
`/data/local/tmp/ark_dps/frida-server-17.26.1 -l 127.0.0.1:27864` 才停止该 PID；
不能按进程名批量终止或清理其他会话。

解除条件：设备命令重新可执行，处理上述精确遗留句柄；然后对本票最新 manifest HEAD/ZIP
重跑默认事件、快照降级、四格性能、同进程换局/归档与退出核对，再登记独立评审。
恢复前只保留草稿 PR 和候选工件；ARK-14 保持 blocked，不登记为可交审或已完成。


### 续跑恢复与再次验收入口

续跑确认所有 MuMu 配置均已停止。仅对既有 **index 0 / 官服16384** 执行
`mumu-cli control --vmindex 0 launch`（未重启运行中的实例、未新建/删除配置），
Android 就绪后定向 ADB connect 成功，guest echo 返回 ARK14_READY。
CLI 报告的安装版本为 **5.30.1.3586**，安装目录仍名为 MuMu Player 12；
应同时保存这两个事实，不凭目录名称猜测完整版本。游戏仍为 **2.7.91**。
前述旧 guest PID 15646 已不存在，无需终止复用 PID；MAA 6.22.0 的 StartUp、
Terminal-Entry、OF-1 与零理智确认均重新通过。当前可以继续验收，旧阻塞不是最新的运行结论。

本票最新候选按相同构建步骤重新生成；后续准确状态、全量测试、解压自检、实机四格和
退出回执均由本票附带的、带 HEAD 与 ZIP SHA-256 的 acceptance-receipt 拥有。
恢复本身不等于验收完成；只有该最新工件的实测证据齐备才转交 Review Loop，仍不对外发布。


### Windows CI 的真实桌面前置条件

首次 CI 已真实运行全量回归，600 项通过、4 项因托管桌面高度仅约 768 而失败：
Windows 将测试请求的 760px 客户区限制到 749px，另使模拟工作区的几何断言失真。
不跳过用例、不放宽断言、不改产品恢复逻辑；CI 在测试前用
`tools/prepare_windows_ci.py` 为隔离的 GitHub Windows runner 设置并核验 1920×1080。
仅 GITHUB_ACTIONS=true 且 RUNNER_OS=Windows 时允许运行，用户桌面拒绝访问显示 API；
模式测试、应用或验证失败都使 CI 明确失败。新回归固定该门禁和错误传播。
该操作只属于隔离 CI 夹具，不是产品功能，也没有修改作者本机的显示设置。

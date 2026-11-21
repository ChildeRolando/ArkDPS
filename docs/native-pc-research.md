# 国服 PC 采集预研（ARK-16）

## 2026-11-21 · CE-5 演习

ArkDPS 基于 rc.8 候选 `ee7cf86b61642108db8c832c0fc9994c4ff76747`。实机使用鹰角启动器的国服 Windows x64 客户端，客户端文件与已知构建的三个 SHA-256 一致，演习后资源清单记录 `V079 / 26-11-13-06-34-18-84612175`。玩家通过游戏界面完成 CE-5 演习。客户端文件自带 ACE，取证期间系统报告 ACE 的两项服务及 `ACE-BASE` 驱动为停止状态。

| 检查 | 观测 |
| --- | --- |
| `Player.log` | 从 7,721 字节增长到 18,496 字节。新增 154 个非空行，其中 48 个战斗相关匹配为战斗 UI 调用栈或资源诊断。未找到逐次伤害、治疗、护盾或结算统计。 |
| 其他本地文件 | 启动器和 SDK 日志未找到可用的战斗数值。安装目录与游戏用户目录未发现 `BattleLog`。结算附近有二进制文件 `eld_Arknights.db` 更新，公开格式和字段尚未确认。 |
| Windows 只读访问 | 在启动阶段和演习结束后分别识别到与安装路径匹配的 `Arknights.exe`。两次请求 `PROCESS_QUERY_INFORMATION \| PROCESS_VM_READ` 均在 `OpenProcess` 返回错误码 `5`，读取字节数为 `0`。 |
| 对照 | 探针在中等完整性级别运行。同一环境使用相同权限成功读取自建测试进程中的已知字符串。游戏进程拒绝访问的具体原因尚未确认。 |

本轮没有取得可供统计的实时数据源。PC 快照、逐次事件与 MuMu 功能齐平均待验证。后续可使用已确认的官方数据接口或可正常访问的普通只读来源重新对账。

## 2026-11-21 · 权限对照与开源实现核查

用同一个已启动的客户端比较 Codex 受限令牌与沙箱外普通令牌。两者均处于中等完整性级别，状态为未提升。Windows 在两个环境里都允许 `PROCESS_QUERY_LIMITED_INFORMATION`，并拒绝 `PROCESS_QUERY_INFORMATION`、`PROCESS_VM_READ`、二者的组合和 `READ_CONTROL`，均返回 `5`。独立申请 `PROCESS_VM_READ` 也失败，因此组合掩码和 Codex 受限令牌都无法解释这次拒绝。对照只申请并关闭句柄，未读取游戏内存。[Windows 进程访问权限](https://learn.microsoft.com/en-us/windows/win32/procthread/process-security-and-access-rights)

`GetProcessInformation` 返回 `PROTECTION_LEVEL_NONE`。`OpenProcessToken(TOKEN_QUERY)` 返回 `5`，无法直接核对目标令牌。磁盘上已签名的 `Arknights.exe` 和启动器的 `Games.exe` 都声明了 `requireAdministrator`。这将调查集中到游戏与采集进程的权限差异，但还无法确定是哪条访问控制规则拒绝了请求，也无证据将拒绝归因于 ACE。[Windows 进程保护级别](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/ns-processthreadsapi-process_protection_level_information) · [Windows 应用清单](https://learn.microsoft.com/en-us/windows/desktop/sbscs/application-manifests)

| 来源 | 已核查的能力 |
| --- | --- |
| [ArknightsCostBarRuler](https://github.com/ZeroAd-06/ArknightsCostBarRuler) | 从官服 Windows 窗口的费用条估算逻辑时间，提供本地 API。费用满、禁止自然回费或锁费时，无法靠这个方法推进计时。 |
| [MAA 的 Windows 截图代码](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/ae2cd60b46841f3c60dda1af67705e7aaf2e4663/src/MaaCore/Controller/Win32Controller.cpp) 与[战场识别代码](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/ae2cd60b46841f3c60dda1af67705e7aaf2e4663/src/MaaCore/Task/BattleHelper.cpp) | 可识别击杀数、费用、部署栏等可见状态。当前实现没有给出逐干员原生累计和逐次结算数据。 |
| [鹰角启动器数据面板说明](https://customer-service.hypergryph.com/app/arknights/question/ART178365239398256169747501) | 公开范围包括理智和任务进度，本轮未找到该面板提供战斗统计的证据。 |
| [ArkHelper v1.4.2 快照契约](https://github.com/zhengkanqin/ArkHelper/blob/v1.4.2/include/arkhelp/ipc.h) · [CSV 导出](https://github.com/zhengkanqin/ArkHelper/blob/v1.4.2/native/modules/damage_meter/csv_export.cpp) | 开源项目声明在国服 PC 获取数据。v11 实时快照定义了战局、逻辑帧、游戏时间、逐干员及团队指标、能力掩码，以及事件缺失和行截断标记。CSV 同时导出实际扣血和计算伤害。当前机器未观察到该发布端运行。 |
| [ArkFramework v1.0.0 CSV 导出](https://github.com/wuyu8512/ArkFramework/blob/v1.0.0/plugins/damage_meter/csv_export.cpp) | 导出实际扣血、计算伤害和游戏时间，未找到独立的实时消费接口。 |

ArkHelper 的公开说明称其数据发布端使用进程内注入和 Hook。ArkDPS 的实验接收端只尝试读取一个已存在的战斗快照。实时头文件里的总伤字段按实际扣血定义，ArkDPS 的游戏累计伤害含过量，两者保留各自口径。连续事件、技能区间、接敌 DPS 和护盾归因仍需要独立日志及实机对账。[ArkHelper 项目说明](https://github.com/zhengkanqin/ArkHelper/blob/v1.4.2/README.md) · [ArkDPS 统计契约](statistics-core.md)

权限对照回执和本轮使用的诊断脚本保存在 `runtime/native-pc/2026-11-21-access/`。

用发行版 C++ 头文件按 Windows x64 布局编译了独立的合成快照。实验接收端读出预期的战局 `42`、帧 `309`、时间 `10.3` 秒、逐干员实际扣血 `123.25` 和治疗 `45.5`。护盾能力位未设置，输出为 `null`。本机正常运行游戏时读取 ArkHelper 快照返回 `feed_missing`（Windows 错误码 `2`）。具体客户端构建上的真实战斗输出尚未验收。

## 复现

通过鹰角启动器打开客户端。在普通权限的 PowerShell 中运行以下命令，将路径改为本机安装目录。

```powershell
python acceptance/probe_native_pc.py --game-root 'D:\games\Hypergryph Launcher\games\Arknights'
```

探针按三个文件的哈希、进程与模块路径限定目标。如 Windows 授予读取权限，探针会比较 `GameAssembly.dll` 中两个固定位置的 96 个字节。`read_gate_passed` 表示固定字节比较通过，仍需用实机战局验证游戏时钟、编队归属与累计数据。

用 `--handle-only` 可以只核对 Windows 是否授予读取句柄，随后立即关闭。`read_handle_available` 只表示句柄申请通过，该模式不枚举游戏模块或读取游戏内存。

外部实时快照探针可以检查当前 Windows 会话是否已有兼容的 ArkHelper 快照发布端，并将其当前快照输出为 JSON。

```powershell
python acceptance/probe_arkhelper_feed.py
```

录制已有发布端的实时变化时使用 `--watch-seconds 30`。该模式输出 JSON Lines，记录不同的有效快照和连接状态变化。汇总的 `frame_progress_observed` 只在同一局连续样本的逻辑帧与游戏时间同时增长时为真。换局或数据中断会重新建立基线。该标记只证明观察到推进，逐次事件的完整性仍需单独验证。

独立 Windows 进程使用 v1.4.2 合成数据验证了持续采集。接收端在同一局读到第 309 帧实际扣血 `123.25` 和第 339 帧 `225.25`。本机真实发布名称连续轮询返回 `feed_missing`，没有产生有效样本。两份回执分别位于 `runtime/native-pc/arkhelper-v1.4.2-abi/watch-result.json` 和 `runtime/native-pc/2026-11-21-arkhelper-watch/live-check.jsonl`。

本机回执位于 `runtime/native-pc/2026-11-21-ce5/receipt.json`，包含权限状态、构建身份及 `Player.log` 的比较结果。回执仅保存诊断与统计摘要。

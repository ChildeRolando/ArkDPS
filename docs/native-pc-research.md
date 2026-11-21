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
| [鹰角启动器数据面板说明](https://customer-service.hypergryph.com/app/arknights/question/ART178365239398256169747501) | 公开范围包括理智和任务进度。本轮在官方说明和开源项目中未发现可复用的原版 PC 逐干员伤害来源。 |

权限对照回执和本轮使用的诊断脚本保存在 `runtime/native-pc/2026-11-21-access/`。

## 复现

通过鹰角启动器打开客户端。在普通权限的 PowerShell 中运行以下命令，将路径改为本机安装目录。

```powershell
python acceptance/probe_native_pc.py --game-root 'D:\games\Hypergryph Launcher\games\Arknights'
```

探针按三个文件的哈希、进程与模块路径限定目标。如 Windows 授予读取权限，探针会比较 `GameAssembly.dll` 中两个固定位置的 96 个字节。`read_gate_passed` 表示固定字节比较通过，仍需用实机战局验证游戏时钟、编队归属与累计数据。

用 `--handle-only` 可以只核对 Windows 是否授予读取句柄，随后立即关闭。`read_handle_available` 只表示句柄申请通过，该模式不枚举游戏模块或读取游戏内存。

本机回执位于 `runtime/native-pc/2026-11-21-ce5/receipt.json`，包含权限状态、构建身份及 `Player.log` 的比较结果。回执仅保存诊断与统计摘要。

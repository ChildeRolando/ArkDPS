# ArkDPS

ArkDPS 是《明日方舟》的 Windows 战斗统计工具。作战时可以查看全队 DPS、干员排行和敌方血量，也可以把排行放到置顶 HUD。战斗结束后，可以在「历史战绩」复盘或导出数据。

## 下载与启动

1. 前往 [GitHub Releases](https://github.com/ChildeRolando/ArkDPS/releases)，下载 Windows x64 便携包 `ArkDPS-<版本>-win-x64.zip`。
2. 在 **MuMu 模拟器 12** 中启用 Root，启动《明日方舟》安卓客户端 **2.7.91**。这是当前实时采集的适配版本。
3. 将 ZIP 解压到可写目录，双击 `ArkDPS.Desktop.exe`。便携包自带运行环境，无需另外安装 Python 或 .NET。
4. 进入战斗即可开始采集。使用多个模拟器实例时，在「连接设置」中选择游戏所在的实例。

## 便携版使用指南

- **实时战况**展示团队 DPS 趋势、干员伤害、治疗与护盾。可在排行面板切换统计口径，点击干员查看详情。总伤包含溢出伤害，有效伤害扣除已确认的溢出部分。
- **HUD**可从排行面板的「HUD」按钮打开。锁定后鼠标可穿透，字体、宽度和间距可在设置中调整。
- **敌情**展示已入场敌人的属性和血量。缺少数据时，数值会显示为未知或已确认的下限。
- **历史战绩**可离线浏览最近 100 局，支持筛选、导出 JSON 和保存完整排行 PNG。

「连接设置」中的「启用观察者」可以补充有效伤害、被减伤率、技能期伤害与覆盖率、接敌 DPS、实际护盾吸收和敌人入场等事件数据，需要在模拟器中准备 Frida 服务。观察者未连接时仍可读取伤害和治疗累计，未覆盖的事件指标显示为未知。可查看连接诊断或点击「重连观察者」。[观察者准备说明](docs/windows-release.md#观察者)

## 退出与升级

关闭主窗口会缩到系统托盘，采集和 HUD 继续运行。从托盘选择「退出 ArkDPS」即可停止。

战绩和配置保存在本机的 `collector/runtime/`，源码运行时位于仓库的 `runtime/`。升级便携版时先退出旧版，把新版解压到新目录，再将旧版的 `collector/runtime/` 复制到新版的 `collector/` 下。

## 从源码运行

安装 Python 和 .NET 10 SDK 后，双击仓库根目录的 [start.cmd](start.cmd)。`main` 与预发布包可能对应不同提交，实际版本所用的源码见 [GitHub Releases](https://github.com/ChildeRolando/ArkDPS/releases) 中的构建提交。

## 了解更多

- [变更日志](CHANGELOG.md) · [文档索引](docs/README.md) · [仓库开发导航与代理规则](AGENTS.md)
- [问题反馈](https://github.com/ChildeRolando/ArkDPS/issues)

ArkDPS 是非官方工具，复用了 [Arknights_timer](https://github.com/Tim23333/Arknights_timer) 的部分采集代码与数据。按随附许可用于非商业用途。[许可证](LICENSE) · [第三方来源](THIRD_PARTY.md)

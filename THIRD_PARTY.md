# 来源与修改记录

上游：Tim23333/Arknights_timer

URL: https://github.com/Tim23333/Arknights_timer

固定提交：5734c30bf54780056ff758505380322c0cf519fe

许可：Tim23333 Non-Commercial Software License Version 1.0，完整文本保存在本目录及 vendor/ark-source/LICENSE。本地衍生代码按该许可分发；没有另行宣称 MIT 授权。

保留：tools/enemy_health、tools/deploy_tracker 中采集相关 Python 模块，tools/game_data.py、tools/runtime_log.py 等支持文件，android_arm64 偏移、干员名称表、上游数据 manifest。数据只包含本工具需要的子集，不能用来运行上游完整应用或证明 manifest 全量文件齐备。

上游源码修改：vendor/ark-source/tools/enemy_health/memcore.py 将设备端服务目录改为 /data/local/tmp/ark_dps，推送前创建目录；监听只绑定 127.0.0.1；服务版本变化时只停止本工具端口的 nc，不停止其他端口的服务。

ARK-6 退出修复使同文件的进程查询要求设备侧 `pidof` 状态标记，拒绝将 ADB 故障的空输出当成已确认无游戏进程；查询失败继续抛错。

设备端二进制：从随附 tools/enemy_health/memsrv.c 用 Zig 0.14.1 在本机重新编译，目标 aarch64-linux-musl，静态链接、-O2。命令：

```
python -m ziglang cc -target aarch64-linux-musl -static -O2 -o memsrv memsrv.c
```

新增：collector.py 的伤害专用守卫事务、身份/版本校验和关闭清理；metrics.py 的游戏时间 DPS；app.py 的独立置顶窗口与本地导出；tests、启动脚本和文档。

本原型不代表原作者认可或提供维护支持。

## GUI 干员头像

`assets/portraits/normal.png` 和 `muted.png` 包含随附名称目录中 467 个干员的基础头像。
来源为 [yuanyan3060/ArknightsGameResource](https://github.com/yuanyan3060/ArknightsGameResource/tree/62fe51e6cd7faa5c1698e05f8cf63ed61cf6a7ac/avatar)，
固定提交 `62fe51e6cd7faa5c1698e05f8cf63ed61cf6a7ac`。该仓库声明静态资源版权属于
Arknights / 上海鹰角网络科技有限公司，仅用于学习与交流；游戏素材不重新声明为本项目自有素材。
本项目未使用其程序代码。保留原权利归属，不代表游戏厂商认可本工具。

构建脚本 `tools/build_portraits.py` 只下载基础头像，缩为 48×48，分别打包原色与灰度图集；
`assets/portraits/manifest.json` 记录每张原始 PNG 的 SHA-256、源提交、图集坐标及图集摘要。
重建需要 Pillow，当前 WPF 产品离线读取同一头像图集（旧 Tk 诊断入口保留）；未知干员使用姓名占位。

ARK-6 生命周期接入补入同一固定提交 `5734c30bf54780056ff758505380322c0cf519fe` 的 `tools/stage_global_state/memory.py`，保留其有界精确只读实现；包入口精简为该子集的说明，不导入未随附的 Timeline 应用。`tools/enemy_health/battle_lifecycle.py` 的读取结果增加已读取的原生 `state` 字段，供产品区分初始化、运行和结束，并增加经过对象类型及字符串槽稳定性校验的可选 `LevelData.levelId` 元信息；其余单例与场景读守卫保留。未验证的胜负、演习和退出原因不推断。新增产品观察代次、队列交接及视图契约位于仓库根目录 `battle_lifecycle.py`，没有复制上游完整应用。


## Windows 桌面运行时分发（ARK-19）

`tools/package_windows.py` 从官方 Python.org HTTPS 地址取得 CPython 3.12.10 embeddable x64，并使用固定 SHA-256 核验；原始 `python/LICENSE.txt` 保留在分发目录。自包含 .NET 10 / WPF 产物随包分发，保留 .NET `LICENSE.txt` 与 `ThirdPartyNotices.txt` 于 `licenses/`。默认完整包还保留 PyPI `frida==17.26.1` wheel 的 `dist-info/licenses/COPYING`（wxWindows Library Licence 3.1）及元数据，不将该许可改写为本仓库许可。Python 3.12 不需要其仅针对 Python <3.11 的 typing_extensions 依赖。安装包不包含 Frida server 或 ADB，也不安装/启动设备服务；明确 `--without-events` 仅生成无 Frida 的累计快照变体。

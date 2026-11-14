# Android 事件研究探针

用于已经授权的本机实验。已验证环境：Android 客户端 2.7.91 ARM64、MuMu、Frida Python/server 17.26.1。与产品的只读内存快照采集器分开；Interceptor 会安装观察 hook，但脚本没有改写参数、返回值或战斗数值。

需自行准备匹配版本的 Frida server、ADB 转发和当前游戏 PID。本脚本不会安装 server、改模拟器设置或启动游戏。不要把本次进程的地址当作下次可用地址；脚本从 IL2CPP 元数据重新找到方法，并核对所用字段布局，失败时不会开始观察。

```powershell
python acceptance/probes/trace_events.py --pid <当前游戏PID> --output <新证据文件.jsonl>
```

看到 READY 后再启动 MAA 作业。游戏暂停后按 Enter，刷新缓冲并卸载 hook。可使用 `--seconds` 做有时限的采集，但若在战斗启动前就超时，空事件文件没有验证价值。输出文件必须不存在，以免覆盖上一轮证据。

记录包括：

- ApplyModifier 前后数值及 HP、来源/目标，带同步逻辑帧。
- Buff.OnTakeDamage 的调用上下文，识别施盾来源及 buff 实例。
- BlockDamage.Execute / _AssignDynamicVar 与 OnBlockDamage，用于对比不同观察点，不能全部相加。

Modifier 是引用传入的未装箱结构，运行时字段元数据偏移需减去 16 字节对象头；Entity/Buff 则是对象。FP 按本次验证的 32 位小数解析。当前只测了闪灵 S2，不自动把所有减伤、闪避和次数抵挡当成数值盾。

代码包含全部回调观察数据，刻意未做生产优化或断连持久化。出现错误的采集应作为不完整证据保留，不能静默计为零。脚本卸载会结束采集，之后的事件不会由这段脚本自动补回。

版本化脚本已在暂停的同一进程验证元数据解析、方法地址与挂接/卸载。实战 JSONL 来自它的研究前身；核心回调逻辑相同，前身使用同一进程提前解析的方法表。不能把暂停冒烟检查称为又完成了一轮实战。

## 原生伤害累计诊断

```powershell
python acceptance/probes/trace_events.py --probe battle-logger --source char_4082_qiubai --pid <当前游戏PID> --seconds 20 --output <新证据文件.jsonl>
```

在已进入战斗、目标干员即将输出时启动。脚本校验当前 Android 客户端中 `ApplyModifier`、`BattleLogger.LogModifier` 与 `_CheckPlayerSide` 的签名和读取字段，只记录指定累计键（默认为仇白），最多 600 条回调记录。它按线程和引用地址匹配内层日志调用，同时保留角色 UID、目标、名义值、扣血、取消原因和原生累计分类写入前后值。

只有当帧、对象身份与日志调用边界均满足检查时，`comparable_native_delta` 才给出分类差值。未配对、缺行或交叠回调留作诊断。以多笔普通命中、未命中、同名不同 UID 和全吸收命中核对事件口径。此探针尚无 Android 实机回执。

# ArkDPS 原生采集性能与事件正确性专家咨询案

日期：2026-11-27。面向：Windows 原生采集、嵌入式 JavaScript、并发队列和性能工程专家。

咨询内容的代码核对基准为 [`5f946f4`](https://github.com/ChildeRolando/ArkDPS/tree/5f946f4b07fba4d87c18e3d6dbdc65dc11657594)。本次推送的源码分支快照为 [`c90fca4`](https://github.com/ChildeRolando/ArkDPS/tree/c90fca4b790ef5a3ca29bed231ae158a9e171c64)，分支为 [`codex/native-queued-capture`](https://github.com/ChildeRolando/ArkDPS/tree/codex/native-queued-capture)。源码分支仍在推进；本文所有源码与证据链接使用固定提交，不随分支更新漂移。较新提交中的改动不自动构成本文测量结果或实机验收结论。

## 当前最需要专家判断的问题

我们正在把游戏线程中的同步 JavaScript 采集，改为“原生现场取证 → 有界队列 → 后台单一 JavaScript 消费者”。

前端已经完成相当一部分异步化，但出现了第二个瓶颈：**后台完整 HP 回放约只能消费每秒 5 千至 6 千次 setter，而压力目标是每秒 3 万次。消费者长期落后，最终导致队列耗尽和采集停止。**

现在需要判断：

> 继续优化现有“重建指针接口、重放原回调”的方案，是否还有足够性能空间；还是必须改为“直接向同一套业务归约器提供结构化事实”，减少指针模拟和逐字段包装？如果改变接口，怎样证明没有损失结算、归属、嵌套和生命周期语义？

这不是尚未启用批量发送的问题。批量读取、预分配队列、后台消费、部分原生批量校验均已存在；当前难点是每笔事实的消费成本过高。

## 项目用途与不能牺牲的结果

ArkDPS 是明日方舟战斗统计工具，提供伤害、治疗、技能、站场时间、敌人状态及历史战绩等信息。卫戍模式包含备战、防御、协防、Boss 及跨回合切换。

与普通累计计数相比，项目还需要回答：

- 一次伤害属于哪个玩家、哪个干员或召唤物。
- 同名干员、对象地址复用、变身和召唤物宿主如何区分。
- 嵌套伤害回调是否描述同一笔结算，如何避免重复统计。
- 技能、死亡、撤退、回合结束如何闭合。
- 缺失证据时哪些数据只能标为未知。

| 层 | 实现 |
| --- | --- |
| PC 现场采集 | Windows x64、C++17、MinHook 1.3.4 |
| PC 业务回调执行 | 嵌入式 QuickJS 2025-09-13 |
| Android 采集 | Frida 适配器，共用主要 JS 业务逻辑 |
| 统计、生命周期、归档 | Python |
| 桌面界面 | WPF / .NET 10 |

本轮原生队列改造主要落在 Windows PC 后端。Android 继续通过现有适配器进入共用业务逻辑，不能把 PC 原生传输改造描述成已经移植到所有平台。PC 原生后端不依赖 Frida 来执行这些钩子。

参考机器为 Ryzen 7 7800X3D、64 GB 内存、RTX 5060 Ti。下面的性能数字均有版本和场景限制。

## 改造前已经测得的实机问题

旧 PC 原生后端的主要调用关系是：

```text
游戏函数进入／退出
  → 获取全局 recursive_mutex
  → 构造 QuickJS receiver、参数、NativePointer 等对象
  → 执行 JS 回调、现场读取、作用域验证、事件归并
  → 返回游戏

宿主 mark／read／ACK
  → 获取同一个 mutex
  → 使用同一个 QuickJS 上下文
```

游戏调用线程直接承担 JS 执行和对象构造成本，并与宿主采样争用同一个 VM。

实机高负载窗口中，HP setter 达到每秒一万至三万次。部分场景 FPS 降至 1—2，另一些同级或更高调用频率的场景仍较流畅。

一组同盘自然对照：

| 场景 | HP setter／秒 | HP 退出回调锁内均耗时 | FPS 中位数 |
| --- | ---: | ---: | ---: |
| 第 13 回合防御严重段 | 15431 | 21.52 μs | 1 |
| 同回合恢复后 | 21996 | 1.11 μs | 58 |
| 第 13 回合协防 | 23571 | 3.24 μs | 51.5 |

另一盘第 12、13 回合，我方干员 CID 和重复数量相同：

| 指标 | 第 12 回合 | 第 13 回合 |
| --- | ---: | ---: |
| HP setter／秒 | 12259 | 20283 |
| 进入回调锁内均耗时 | 15.78 μs | 16.14 μs |
| 退出回调锁内均耗时 | 1.45 μs | 10.31 μs |
| FPS 中位数 | 47 | 2 |

因此，调用频率、处理分支及其持续时间共同影响负担，不能只用“次数多”解释。

补充事实：

- 宿主士兵回血也出现过短暂个位数 FPS，不是只有宿主重装士兵。
- 另一盘隐匿敌人场景同样出现严重低谷，但其 HP 退出回调约 3 μs，增加更明显的是伤害结算和 Buff 回调频率。
- 同类隐匿敌人在协防中仍可保持 60 FPS。
- 实际管道写入通常只有约 0.2—0.3 ms／秒，WPF 没有对应的秒级应用状态停顿。
- 没有对应窗口的完整 CPU／GPU／调度 ETW 栈，不能宣布全部低 FPS 均由 ArkDPS 解释。
- 这些是自然对照，敌人、装备、倍速等没有全部冻结，不能当作严格单变量实验。

这些实机数字来自改造前的同步计时版本。不能直接与后文的 owned-memory 合成实验作倍数对比，或据此宣称异步候选已经改善游戏 FPS。版本、计算方法和原始日志前缀身份见[架构文档的实机依据](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/docs/native-capture-worker.md#依据与验收范围)及[性能计时文档](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/docs/performance-timings.md)。原始游戏日志未因本次文档推送而新增上传。

## 当前异步架构的实现情况

```text
游戏函数钩子
  → C++ 按字段合同复制当时的现场事实
  → 预分配线程队列／溢出环
  → 单一后台 QuickJS worker
  → OwnedReplayView 限定读取范围
  → bridge 重建回调环境
  → 共用 JS 业务回调与事实归约
  → 不可变发布前缀
  → Python 读取、验证、消费、ACK
  → 统计／历史／WPF
```

### 原生生产端

- 按钩子合同复制必要参数、对象身份、UID、前后值、精确时钟、作用域证据及父子调用关系。
- 热路径不进入 QuickJS，不做 JSON、逐笔日志或 IPC，不等待消费者。
- 原函数仍保留 Windows x64 参数、浮点和返回值 ABI；观察器只复制声明需要的参数槽。
- 不能只排队裸指针，等后台再读游戏对象，因为对象可能已变化、释放或复用。
- 有安全依据时提前过滤非目标类型、减少无变化分支的字段采集。
- 冷启动、回合第 0 帧和最终结束点需要真正的当时现场证据。

### 队列与线程所有权

- 最多 16 个生产线程槽，调用深度上限 256。
- 使用线程出生身份及世代，不能仅靠 TID 识别线程或回收槽位。
- 每条原始记录有 ticket；消费者必须处理连续前缀，不能跳过尚未发布的 ticket。
- 记录包含 312 字节固定头，以及按实际数量序列化的字段状态、segment、检查点和持有字节。
- 队列满、结构缺口等显式失败，不覆盖未消费记录，不阻塞游戏来等待空间。

### 后台消费端

`OwnedReplayView` 只允许访问当前记录已经持有的字节。读取权限还受当前租约、角色、checkpoint、字段 ID、地址范围、字段状态、守卫结果，以及精确配置、图版本和模式世代限制。

游戏地址在这里主要是“持有字节的身份标签”，不再授权现场读取。

消费者随后通过 bridge 重建 `NativePointer`、参数视图、receiver 等环境，执行原有 JS 回调。这保留了业务复用，也正是当前性能瓶颈的重要来源。

## 必须协调的状态机与不变量

| 状态机 | 必须保留的约束 |
| --- | --- |
| 原函数调用 | 进入、嵌套子调用、退出必须配对；父检查点不能借用另一次调用 |
| 对象身份 | 地址相同不等于同一实例，必须检查 UID、类型和世代 |
| 作用域 | 战局根、阶段、回合、名单、玩家归属和时钟必须相互匹配 |
| 配置与模式 | 已入队调用使用入场时固定的配置和模式，不被后来设置重新解释 |
| 冷图与热图 | 旧图只是布局候选，不能把旧现场值改时间后冒充新回合证据 |
| 事件归约 | 伤害、HP 写入、技能、死亡及召唤物归属会共同修改状态 |
| 发布与 ACK | 已采集、已处理、已闭合、已发布、已读取和已确认是不同水位 |
| 观察器生命周期 | 采样拒收不等于来源损坏；退出和退休还要处理未完成交接 |

特别需要保留以下限制：

- 不能把进入时的 HP 当退出 HP，不能只按对象地址缓存最近读到的值。
- 不能省略首笔回血、MaxHP 变化、死亡、跨回合或第 0 帧证据。
- 缺少计划字段、错角色和越界属于结构问题；真实现场不可读则可能只使局部结果未知，两者不能混淆。
- 不可变发布不能提前推进读取水位；ACK 只能确认实际读取并消费的前缀。
- 同一个 QuickJS 上下文不能靠换两把锁并发执行。
- 多 VM 或按线程分片也必须解决共享作用域、嵌套结算、事件顺序及全局水位问题。

现有静态图比较已部分下沉 C++：C++ 验证固定原始字节条件，JS 继续解释动态时钟、阶段和业务状态。证明绑定当前记录及配置，不能用一个可随意拼造的 `verified: true` 代替。

## 消费成本已经超过持续吞吐预算

压力目标为每秒 3 万次 HP setter。一次 setter 通常对应进入和退出两条事实，即约每秒 6 万条原始记录。

```text
单消费者每次 setter 的平均总预算
  = 1 秒 ÷ 30000
  ≈ 33.33 微秒
```

这是进入、退出、回放包装、字段验证及必要业务处理的总预算，还没有给其他钩子和发布留余量。

“HP 入口已经超过预算”指的是后台执行共享 HP 进入回调的成本，不是异步后的原生钩子入口成本。

分段实验使用固定源码和同一二进制，每分支 5000 次 setter，进入、退出各约 40 个采样：

| 回放部分 | 进入平均 | 退出平均 |
| --- | ---: | ---: |
| packed record 构造 | 2.97 μs | 2.64 μs |
| ReplayBegin | 6.12 μs | 5.50 μs |
| receiver 设置 | 3.63 μs | 0.70 μs |
| 参数／指针包装 | 3.17 μs | 1.38 μs |
| 实际共享 HP 回调 | **41.70 μs** | **116.24 μs** |
| 健康检查／前缀处理 | 1.09 μs | 1.41 μs |
| ReplayEnd | 2.11 μs | 2.35 μs |
| 返回包装 | 0.86 μs | 0.78 μs |

按[原始 JSON](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/runtime/production-hp-throughput/dispatch-sampling/sampled.json)逐段复算，非 callback 部分合计约 **34.69 μs／setter**；无变化分支约 **31.48 μs／setter**。该实验摘要中的 32.67／29.5 与原始值不一致，本文采用原始数据。

这意味着：

- 只优化退出分支还不够，进入回调本身已超过 33.33 μs。
- 只优化业务函数也不够，回放外围包装已接近或超过全部预算。
- 没有一个单独的巨大包装函数，成本分散在 Begin、record、receiver、参数和 End。
- **5000 次回血 setter 已经归并为仅 2 条 HP 事件，整个测试仍约 934 ms，吞吐约 5353 次／秒。** 输出事件少，并不代表归并前工作少。

另一组[独立函数采样](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/runtime/production-hp-throughput/function-sampling/summary.md)中，作用域适配器约 75 μs，其中动态字段循环约 46 μs；原生完整图检查本身约 4 μs。成本不只是底层读字节，还包含动态 slot 解码、角色切换、包装、比较和类型检查。

不同固定源码和插桩实验的数字不能跨表直接相加。它们也不是精确的无插桩生产净耗时；真实 FIFO 测试独立证实了吞吐不足。

## 队列容量已扩大但持续积压仍然失败

当前容量设计为 16 个线程私有环、每个 2 MiB，加 4 个共享溢出环、每个 32 MiB。字节 arena 合计 160 MiB，单个热线程最多可使用约 130 MiB。

实际生成的一个 40 棋子加召唤物场景，HP 进入记录约 907 字节，回血退出约 6208 字节。每秒 3 万次 setter 持续走这个分支时，500 ms 消费者停顿约需 106.7 MB。

真实有界 FIFO 实验目标为 300000 次 setter、名义 10 秒，仍失败：

| 消费者初始暂停 | 队列失败码 | 生产实际持续 | 已提交 setter |
| --- | --- | ---: | ---: |
| 0 ms | 4，queue_full | 约 799 ms | 23796 |
| 300 ms | 4 | 约 728 ms | 21734 |
| 500 ms | 4 | 约 673 ms | 20080 |

无暂停时，消费约 11775 条原始记录／秒，相当于约 5887 次 setter／秒；移除一处不必要引用环后约 12521 条原始记录／秒，仍远低于目标 6 万条。

扩大容量只能推迟失败，不能解决持续生产快于消费。具体输入、同二进制比较和失败记录见[完整 FIFO 结果](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/runtime/production-hp-throughput/hookpoint-active/summary.md)。

3 万次／秒参考实机总 HP 调用的短窗上界；“持续纯回血加完整字段”是保守组合，并非已测得的长期实机分支比例。需要专家判断合适的分支混合、突发和余量标准。

## 已尝试的优化与负结果

| 尝试 | 当前结果 |
| --- | --- |
| 回血／HP 事件归并 | 已有，能降低事件数量，但仍执行前置采集与回放 |
| 原生现场取证与后台队列 | 已实现主要链路，瓶颈转移到消费端 |
| OwnedReplayView 索引、标量读取优化 | 有局部收益，不能代表完整链路吞吐 |
| 固定图条件原生比较、动态 slot 投影 | 已有，原生检查较快，JS 动态处理仍贵 |
| 独立 HP 批读计划及注册表 | 实际 A/B 没有收益，已从候选删除 |
| HookPoint 批量读取实验 | 回血和 dirty 分支未取得稳定总体收益，FIFO 仍耗尽 |
| 移除 record 引用环 | FIFO 服务率约提高 6.3%，差距仍很大 |
| 闭包、字段描述表、projection visit 优化 | 部分分支改善、部分退化，未证明目标回血吞吐稳定改善 |

一组[ABBA 对照](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/runtime/production-hp-throughput/visit-abba/summary.md)中，projection visit 平均结果为：伤害约 +8.6%、回血约 +1.8%、dirty 约 +12.3%、无变化约 −16.7%。输入基线也有漂移，不能宣布总体成功。

性能实验要求固定二进制和源码 SHA，并核对 native API 实际调用计数，防止把走回退路径的测试当成新路径测试。更换 VM、JIT、更多消费者等方案尚没有本项目中的有效验收证据。

## 正确性仍有独立阻塞

主会话对近期异步测试包的实机复核记录如下：

- 第 12 回合防御未核实命中 763／1210，约 63.1%；协防为 0／91。
- 多数失败样本缺少同一次命中的命中后 HP。
- 最长数据年龄约 92.8 秒、重试 297 次，随后在 01:45:04 出现 queue_full（code=4），事件采集停止。

这组是近期测试包的实机结果，与前文同步版本的 FPS 对照、后文 owned-memory 实验分开。原始测试包日志未因本次文档推送而新增上传。

主会话随后补充了结算证据路径：当一次 `ApplyModifier` 内没有通常的 Unit／Output 通知，但确实捕获到唯一 HP 写入的完整进入、退出时，把这份独立证据交给现有结算验证器。

仍需核对来源与目标身份、作用域、时钟、前后 HP、实际变化、盾和过量伤害一致性；不能拿名义伤害量补造实际 HP 变化。实现和专项负例见[结算证据测试](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/tests/test_event_probe_hp_settlement.py)及[架构文档中的结算边界](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/docs/native-capture-worker.md)。

截至咨询基准，主会话报告相关修复通过 1004 项相关回归，另有 6 项原生 Graph 校验，但尚未完成新的实机验收。不能把所有未核实命中都归因于队列耗尽。

另外两个相关边界也需保留：

- 曾出现父类链只检查十层的问题，已改为设置期验证完整元数据闭包。新测试未再出现旧原因码 16，但这不等于所有现场类型均已实机覆盖。
- 旧运行中出现过跨小局待交接状态保留旧 cursor、观察器停止后无法同进程重建的问题。它与吞吐问题相关，但不是同一个故障，不能通过删除 cursor 校验解决。

## 验证已经覆盖与尚未覆盖的范围

已有测试覆盖原函数 ABI、嵌套、owned 字节、原存储被污染后的回放、错角色／错 checkpoint、缺字段、守卫变化、越界、队列回绕、满队列、跨线程、模式世代、冷图、第 0 帧、终局和 ACK 等。

| 验证层 | 能证明的结果 | 当前边界 |
| --- | --- | --- |
| ABI 测试 | 原函数参数和返回值保持 | 不能证明游戏 FPS |
| 字段及负例测试 | 错误不会被伪装为成功 | 不能替代持续吞吐 |
| 完整共享 HP 实验 | 特定合成场景的业务结果及成本 | 不是精确生产上限 |
| 有界 FIFO 实验 | 持续积压和暂停后追赶 | 当前仍失败 |
| 实机验收 | FPS、采集连续性及统计正确性 | 当前仍未通过 |

完整 HP 实验使用真实计划、原生 capture、OwnedReplayView、bridge 和共享 HP 回调；full-scope 场景含 64 个 scope 字段和 785 个 publication guard。它仍排除了实际游戏调度、钩子 trampoline、IPC 等，fixture 驱动及部分插桩也有成本。

QuickJS 缓存源码已与固定上游压缩包核验，检查的 61 个文件无补丁差异；未发现明显 DEBUG／PAGEHEAP 配置。这不等于已经排除所有编译、分配器或调度影响。[QuickJS 来源回执](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/runtime/production-hp-throughput/function-sampling/quickjs-provenance.json)记录了源码压缩包和缓存对象摘要。

## 请专家重点给出的决策意见

希望意见落实到以下问题，而不止是“批处理、无锁、换引擎”：

1. **回放接口是否应改变？** 是否应停止为每笔 HP 事实重建完整指针回调环境，改为结构化事实直接进入共用 JS 归约器？如何划分平台取证、证据验证和业务状态处理？
2. **事实格式最少需要什么？** 请明确实例身份、前后 HP、MaxHP、时钟、scope、父子调用、检查点及失败状态的字段，并说明哪些不能压缩或缓存。
3. **哪些合并可以提前到 JS 之前？** 对无变化 setter 或同实例连续回血，能否在保留原始时序证据的前提下压缩？遇到伤害交错、嵌套、MaxHP 变化、首笔回血和跨线程时，应在哪里强制断开？
4. **单消费者还是否合理？** 若建议多线程或多 VM，必须说明全局顺序、共享 Map、跨对象结算、作用域和 ACK 如何归属，不能只增加消费者数量。
5. **性能验收应怎样设计？** 如何区分生产端耗时、字段复制带宽、回放包装、业务归约、发布和 IPC？3 万 setter／秒应采用什么分支混合、突发长度、余量和暂停恢复标准？
6. **如何证明性能改造没有改变统计语义？** 希望得到明确的等价性检验、对抗负例和实机对照方法，尤其覆盖缺失结算通知、同名干员、召唤物、跨小局初态及最终水位。

主会话目前倾向探索“保留逐笔事实、减少指针模拟、让同一套 JS 逻辑直接处理事实”的接口。该方向仍在设计和实验阶段，不能视作已经验证成功的解决方案。

## 代码与测量证据入口

以下链接全部固定到咨询基准提交。复现时应同时核对回执内记录的二进制、脚本和较早源码版本，不能假定所有实验都测量该基准提交。

| 内容 | GitHub 入口 |
| --- | --- |
| 架构、状态机及证据边界 | [native-capture-worker.md](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/docs/native-capture-worker.md) |
| 原生取证、回放、消费调度、宿主请求 | [engine.cpp](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/native_pc/native_event_runtime/engine.cpp) |
| 采集组合、早过滤、scope 与 publication | [engine_capture_pipeline.h](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/native_pc/native_event_runtime/engine_capture_pipeline.h) |
| 队列槽位与调用所有权 | [engine_capture_lane.h](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/native_pc/native_event_runtime/engine_capture_lane.h) |
| owned 字段索引及读取约束 | [owned_replay_view.h](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/native_pc/native_event_runtime/owned_replay_view.h) |
| record、指针、参数、Begin／End 包装 | [bridge.js](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/native_pc/native_event_runtime/bridge.js) |
| scope 证明及动态字段解释 | [event_probe_native_pc.js](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/event_probe_native_pc.js#L801) |
| 共用 HP、结算及生命周期业务 | [event_probe_runtime.js](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/event_probe_runtime.js) |
| 发布、读取和 ACK 日志 | [publication.js](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/native_pc/native_event_runtime/publication.js) |
| Python 生命周期与跨回合交接 | [battle_lifecycle.py](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/battle_lifecycle.py) |
| 测试范围与复现方式 | [production_hp_quickjs.md](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/tests/production_hp_quickjs.md) |
| 回放外围原始计时 | [dispatch-sampling/sampled.json](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/runtime/production-hp-throughput/dispatch-sampling/sampled.json) |
| 分段测量脚本 | [run_dispatch_sampling.py](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/runtime/production-hp-throughput/run_dispatch_sampling.py) |
| 函数分解、插桩和来源限制 | [function-sampling/summary.md](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/runtime/production-hp-throughput/function-sampling/summary.md) |
| 完整 FIFO 失败与同二进制对照 | [hookpoint-active/summary.md](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/runtime/production-hp-throughput/hookpoint-active/summary.md) |
| projection visit ABBA 对照 | [visit-abba/summary.md](https://github.com/ChildeRolando/ArkDPS/blob/5f946f4b07fba4d87c18e3d6dbdc65dc11657594/runtime/production-hp-throughput/visit-abba/summary.md) |

所有引用的实验源码、脚本及已提交回执可从该分支历史取得。原始游戏日志、测试包和缓存二进制不因本文链接可访问而自动成为已公开附件；如专家需要逐笔实机对账，应另行提供相应固定前缀及摘要。本文没有将原始日志新增上传到 GitHub，也没有把未公开附件写成可访问的 GitHub 链接。

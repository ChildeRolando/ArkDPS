# ArkDPS 官服 PC 实机验收手册

**先开记录，再按卡片打。** 把采样想成每隔一小会儿抄一次游戏里的记分牌。它能记住当时的累计分，也能看出后来多了多少。两次抄表之间发生了几次命中、哪一次打破了盾，需要游戏另留一条逐次记录。下面把现在能采到的项目和需要额外数据的项目写清楚。

## 开打前看一眼

- 保持官服游戏正常打开。所有下方命令都在**管理员 PowerShell** 里整条复制执行。每条命令会自动取一个不同的文件名。
- 看到 `recording` 再切回游戏。一个采样结束后再开下一个。能演习就演习，关闭代理指挥。
- 卡片说「停」时，切回 PowerShell 按 **Ctrl+C**。在结算页等的时间算现实秒数。最后一行有 `summary` 就行，`interrupted: true` 表示手动结束。静止补读时 `frame_progress_observed: false` 是预期。
- 打完只告诉我**关卡、演习还是普通、带的干员和技能、照做时有哪一步不同**。文件我直接读取。碰到 `failure`，发最后一行。关卡找不到或缺干员，跳过即可。
- **已有结果可以沿用。** CE-5 已验证伤害、闪灵非零治疗、1×/2×、暂停和胜利结算。1-7 已验证稀音小车的 8,109 物理伤害只归给稀音一次。以下项目补的是另外的证据。[可行性报告](native-pc-feasibility.md)

## 现在可以采

### A · 刚才那局还在时补读，2 秒

只有还停在稀音那局 1-7 才跑。保持暂停，等命令自己结束。它会补读客户端保留的手动动作和此刻的单位集合。

```powershell
& 'F:\Code\python\python.exe' 'E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\acceptance\record_native_pc_battle.py' --game-root 'D:\games\Hypergryph Launcher\games\Arknights' --seconds 2 --witnesses --output "E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\runtime\native-pc\2026-11-21-battle\summon-witness-$(Get-Date -Format 'yyyyMMdd-HHmmss').jsonl"
```

### B · 放弃后同图重开，1-7 演习，两局共用这一条

已收到 `restart-20261121-125443.jsonl`。它记录了两局及第二局累计清零，命令留作复查。

```powershell
& 'F:\Code\python\python.exe' 'E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\acceptance\record_native_pc_battle.py' --game-root 'D:\games\Hypergryph Launcher\games\Arknights' --seconds 300 --witnesses --output "E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\runtime\native-pc\2026-11-21-battle\restart-$(Get-Date -Format 'yyyyMMdd-HHmmss').jsonl"
```

1. 第一局用任意干员打出几次伤害。打开暂停菜单，选**放弃行动**。
2. 返回关卡页等约 5 秒，再进同一个 1-7。
3. 第二局先空着走约 5 秒，再放干员打出几次伤害。暂停后停采样。

我要核对第一局已经结束、第二局从自己的时钟和累计开始，以及两局的伤害有没有串在一起。

### C · 两台小车、撤下再补，1-7 演习

已收到 `summon-20261121-123256.jsonl`。小车的伤害已经对上。那次记录没给每台小车记编号，下方命令用于补读两台车的编号和撤退记录。

补录 `scene-presence-20261121-151312.jsonl` 已验证两车同场、撤下一台和新车补位。命令留作复查。

```powershell
& 'F:\Code\python\python.exe' 'E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\acceptance\record_native_pc_battle.py' --game-root 'D:\games\Hypergryph Launcher\games\Arknights' --seconds 300 --witnesses --output "E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\runtime\native-pc\2026-11-21-battle\scene-presence-$(Get-Date -Format 'yyyyMMdd-HHmmss').jsonl"
```

1. 稀音用上局熟悉的技能和站位。保持 1×。把两台摄影小车沿敌人经过的一条路分开放，守门干员放它们后面。
2. 看到两台小车都已经摆上且有输出后暂停，等约 3 秒。恢复，撤下一台，保持另一台在场。
3. 可部署时补一台到有敌人经过的位置。等补上的也攻击几次，再暂停 3 秒，停采样。

我要看到两个不同的单位编号，撤退后少一个，新车上场后多一个。统计只加共享的小车累计一次。

### D · 打到一半才打开采样，1-7 演习

第一次 `midbattle-20261121-130509.jsonl` 对上了接入时的伤害。补采 `midbattle-20261121-151919.jsonl` 已对上同一名干员接入后的新增伤害和近五秒速率。命令留作复查。

1. 先进入战斗。用一名干员打几次，确认已经有伤害。然后**暂停**。
2. 保持暂停，运行下面这条。等 `recording` 出现后再等约 3 秒。

```powershell
& 'F:\Code\python\python.exe' 'E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\acceptance\record_native_pc_battle.py' --game-root 'D:\games\Hypergryph Launcher\games\Arknights' --seconds 120 --witnesses --output "E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\runtime\native-pc\2026-11-21-battle\midbattle-$(Get-Date -Format 'yyyyMMdd-HHmmss').jsonl"
```

3. 回游戏恢复 1×，让同一名干员再打几次，暂停，停采样。首份样本应已有伤害，之后只新增这段打出来的量。

### E · 正常的失败结算，1-7 演习

已收到 `defeat-20261121-130606.jsonl`。游戏给出了失败结果，回到关卡页后战斗也已结束。命令留作复查。

```powershell
& 'F:\Code\python\python.exe' 'E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\acceptance\record_native_pc_battle.py' --game-root 'D:\games\Hypergryph Launcher\games\Arknights' --seconds 300 --witnesses --output "E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\runtime\native-pc\2026-11-21-battle\defeat-$(Get-Date -Format 'yyyyMMdd-HHmmss').jsonl"
```

1. 先放干员打出几次伤害，再撤走负责守门的干员。
2. 让敌人进蓝门直到出现**战斗失败**。在失败画面停 5 秒，回关卡页再等 5 秒，停采样。

我要对比游戏明确给出的失败结果与 B 的主动放弃。

### F · 本体撤退后再部署，1-7 演习

已收到 `operator-redeploy-20261121-130807.jsonl`。缄默德克萨斯每次上场都有新编号，伤害仍一直接在同一行。命令留作复查。

```powershell
& 'F:\Code\python\python.exe' 'E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\acceptance\record_native_pc_battle.py' --game-root 'D:\games\Hypergryph Launcher\games\Arknights' --seconds 300 --witnesses --output "E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\runtime\native-pc\2026-11-21-battle\operator-redeploy-$(Get-Date -Format 'yyyyMMdd-HHmmss').jsonl"
```

1. 带夜刀或一名能较快再部署的干员和守门干员。1× 下让被测干员单独打几次。
2. 撤掉被测干员，守门干员继续挡怪。等被测干员转好，让他重新上场打几次。
3. 然后暂停 3 秒，停采样。告诉我你用的是谁。

我要看同一角色离场时如何保留累计，再部署后有没有接着增加。这项帮我们核对「全周期 DPS」的分母，也就是该干员自己在场多久。

### G · 同一个敌人受击前后的当前 HP，1-7 演习

`enemy-hp-20261121-135345.jsonl` 已验证当前 HP 和暂停。命令留作复查。

在关卡页启动下面的命令，看到 `recording` 后进 **1-7 演习**。开 1×，用米格鲁一类普攻伤害较低的干员拦第一波敌人。让同一个敌人挨几下。看到它还活着时暂停等 3 秒，再继续打。结算回到关卡页后停采样。

```powershell
& 'F:\Code\python\python.exe' 'E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\acceptance\record_native_pc_battle.py' --game-root 'D:\games\Hypergryph Launcher\games\Arknights' --seconds 180 --enemies --output "E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\runtime\native-pc\2026-11-21-battle\enemy-hp-$(Get-Date -Format 'yyyyMMdd-HHmmss').jsonl"
```

我要核对同一个敌人的编号一直相同，并且在受击后的新一帧能读到较低的当前 HP。最大 HP、击杀原因和每次伤害的来源还需要其他证据。

### H · 本关引用哪个等级的敌人，1-7 演习

在关卡页用管理员 PowerShell 整条运行。看到 `recording` 后进 1-7 演习。等第一只敌人出现，再等约 5 秒，然后切回 PowerShell 按 **Ctrl+C**。采到开场即可。

```powershell
& 'F:\Code\python\python.exe' 'E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\acceptance\record_native_pc_battle.py' --game-root 'D:\games\Hypergryph Launcher\games\Arknights' --seconds 50 --enemies --stage-enemies --output "E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\runtime\native-pc\2026-11-21-battle\stage-enemies-$(Get-Date -Format 'yyyyMMdd-HHmmss').jsonl"
```

同一个敌人可能有多个配表等级。这里核对这关用了哪个等级、是否带覆盖，以及实际出现的敌人键。确认这些以后，才能决定哪些图鉴数据可用于对照本局。

`stage-enemies-20261121-143120.jsonl` 已通过 1-7 的关卡引用实测。命令留作复查。

### I · 演习标记、主动放弃和正常失败，1-7

在关卡页用管理员 PowerShell 整条运行下面命令。以下三步共用这一次采样。

```powershell
& 'F:\Code\python\python.exe' 'E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\acceptance\record_native_pc_battle.py' --game-root 'D:\games\Hypergryph Launcher\games\Arknights' --seconds 240 --game-flow --practice-selection --output "E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\runtime\native-pc\2026-11-21-battle\game-flow-$(Get-Date -Format 'yyyyMMdd-HHmmss').jsonl"
```

1. 看到 `recording` 后打开 **1-7 普通行动**的编队页。停约 3 秒，返回关卡页。
2. 打开 **1-7 演习**，也在编队页停约 3 秒。进战斗，放一名干员打几次，再用暂停菜单里的「放弃行动」回来。关卡页等约 3 秒。
3. 再进 **1-7 演习**，让敌人走进蓝门直到出现「战斗失败」。回到关卡页等约 3 秒，然后按 **Ctrl+C**。

我会对照两局进入结算时的场景和结果字段。这个探针把两者分别记下来，便于检查游戏能否区分主动放弃和普通失败。

`game-flow-20261121-145053.jsonl` 已验证普通和演习编队的两种选择、两次演习开战，以及主动放弃和自然失败的不同转换顺序。命令留作复查。

### J · 手动撤退和被敌人击倒，CE-5 演习

在关卡页用管理员 PowerShell 整条运行。看到 `recording` 再进 **CE-5 演习**，保持 1×。

```powershell
& 'F:\Code\python\python.exe' 'E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\acceptance\record_native_pc_battle.py' --game-root 'D:\games\Hypergryph Launcher\games\Arknights' --seconds 240 --presence-lifetime char_122_beagle --output "E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\runtime\native-pc\2026-11-21-battle\presence-lifetime-$(Get-Date -Format 'yyyyMMdd-HHmmss').jsonl"
```

1. 让**米格鲁**挡住一波敌人，亲手把她撤下。其他干员可以守门。
2. 米格鲁转好后，再把她放到敌人能打到的位置。这次不给她治疗，也不点撤退，等敌人把她击倒。
3. 击倒后继续 3 秒，暂停并按 **Ctrl+C**。告诉我是否完成了两种离场。

这次采样会记录同一名干员的两次在场编号、游戏报告的离场原因和原始时钟。就算画面上看到了离场，也需要样本里有对应时标，才能把它用在全周期速率里。

首轮取得了部署和手动撤退记录，原始时标校验未通过。修正后的 CE-5 补采读到了构建时标和 `HP_ZERO`，也确认这次击倒的死亡时标仍是 `0`。这组采样已读完。

### K · 1× 治疗和多个在场敌人，CE-5 演习

这项单独录。回关卡页，在管理员 PowerShell 整条运行。看到 `recording` 再进 **CE-5 演习**，保持 1×。

```powershell
& 'F:\Code\python\python.exe' 'E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\acceptance\record_native_pc_battle.py' --game-root 'D:\games\Hypergryph Launcher\games\Arknights' --seconds 180 --enemies --output "E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\runtime\native-pc\2026-11-21-battle\ce5-dense-heal-$(Get-Date -Format 'yyyyMMdd-HHmmss').jsonl"
```

1. 放一名重装挡在敌人经过的路上。放**闪灵**给她治疗。先不放会快速清掉敌人的输出干员。
2. 看到闪灵连续治疗几次、场上有多名敌人后，暂停约 3 秒。
3. 恢复，再等闪灵治疗几次。按 **Ctrl+C** 停采样。

一份记录分别核对两件事。闪灵在 1× 时的近五秒治疗，以及多个敌人同时在场时，能否持续读到各自的编号和当前 HP。

K 实机记录已核对 1× 正治疗和同帧 9 个在场敌人的 HP，以上命令留作复查。

### L · 固定波次，复用正在打的 1-7 或 CE-5

在战斗中暂停，在管理员 PowerShell 整条运行。看到 `recording` 后恢复约 5 秒，再暂停。等命令自己结束。顺手记下界面显示的敌人总数，便于对照波次。

```powershell
& 'F:\Code\python\python.exe' 'E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\acceptance\record_native_pc_battle.py' --game-root 'D:\games\Hypergryph Launcher\games\Arknights' --seconds 15 --stage-spawns --output "E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\runtime\native-pc\2026-11-21-battle\stage-spawns-$(Get-Date -Format 'yyyyMMdd-HHmmss').jsonl"
```

CE-5 复测共 13 次读取，固定波次配置均为 42。命令留作复查。

## 等我发对应的采样命令再打

下面也都是真实要做的验收。现有命令还记不到所需的目标 HP、护盾消费或逐次结算。现在跑会留下整局总数，缺少要对账的那一笔。我准备好相应字段后会直接发完整命令。

| 要测什么 | 到时这样打 | 看什么才算拿到证据 |
| --- | --- | --- |
| 手动技能和自动技能 | 1-7 演习，用玫兰莎 S1 或一名手动攻击技能干员，先普攻，开一次技能，等结束后再普攻。自动技能另开一局测。 | 技能前、中、后都要有单独归属的伤害。接敌 DPS 还需要两段攻击之间有一段空档。 |
| 敌人 HP、有效伤害与过量 | 1-7 演习，让一个攻击者单独打一个敌人，留出至少一次没打死和一次低血量击杀。 | 同一敌人实例的命中前后 HP、该笔伤害的来源和结算量。这样才能算多打了多少。 |
| 减伤率 | 选熟悉的演习，分别让同一攻击者打普通和高防或高抗目标。我确认目标和技能后给站位。 | 同一笔攻击的公式前后值和对应目标。 |
| 数值盾实际吸收 | 能进入 OF-8 时用闪灵 S2、夜刀、煌 S2。让闪灵的盾先挡到攻击，再等到盾被打破。OF-2 可做另一次自然到期对照。 | 这面盾属于谁，敌人的哪一击扣了多少盾，盾有剩余时自然消失的数据保持单独。[已有站位](experiment-playbook.md#4-守门让实验活到目标事件) |
| 召唤护盾 | 能进入 OF-2 且有凯瑟琳装置时，部署装置保护一名挡在敌人前面的干员。 | 盾实际吸收量只加一次，归给凯瑟琳。 |
| 满血、持续回血和离场召唤治疗 | 有可编入队伍的赫默 S2 时，用同一架无人机轮流覆盖缺血和满血干员。单独做山 S2 自回血的对照。 | 逐次治疗、实际回复的 HP、召唤宿主是否离场，以及这几种数值各自的口径。 |
| 元素积累与爆条 | 有酒神 S1 且能进 OF-2 时，把元素积累集中到同一目标，等它爆条。 | 哪个来源施加了积累，爆条伤害最终归给谁。 |
| 敌情、首领筛选、复活和阶段变化 | H7-4 已解锁时，打到首领阶段转换并留到结算。 | 同一敌人身份、当前和最大 HP、阶段边界，首领输出应能从其他敌人中分开。 |
| 地图 NPC 和装置 | M8-6 已解锁时，让地图自带的单位先输出，再部署自己的干员。 | NPC、地图装置和队伍输出各自归属，不混进同一排行分母。 |
| 卫戍协议与肉鸽 | 模式开放并确认能进后再采。卫戍至少连续两小局，肉鸽挑能确认变形或次数盾的节点。 | 小局切换不串数据。同一个敌人的变形、次数盾和免疫按实际机制记录。 |

关卡和干员是否可用以你的游戏界面为准。后几项用到场景机制时，我会先核对实际地图，再把位置和动作写给你。[已验证的 MuMu 场景](capability-validation.md) · [补充场景](recovery-validation.md)

## ArkDPS 界面版

已完成 1-7 演习正常入口的实时排行、胜利、退出、失败和战绩归档。用 `b02dd40` 测重启游戏、窗口、当前名单 HP 和固定波次。先从托盘退出旧 ArkDPS，在管理员 PowerShell 整条运行。游戏照常启动。

```powershell
& 'E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\runtime\native-pc\product-b02dd40\ArkDPS-win-x64-d75gxzgo\ArkDPS.Desktop.exe' --platform native_pc --native-game-root 'D:\games\Hypergryph Launcher\games\Arknights' --state-dir "E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\runtime\native-pc\product-b02dd40\desktop-review-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
```

1. 在 ArkDPS 点「侧边吸附」。进 **CE-5 演习**，放一个输出干员，等他打几下。敌情页看「固定波次计数」是否为 **42**，点开一个受伤敌人看「已观测最高 HP」。顺手记下游戏顶部显示的敌人总数。
2. 最小化游戏，再从任务栏恢复。看 ArkDPS 侧栏是否跟着收起和回来。
3. 保持 ArkDPS 开着，正常关闭游戏，再从鹰角启动器重开。等 ArkDPS 连上后进一局 **1-7 演习**，先空着几秒，再放干员打几下。告诉我游戏里的敌人总数、侧栏有没有跟上，以及重开后进了哪一关。换进程前后的累计由日志核对。

已有战绩时，可以在历史里选中它并点「完整排行 PNG」保存。HUD 和 MuMu 平台实机切换另测。

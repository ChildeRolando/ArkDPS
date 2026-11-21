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

```powershell
& 'F:\Code\python\python.exe' 'E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\acceptance\record_native_pc_battle.py' --game-root 'D:\games\Hypergryph Launcher\games\Arknights' --seconds 300 --witnesses --output "E:\文档\ChatGPT\ArkDPS\arkdps-native-pc\runtime\native-pc\2026-11-21-battle\scene-presence-$(Get-Date -Format 'yyyyMMdd-HHmmss').jsonl"
```

1. 稀音用上局熟悉的技能和站位。保持 1×。把两台摄影小车沿敌人经过的一条路分开放，守门干员放它们后面。
2. 看到两台小车都已经摆上且有输出后暂停，等约 3 秒。恢复，撤下一台，保持另一台在场。
3. 可部署时补一台到有敌人经过的位置。等补上的也攻击几次，再暂停 3 秒，停采样。

我要看到两个不同的单位编号，撤退后少一个，新车上场后多一个。统计只加共享的小车累计一次。

### D · 打到一半才打开采样，1-7 演习

已收到 `midbattle-20261121-130509.jsonl`。打开采样时已有伤害，这项已对上。打开后的这段没读到新伤害，后续只需补这一步。

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

## ArkDPS 界面版接好后再做

累计与战局接入正式入口后，还需要一次**关游戏再开**和一次**界面验收**。关游戏前打一段，正常退出，重新开游戏后再进一局。我会核对旧局只归档一次、新局统计没有串进去。界面验收要实际操作连接平台选择、实时排行、HUD、侧栏、历史、PNG／JSON 导出、设置恢复，最后用同一候选复查 MuMu。准备好候选时我会给运行路径和每一步，现在可以先做 A 到 F。

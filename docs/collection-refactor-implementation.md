# 从采集钩子到展示的重构实施记录

日期：2026-11-26。规划基线为 `066bcb9a184d8c9fb3222560973511c620dc44d3`，生产基线为 `667bb2e320c76ad60f64b02a5a5e6a4a4b6b2b86`。实施分支为 `codex/collection-refactor`。本记录对应[重构规划](collection-refactor-plan.md)的 D01–D26；代码、离线验证、实机验收分开登记。

状态：D01–D26 代码收敛及本机离线门禁完成。以下保留私测 `1.0.0-rc.2026112613` 的冻结交付记录；当时包与桌面副本的源提交、SHA 和实际路径见 `runtime/refactor-evidence/package-delivery.json`，没有操作正在运行的游戏。后续公开 rc.42 的交付及迁移见[发布说明](release-rc42.md)。实机性能验收尚未完成。

## 已改变的运行路径

共同 JS 在原生回调中保存事实，完整目标结算只输出一份有版本的结算证书。Python 通过共同的范围与 ACK 规则接收，由 `BattleSessions` 决定小局和收尾，`EventStream` 验收后交给单一逐击账本。独立读取的原生累计仍是另一类事实，不从逐击观察量补造累计。Python 提供有名字的展示读数，WPF 直接呈现。

技能只保留 actor/deployment 状态；owner 的技能时间是只读汇总。元素来源只有 target 生命周期内的一份 witness，并且只消费一次。部分伤害、boss、技能伤害、过量和接敌区间从同一逐击账本投影，删除旧账本后不再在发布末段覆盖旧结果。

平台 ABI、内存布局、I/O、资源释放和独立原生快照校验有各自职责，继续保留。Android/MuMu、PC Frida 和 PC ArkPie 提交同一共同 JS；没有按平台复制技能或结算算法。

## D01–D26 交付对照

| 项目 | 最终归属与删除结果 |
| --- | --- |
| D01 | `event_probe.reset_probe_cursor/commit_probe_prefix` 共用提交规则，ACK 绑定 observer 寿命；丢 ACK 只重试确认。 |
| D02 | `native_pc.event_source` 共用启动提交、watcher 和取消转移；Frida token、ArkPie lease/build/claim 校验保持各自边界。 |
| D03 | probe 共用范围参数、ACK 值、JSON 格式及 PC 终局尾读取规则；各 transport 的请求、锁、超时和清理薄适配保留。cooperative host 继续拥有服务端游标，不再叠加一套客户端游标。 |
| D04 | 删除 owner 的可变技能窗口；唯一 actor/deployment 技能 FSM 处理真实开始、结束、死亡及退场。 |
| D05 | 初态未知和历史区间质量由同一技能状态输出；陌生 UID 及旧 body 不污染无关实例。 |
| D06 | `Statistics.settlement_bars` 是唯一元素来源缓存；旧 `last_elemental` 只读投影，不再写入第二份 bar。 |
| D07 | 唯一 settlement supplement 写入；显式来源与 source-null 累计外补充各按证据处理一次。 |
| D08 | `DamageSettlement` 是逐击金额唯一归约器；旧 wire 只在明确能力模式解释，完整证书模式的旧金额不再参与重复归约。 |
| D09 | 构造结果时一次选择金额域；删除先算旧 effective/overkill、随后覆盖的分支。 |
| D10 | 删除 `PartialDamage` 类及存储；partial 是同一逐击账本的观察子集。 |
| D11 | 删除 `BossDamage` 类及存储；boss 是同一已分类命中的目标子集。 |
| D12 | `damage_settlement.extend_activity` 共用 1.5 秒区间合并；伤害、治疗及不同金额域保留各自触发集合。 |
| D13 | v2 完整证书携带 HP、取消及 native modifier 事实，停止为同一次结算重复输出旧 damage/hp/burst。无对应证书的我方、召唤物或未知目标观察仍保留，不删除必要输入 hook。 |
| D14 | 世界目标事实由已验收命中更新一次；多玩家各自归属视图不再次相加。 |
| D15 | native 事件共用 `resolve_native_event_member`；native 和 schema1 token 宿主共用 `verified_roster_token_host`。累计 CID key→owner 与账号 actor 是不同身份，原映射职责保留。 |
| D16 | `BattleSessions` 选择终局证据，`BattleRuntime` 编排消费及 ACK；受管 adapter 不再自行重置/收尾。 |
| D17 | `CollectionSession` 产品采集统一经过 `BattleRuntime`；删除旧独立 read/finish 路径。 |
| D18 | `capture_battles` 委托生产 CollectionSession；保留 journal 和原始回放格式。 |
| D19 | `PanelSourceRow.rate` 提供榜单、详情与趋势的相同口径读数；全周期与观察期接敌仍是有名字的不同窗口。 |
| D20 | Python `inspector_readings` 提供比例、证据和原因；删除 C# 重新选择证据、重新计算比例及旧字段 fallback。 |
| D21 | `DesktopState` 按发布 revision/scope/配置复用规范化结果；同帧修订使缓存失效。采样、IPC、FPS 年龄每次按原时刻更新，FIFO 趋势中间点保留。 |
| D22 | wire 只保留 `details_by_owner`；删除 `entries[].detail`。Python 的兼容属性只指向同一个对象，不再序列化第二份。 |
| D23 | `view.source` 收窄为界面确实使用的字段；导出和历史证据不随 UI DTO 裁剪。 |
| D24 | 旧战绩解释集中到 `BattleRecord.normalized_statistics`，分享仅委派；原始 payload 与经过验证的读取视图分离，同 record 只解释一次。 |
| D25 | 删除无生产消费者的 `metrics.Meter`；保留独立 `observation_age`。 |
| D26 | 删除无活动消费者的 `battle_share.record_view`、`panel_model.render_board`，测试转向实际生产入口。 |

## 小局初态、提交与终局

真实零起点仍必须来自原生 reset 返回与完整前缀，备战回看副本不参与建局。为“阶段先变、时钟尚未 reset”的场景增加独立的 `pending_scope_start` 事务：保留原始正时间锚点，等待并分页读取完整首事件前缀，验收成功后才 ACK。它不会假称采到零帧，也不会把初始技能状态设成已知。

受控场景在 frame45、1.5 秒保存序号1的空锚点，随后读取 `(1,2]`，首笔伤害75只入账一次。该局全程覆盖仍为不完整。无 reset 的合法零输出 scope 会保留实际观察区间并归档；只有原生 serial 和 generation 均严格相邻、片段确为空且不存在未知来源时，才允许真实 reset 替代前置片段。

pending 与普通提交都绑定 observer 的 `_cursor_lifetime`。同一对象重新加载探针、事件序号增加但 epoch 倒退、跨过中间世代时拒绝接棒。首次中途附加且没有前一局时仍立即建立真实的 late-attach 采样，不无限等待首击。

能力由 producer 明确字面声明锁定。只有 `enemy_damage_ready is False` 且 unknown 声明明确为 False 才启用独立旧观察模式；缺字段不等于 False，mark/drain 冲突或同流能力翻转在归约前拒绝。Stats 直接调用边界也拒绝证书与旧观察的两个金额写入者。

## 输出格式、旧档与对账

新证书使用 `proof.settlement_wire_version: 2`；嵌套链引用真实 settlement sequence，不生成虚假 burst sequence。需要独立原生 final modifier 时读取已保存的 `proof.native_modifier_observation`，不能拿名义伤害代替。完整格挡时 final modifier 为0而名义伤害为40，二者继续保留不同含义。

旧 raw 输入不补不存在的 HP、UID 或原生实例，不升级完整性。旧证书和旧配对记录仍在读取边界严格校验。历史回放比较只对已经版本化的诊断原因做对应转换；金额、身份、序号、窗口和原字段存在性保持严格检查。旧 fixture 和实际战绩字节不改写。

`BattleRecord.to_dict` 和导出保留原输入的缺失、null、零、旧 policies 及未知字段；默认值只用于验证和解释。五种冻结场景的 full/panel 结果逐字段相同，同 record 五读的解释次数由5降到1。私有读取视图只复制需要添加默认的容器，公开返回值独立深拷贝。History 的128条/64MiB上限指编码 raw 缓存载荷，不包括活跃 record 和调用方副本的额外内存。

## 验证记录

生产源码与测试在 `06e9a45c557fa32a377b6860fe27f4813191b499` 冻结。其后只有本文档收口；`tested-source.json` 和 `tested-source-verification.json` 核验492份源文件没有改变。阶段专项、原失败及修复后的结果均保存于 `runtime/refactor-evidence/`，各子代理的原始结果和复制 SHA 清单在 `agents/` 与 `final-agents/`。

| 门禁 | 实际结果与回执 |
| --- | --- |
| CI 同范围 Python 完整回归 | `python -m pytest tests -q --assert=plain`：6139通过、22跳过，0失败；128.71秒。`ci-scope-final.log/xml`。 |
| 跳过原因 | 21项可选 controlled/internal C++ host 未构建；1项本地账号无符号链接权限。未提权或伪装通过。`final-regression-summary.json`。 |
| WPF Debug 与 Release 构建 | 均0警告、0错误；`wpf-*-final.log`。 |
| WPF Debug 与 Release 无输入检查 | 全部通过，含同份 Python golden 的57项 Inspector 断言、37组验收标记组合；`wpf-*-headless-final.log`。 |
| 包与交付 | 构建器验证嵌入式 Python、离线 worker 启动及干净解压；独立脚本逐文件核验 ZIP 和桌面副本。实际摘要、源提交与路径记录于 `package-delivery.json`，原始构建日志为 `package-test13.log`。 |

第一轮全量发现4项失败，其中3项对应删除的 UI/source 契约，1项为旧回放的 partial 诊断版本比较；修复后相关152项通过，且增加6个金额/身份等篡改反例。后一次无目录参数的 pytest 额外收集了 runtime 中用于证明旧缺陷的历史脚本，得到6133通过、22跳过、1失败；没有修改该脚本或原证据。正式门禁沿用 CI 的 `python -m pytest tests -q --assert=plain`。

WPF 只执行构建与无输入契约检查，未调用会移动鼠标的 `verify_wpf.py/verify_wpf_package.py`。本机未替代隔离 Windows CI、交互验收或实机对照。

## 性能证据与未完成的验收

本次已经减少同一事实的重复状态、归约、wire 与展示物化，但没有证明测12 R11的掉帧已解决。

- 从保存的 R11 HP 记录选32个负向端点，在自有 Node 内存中执行4轮，共128次回调。前后均为9344次逻辑读取、131864字节和128条 HP 记录，即每次73次读取；单次短计时差异不足以证明加速。负向 HP 的读取预算尚未下降。
- 使用与当前6份原生源码完全匹配的 DLL `551cbc03224f0cc85bde790644e783edb8ae0457e9bb370c75332ff3d268879a` 在自有进程执行256次整数 ABI 调用，记录真实包装/JS行为并核验停止后代码恢复。该实验没有完整 HP 逻辑，VM 锁等待/持有及游戏 FPS 均未测量。
- 小型两行展示 golden 的 wire 从9333增至9354字节：重复详情已删除，但新增读数解释抵消了字节收益，不能宣称总 JSON 变小。稳定 revision 的重复规范化已通过计数验证。
- 之前误用的另一份缓存 DLL 基准已降为历史 ABI 证据，原输出保留。本次包仅复用已校验源码的551cbc DLL，不重新编译或改动原生引擎。

新构建的 `performance_verified/autochess_handoff_verified` 默认 False。支持、载荷校验和真实候选实机验收分别记录；启动成功不再自动声称实机通过。原生 VM 锁分段、完整 HP 回调与同关卡 FPS 对照仍是未完成的实机/原生性能验收，不以离线通过补造 PASS。

## 变更控制与恢复

沿用现有 AGENTS、ADR、Statistics/EventProbe/BattleSessions/panel_model 边界，没有新增服务、第二 worker、通用事件总线或规则注册中心。不同职责的独立校验保留；旧 Tk 封存区没有修改。各阶段独立提交，回滚采用前一个完整匹配包，不通过静默换后端或切回另一套金额路径恢复。

私测包来自干净的已提交候选，记录源提交、逐文件 SHA、ZIP 摘要、Python/WPF 协议及原生源码清单；提供私测包，不冒充正式发布或实机验收完成。

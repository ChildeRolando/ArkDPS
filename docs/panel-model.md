# GUI/HUD 共用面板模型与排序口径

当前 WPF 提供侧栏导航、实时总览、排行、HUD 和干员详情。「孤星」「人们，我们。」和「巴别塔」共用布局与数据模型，各有主题图稿和样式。干员详情中的「被减伤率」使用 `mitigation_rate`（百分比）；没有公式前伤害证据时保持未知并显示“—”，不以过量或护盾替代。

伤害面板的「仅首领」与「有效伤害 / 溢出总伤」独立组合。开关按面板保存，排行、干员详情和对应 HUD 共用所选口径。近 5 秒 DPS 按这段时间内命中首领的结算事件计算，接敌 DPS 使用首领命中的活跃区间，全周期 DPS 使用干员在场时间。历史战绩保存首领伤害数据，旧战绩没有该字段时显示未知。

当前 WPF 展示通过 `desktop_bridge.py` 只读消费 `panel_model.py`，C# 不重算排行/柱长/占比；运行与验收见 [桌面运行时](desktop-runtime.md)。产品 UI 只有 WPF：Tk 实现（`gui_panel.py`、`hud.py`、`gui_history.py`、`ui_theme.py`）已归档封存到 `archive/legacy-tk`，见 [ADR 0005](adr/0005-seal-legacy-tk.md)。下文 ARK-9/10/12/19 章节是 Tk 时代的实现与验收记录，只作追溯，不代表 WPF 的实机验收已完成。

共享模型实现于 `panel_model.py`；`app.py` / `gui_panel.py` 的实时 GUI、`hud.py` 的 HUD 和 `gui_history.py` 的历史窗口使用该模型，共享同一口径。

## 配置与运行状态分离

- 持久化只保存 `PanelConfig`：`panel_id`、类别、排行、DPS算法、可选标题；`PanelCollection` 带 `version`，读取时校验版本与重复 id。
- 运行状态只存在于 `PanelView` / `PanelEntry`：数值、柱长、占比、颜色、在场与详情，不回写配置。
- 类别：伤害 / 治疗 / 有效护盾。排行：总伤（治疗为总治疗、护盾为总护盾）/ DPS；DPS 排行按所选 DPS 算法排序并以该算法计算贡献条、占比。DPS 算法列：近 5 秒 / 接敌 / 全周期DPS；护盾不使用 DPS 排行、接敌或全周期（配置直接拒绝）。全周期分母是在场时间，从本次观察起点累计；若接敌活跃时长更大，分母至少取活跃时长，采集缺口或未知在场状态保持未知。有效伤害开关把总量及所有 DPS 算法的分子改为有效伤害；近期/接敌缺有效分子证据时保持未知。

## 口径规则

- 排行、柱长、占比分母始终使用排行口径（总量或最近 5 秒），**切换速率列不改变它们**。
- 最高值柱长为 1，其余线性缩放；不使用 Softmax，也不固定柱长之和。
- 排序使用未舍入原始值；显示使用整数与 1/K/M，仅影响显示。排行值、占比、速率、悬浮提示、HUD 和统计 PNG 最多保留 1 位小数（`0.#`）；右侧明细面板统一四舍五入到整数，并隐藏所有数值为零的行。未舍入原值只保留在内部模型与 JSON 导出中。
- 伤害条颜色：物理红 `#e5484d`、法术蓝 `#3d8bfd`、元素紫 `#9b5cf6`、真实白 `#ffffff`；累计排行使用 `damage_by_type`，近期排行使用同一近期速率口径的 `recent_damage_by_type`，`segments_complete` 提示分型合计是否与对应排行数值一致。近期分型缺失时分段为空且不完整；详情仍保留累计分型。
- 数据完整时，分母是全队同口径输出之和。数据有缺口时，以所选指标可靠、口径一致的条目之和计算「已知数据内占比」。已知的零输出和离场条目仍属于各自的分母。未知条目的占比显示 `—`。
- `share_scope` 为 `team`、`known` 或 `unavailable`。`share_known` 表示全队占比已知。已知分母须大于零。
- `overkill_damage` / `overkill_coverage_complete` 只随行传播到详情和分享，不进入排行分母。事件流未覆盖、目标、击杀时的护盾/抵挡吸收上下文或实际扣血证据不足时该值为 `None`，不得替换为 0。
- 技能期伤害占比优先使用完整拆分。拆分不全时，详情用已观察且可判断所属技能窗口的命中计算占比，并显示缺口原因。有效伤害口径只纳入能够核对过量的命中，分母为同一口径下已观察的技能期与非技能期伤害之和。没有正值分母时显示未知。
- 含过量总伤与元素分段以原生累计为基数，只补入成功归属的无来源爆条。支持逐击结算的战局以已确认的实际扣血和扣盾统计有效伤害，并显示缺口。历史记录缺少逐击结算时使用原版本可核对的证据。归属未知的爆条不计入干员有效伤害。
- 逐属性过量使用 `overkill_damage_by_type` / `overkill_type_complete`，由事件的原生 `DamageType`（1 物理、2 法术、3 真实、5 元素）分组。未知类型使分属性值保持未知，但不影响已验证的总过量。总量排行的伤害分段可带 `overkill_ratio`，表示该属性段末端被过量占据的比例；近 5 秒排行不派生过量。WPF/HUD/PNG 在同一位置绘制半透明斜纹与警戒线，遗漏类型或事件不完整时不绘制。
- `share_reason` 保留缺失依据，包括未归属、歧义、冲突或字段缺失的来源，以及累计快照不完整的干员。已识别为地图装置或陷阱的非队伍来源排除在分母之外，另在 `share_note` 列出名称与输出。
- 近期占比只使用有合法 `recent_<category>_window=(start, end)` 的可靠条目。该字段证明数据覆盖 `[max(采集开始时间, 当前时间-5), 当前时间]`。缺少窗口的条目保留已知排行值，占比为 `—`。多个可靠窗口起止不一致时，占比不可用。

新版统计带有局部覆盖时，排行、HUD 和历史使用同一份已核实数值。队伍占比按同口径可用值计算，标为已知数据内占比。受影响的列有标记，详情显示统计失败占比和可用分母。首领、近期和接敌指标各自核对分母，分母无法确认时显示未知。

## 列表行为

- 总量列表保留离场干员的累计，头像标记为灰（`portrait_muted=True`）。
- 实时列表只展示最近 5 秒输出大于零的条目；归零即移除，有输出再出现。
- 未知指标不生成占位行，也不写零；输入行不被删除或修改。
- 点击详情包含四类伤害、**有效伤害**、**过量伤害**、治疗、有效护盾、按属性过量、活跃时间与归并来源键。所有数值取整，零值行不显示；元素爆条已折入有效/过量，不单列。过量来自事件证据，未知时显示 `—` 并说明原因。WPF Inspector、历史详情、统计 PNG 与 JSON 导出使用同一组字段。

## 事件不可用时的接敌展示（ARK-22）

- DPS算法为「接敌」时，视图携带事件能力：`rate_available`、`rate_reason`、`rate_hint`、`rate_action`、`rate_action_label`。只有 `event_status` 为 `recording`/`finished` 才视为可用；`failed`、`unavailable`、`stopped`、`gap`、`attaching`/`ready` 都给出可读原因与恢复入口。
- 实时面板的恢复入口是「重试事件观察」（重新连接采集，不重启应用）或「打开连接设置」；历史记录只提供连接设置入口，并明确该局无法补采。
- 逐行 `rate_state` 区分：`value`（有值）、`no_engagement`（事件覆盖完整且该干员零事件，显示「无接敌」）、`unknown`（覆盖或在场时间不完整）、`unavailable`（事件观察不可用）。`rate_text` 是对应的完整显示文本（`123/s`、`无接敌` 或 `—`），`rate_note` 说明可靠零/未知原因。全周期DPS使用 `presence_seconds`（观察在场秒数）和 `presence_complete`，不以接敌活跃时间冒充。
- 事件不可用时不得以 0、墙钟时间或近 5 秒速率冒充接敌速率；速率选择器仍允许切回「近 5 秒」，并给出原因提示。
- 实时视图的事件能力来自当前样本 `event_status`/`event_error`；历史视图来自 `BattleRecord.event_status`/`event_error` 与逐行 `shield_coverage_start`/`event_coverage_end`。覆盖不完整时注明可靠区间，未覆盖区间保持未知、不补零。
- 事件观察恢复后，同一面板在下一次快照自动更新，无需重建面板或重开窗口。

## 验证

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests/test_panel_model.py tests/test_desktop_bridge.py tests/test_wpf_contract.py -q --assert=plain
```

覆盖：类别切换、总量/实时并列、速率列不影响排行/柱长/占比、线性柱长、原始值排序、颜色与分型、离场灰像、实时零输出移除与回归、未知指标与不完整队伍占比、配置持久化与集合校验；以及事件不可用、事件覆盖中途关闭、某干员零事件、历史记录事件不完整四种接敌展示回归，断言不出现 0 值冒充未知，并在同一面板验证事件恢复。

## 边界

- 护盾最近 5 秒与接敌速率依赖 ARK-5 的事件数据；模型已支持 `recent_shield`，但当前适配器尚未提供。
- 托盘、连接/事件设置与布局持久化见[设置、托盘与窗口配置恢复](settings-and-layout.md)；历史浏览与分享见下节 ARK-12。

## 实时 GUI（ARK-9，Tk 实现已封存）

`App.render` 只将 `sample.statistics` 交给 `rows_from_statistics` / `render_board`，
不使用兼容投影的旧 `sample.rows` 或全局平均 DPS。多面板共享同一份结果，
各自持有不可变 `PanelConfig`，更改配置不修改统计、来源行或其他面板。
新建使用默认配置，复制分配新 ID，删除不影响战局记录；最后一个面板保留。

`StatisticPanel`（WPF 由 `StatisticPanel.xaml.cs` 承担；Tk 版本已封存）只负责原生显示：伤害分段柱、治疗/护盾柱、离场灰像、姓名与最多 1 位小数的数值悬停、有效伤害开关、
按 owner ID 固定的分类/累计/活跃时间/覆盖/来源详情。近期行隐藏时固定详情继续更新，换局清除固定选择。
复用 Canvas 项；鼠标停在列表时保留顺序并持续更新数值，离开后恢复精确排序。
方向键选择，回车固定；外层面板与各列表分别有滚动条。
空态区分等待数据、未知、可靠零和不完整覆盖下的已观察零。
可靠零同时要求调用方 `statistics_complete` 与每个已映射条目的相应覆盖完整；
存在未归属来源时，即使所有已映射条目均为零，也只提示“已观察输出为零，未覆盖部分仍未知”。
提示框按指针所在显示器的工作区限制全部四边，避开任务栏；底部不足时向上展开，
不把高低不同显示器组成的虚拟桌面空白区域当成可见屏幕。

“打开到 HUD”经 WPF 主窗创建或唤起该源面板唯一的紧凑窗口；首次启动不创建 HUD。
HUD 读取源面板的 `config`，不建立第二套采集。
删除源面板前先关闭其 HUD，附面板 ID。

本票没有引入新统计规则或数据源。采集、归属、覆盖仍由现有核心拥有；新增窗口组件
只隔离绘制、鼠标/键盘状态和独立配置，防止把 UI 选择写回共享统计。

### 验证与复现

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests -q --assert=plain
python acceptance/verify_wpf.py --output runtime/wpf-source
```

WPF 验收使用明确标注的离线样本，不操作游戏；截图与回执由 `acceptance/verify_wpf.py` 生成。
旧 Tk 的命令行测量脚本（`verify_gui.py` 等）已随归档封存，不再可用；混合 DPI 热切换仍需宿主机人工覆盖。
全黑截图曾导致验收脚本失败，不能作为视觉通过证据。

## 历史与分享（ARK-12，Tk 窗口已封存）

“历史与分享”打开独立非模态 `HistoryWindow`；无需连接游戏即可读取 `BattleHistory.list_recent()`。
列表显示最近 100 局的关卡、演习/正式/未知、胜负/退出/未知、最后可靠游戏时刻和观察日期；
观察日期缺失时明确使用记录日期，不推断真实开局时间。刷新只更新列表，保留当前所选副本，
包括已经被保留数淘汰或被搜索/结果/模式筛选隐藏的所选局。搜索索引每次刷新构建，不在每次键入时反复复制完整战绩。损坏与不支持版本的读取诊断可见，健康记录继续可读。

历史面板持有独立 `PanelConfig`，只读消费 `BattleRecord.statistics`，不修改 App 的
`last`、`current_battle`、实时源面板或采集生命周期，不把历史结果送入 HUD 回调。
复用 `StatisticPanel` 的类别、排行、速率、原值悬停、离场标记与固定详情，隐藏实时面板管理动作。
窄窗/大字体可收起列表，面板及详情具有外层滚动；列表横向滚动保留完整元信息。

`battle_share.record_complete` 从冻结统计的来源诊断与逐行累计完整性判断队伍状态，沿用
`BattleRuntime.display_sample` 的条件。`last_sample` 是原生输入，不要求带实时兼容投影的
`statistics_complete`。缺少该字段不能让所有完整历史占比都变成未知；新增回归在初版实现上
实际为 1 failed，修复后通过；未知来源仍禁止发布全队占比。

JSON 导出为完整、校验后的 `schema_version=1 BattleRecord`，保留未舍入浮点值、零和 `null`、
口径、来源、事件错误、快照连续区间及逐指标覆盖端点。保存框打开前固定记录和配置，
嵌套事件循环期间选择变化或实时换局不改变此次分享内容。

PNG 从同一记录和 `render_panel` 生成，包含关卡/模式/结果/日期、含过量口径、排行/速率、
事件能力、快照区间、逐行覆盖起止及所有可展示行；未知不绘制成零。Windows GDI 在内存位图
绘制，再用标准库编码 PNG，不新增产品依赖，不读取桌面，不依赖前台遮挡或屏幕大小。
压缩数值与面板一致，原值在 JSON；PNG 的 `ArkDPS.selection` 元信息保留战局、配置和行身份。
早期 Tk 屏幕外捕获的黑图由三项原生 PNG 检查实际发现并修复。

`write_export` 写同目录临时文件并 flush/fsync 后原子提交；同名替换须确认，新文件在 Windows
使用拒绝覆盖的原子重命名（其他平台使用独占硬链接），避免保存期间出现的同名文件被静默覆盖。
取消不产生文件，写入/提交失败保留已有目标与归档。历史目录及解析后的路径别名禁止作为
导出位置，防止覆盖采集器管理的记录。

验证入口（Windows；Pillow 仅供验收截图）：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest tests -q --assert=plain --basetemp=runtime/ark12-full
python acceptance/verify_wpf.py --output runtime/wpf-history
```

原 `acceptance/verify_history.py` 的 Tk 窗口测量已封存；历史页的当前验收由 WPF 回执覆盖。

`tests/test_battle_share.py` 覆盖各结果/模式、归档占比、精度/未知/覆盖、
取消/拒绝/确认替换、磁盘失败、归档保护、保存期间切换、实时换局及 HUD 源面板继续更新、
关闭历史不停止采集。`verify_history.py`（已封存）曾用离线 101 局样本验证最多 100 局、宽窄布局、
全部行分享图、JSON 对账、实时换局隔离和归档字节不变；当前对应证据来自 WPF 回执。

本次只增加只读展示与分享边界；统计、战局身份和历史写入仍由原有模块拥有。

## 紧凑 HUD（ARK-10，Tk HUD 已封存）

`HudWindow` 只拥有原生窗口和交互状态；复用 `StatisticPanel(compact=True)` 的同一绘制组件，
隐藏配置及长详情，保留排序、分段柱、未知占比、部分覆盖提示、悬停数值及独立滚动。
`App.live_statistics` / `live_source_rows` 直接引用最新只读统计结果，不持有第二个统计核心。
`render_huds` 始终用实时引用和源面板配置生成视图；GUI 历史展示可替换其自身的
`statistics` / `source_rows` 并调用 `render_panels`，不会把历史数值发布到 HUD。
换局时重置 HUD 的固定选择和列表位置；类别、排行、速率变化立即跟随源面板。

每个 HUD 独立管理位置、大小和 70–100% 不透明度，始终置顶。GUI 源面板提供锁定入口；
Windows 对 Tk 外层 HWND 设置 `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE`，
解锁移除后两者。原生调用失败不宣称锁定成功。锁定后自身按钮与拖动也穿透。
拖动使用绝对虚拟桌面坐标；Tk 的负号通常表示相对右边距离，副屏负坐标须使用 `+-x`。
`test_drag_across_negative_virtual_desktop_coordinates` 已复现旧拖动写法失败，并验证修正通过。

关闭 HUD 不调用采集会话的关闭/归档/删除接口；删除源面板先关闭其 HUD。
有 HUD 时关闭 GUI 暂时最小化到任务栏，使全体 HUD 锁定时仍可恢复 GUI 解锁；
ARK-11 用托盘补齐最终关闭行为。最后一个 HUD 关闭时若 GUI 不可见，则恢复 GUI。
“退出应用”仍设置共享停止事件，并等待原采集线程关闭自身资源。

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests/test_hud.py -q --assert=plain
python acceptance/verify_hud.py --output runtime/hud-validation
# 受限桌面只做原生命中测试、窗口绘制与生命周期检查：
python acceptance/verify_hud.py --hit-test-only --output runtime/hud-validation-partial
```

验收脚本只使用标明的合成统一快照，不操作游戏、不改系统 DPI；截图只绘制自己的窗口。
默认路径实际通过 Win32 输入点击 HUD、锁定后的下层窗口和再次解锁的 HUD。
`--hit-test-only` 使用 `WindowFromPoint` 验证相同路由，不证明实际鼠标点击或直播捕获；
其回执明确标为 `PARTIAL_INPUT_DESKTOP_UNVERIFIED`，不可作为完整验收通过。
脚本另外检查 GUI/HUD 数值对账、历史隔离、GUI 不可见时换局、独立尺寸和透明度、
源面板删除及 HUD 关闭不停止共享会话；记录 HEAD、源码和截图 SHA-256。

当前执行环境已通过 431 项全量回归，其中 13 项 HUD 测试通过，含锁定后关闭 GUI 的任务栏恢复行为。
受限桌面的原生命中测试完成 HUD → 下层窗口 → HUD 路由，三类 HUD 与 GUI 截图已检查。
实际输入路径被 `GetCursorPos` / `SetCursorPos` 的 Win32 错误 5 阻塞，该环境未能验证单屏直播捕获。
进程启动间歇性报 `CreateProcessAsUserW failed: 5` / `spawnSync EPERM`，串行重试后已完成最终回归。
解除验收阻塞须在可访问输入桌面的宿主会话运行默认验收脚本，并实际核查单屏直播中的
可读性、置顶、拖动、锁定穿透及 GUI 恢复；不能用部分回执替代这份证据。
ARK-10 交付评审时，宿主会话已补齐同候选默认原生点击 `PASS` 回执（路由 HUD → 下层窗口 → HUD）
与 30.7 秒单屏录像；录像捕获工具和来源未登记，不外推到其他直播捕获模式。

## 全局皮肤与原生交互（ARK-19；Tk 皮肤代码已封存，WPF 见桌面运行时）

`ui_theme.py` 只拥有展示；`panel_model.py`、BattleRecord、统计与采集链路未改动。速率切换不改变排行/贡献条/占比分母；主题切换不重新调用排序、不清除 pin/hover、不重建 canvas 行或 native HUD。主题选择在现有布局中持久化，契约见[设置与布局](settings-and-layout.md)。

WPF 侧由 `desktop/ArkDPS.Desktop/App.xaml.cs` 与 `Verification.cs` 覆盖主题切换、HUD 几何锁定不变、
筛选/换局历史隔离与文字对比度；`tests/test_wpf_contract.py` 固定对比度下限。原 `tests/test_ui_theme.py`
与 `acceptance/verify_ui_themes.py` 已随 Tk 归档封存，其窗口/托盘测量不再可运行。**离线回执不是实际游戏
收到点击的证据，也不等于物理托盘图标双击验收。**

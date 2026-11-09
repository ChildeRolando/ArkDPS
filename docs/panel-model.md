# GUI/HUD 共用面板模型与排序口径

实现于 `panel_model.py`；`app.py` / `gui_panel.py` 的实时 GUI 和 `gui_history.py` 历史窗口使用该模型，HUD 复用同一口径。

## 配置与运行状态分离

- 持久化只保存 `PanelConfig`：`panel_id`、类别、排行、速率口径、可选标题；`PanelCollection` 带 `version`，读取时校验版本与重复 id。
- 运行状态只存在于 `PanelView` / `PanelEntry`：数值、柱长、占比、颜色、在场与详情，不回写配置。
- 类别：伤害 / 治疗 / 有效护盾。排行：本局总量 / 最近 5 秒。速率列：近 5 秒 / 接敌；护盾不使用接敌速率（配置直接拒绝）。

## 口径规则

- 排行、柱长、占比分母始终使用排行口径（总量或最近 5 秒），**切换速率列不改变它们**。
- 最高值柱长为 1，其余线性缩放；不使用 Softmax，也不固定柱长之和。
- 排序使用未舍入原始值；显示使用整数与 1/K/M，仅影响显示。
- 伤害条颜色：物理红 `#e5484d`、法术蓝 `#3d8bfd`、元素紫 `#9b5cf6`、真实白 `#ffffff`；累计排行使用 `damage_by_type`，近期排行使用同一近期速率口径的 `recent_damage_by_type`，`segments_complete` 提示分型合计是否与对应排行数值一致。近期分型缺失时分段为空且不完整；详情仍保留累计分型。
- 占比分母为全队同口径总量：总量视图为所有条目本局累计之和，实时视图为所有条目最近 5 秒输出之和；离场、零输出和条目移除都不改变分母。
- 任一条目所选指标未知、累计快照不完整、或调用方报告队伍不完整时，占比返回未知（`share_known=False`），不以局部条目之和冒充全队占比。
- 近期占比还要求每一行（包括零输出）提供相同的 `recent_<category>_window=(start, end)`，该字段由数据提供方保证完整覆盖当前预期窗口 `[max(采集开始时间, 当前时间-5), 当前时间]`。缺失、无效或不一致时隐藏占比并给出窗口原因；排行仍显示已知速率，不能将不同窗口速率之和解释为全队占比。`Statistics` 在新来源或缺口缩短窗口时不提供此证明，完整窗口恢复后才重新提供。

## 列表行为

- 总量列表保留离场干员的累计，头像标记为灰（`portrait_muted=True`）。
- 实时列表只展示最近 5 秒输出大于零的条目；归零即移除，有输出再出现。
- 未知指标不生成占位行，也不写零；输入行不被删除或修改。
- 点击详情包含四类伤害、总量、治疗、有效护盾、活跃时间与归并来源键。

## 验证

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests/test_panel_model.py -q --assert=plain
```

覆盖：类别切换、总量/实时并列、速率列不影响排行/柱长/占比、线性柱长、原始值排序、颜色与分型、离场灰像、实时零输出移除与回归、未知指标与不完整队伍占比、配置持久化与集合校验。

## 边界

- 护盾最近 5 秒与接敌速率依赖 ARK-5 的事件数据；模型已支持 `recent_shield`，但当前适配器尚未提供。
- HUD 实际窗口、托盘、布局持久化由 ARK-10/11 交付；历史浏览与分享见下节 ARK-12。

## 实时 GUI（ARK-9）

`App.render` 只将 `sample.statistics` 交给 `rows_from_statistics` / `render_board`，
不使用兼容投影的旧 `sample.rows` 或全局平均 DPS。多面板共享同一份结果，
各自持有不可变 `PanelConfig`，更改配置不修改统计、来源行或其他面板。
新建使用默认配置，复制分配新 ID，删除不影响战局记录；最后一个面板保留。

`StatisticPanel` 只负责原生显示：伤害分段柱、治疗/护盾柱、离场灰像、姓名与未舍入数值悬停、
按 owner ID 固定的分类/累计/活跃时间/覆盖/来源详情。近期行隐藏时固定详情继续更新，换局清除固定选择。
复用 Canvas 项；鼠标停在列表时保留顺序并持续更新数值，离开后恢复精确排序。
方向键选择，回车固定；外层面板与各列表分别有滚动条。
空态区分等待数据、未知、可靠零和不完整覆盖下的已观察零。
可靠零同时要求调用方 `statistics_complete` 与每个已映射条目的相应覆盖完整；
存在未归属来源时，即使所有已映射条目均为零，也只提示“已观察输出为零，未覆盖部分仍未知”。
提示框按指针所在显示器的工作区限制全部四边，避开任务栏；底部不足时向上展开，
不把高低不同显示器组成的虚拟桌面空白区域当成可见屏幕。

“打开到 HUD”保留入口与明确的未接入提示。ARK-10 可安装 `App.hud_request(panel)`，
读取源面板的 `config` / `view`；不建立第二套采集。HUD 尚未交付时不创建任何 Toplevel。
删除源面板前发出 `<<PanelDeleting>>` Tcl 事件，附面板 ID。

本票没有引入新统计规则或数据源。采集、归属、覆盖仍由现有核心拥有；新增窗口组件
只隔离绘制、鼠标/键盘状态和独立配置，防止把 UI 选择写回共享统计。

### 验证与复现

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests -q --assert=plain
python acceptance/verify_gui.py --scale 100
python acceptance/verify_gui.py --scale 125
python acceptance/verify_gui.py --scale 150
python acceptance/verify_gui.py --scale 200
# 宿主机先在 Windows 显示设置中将至少一块屏幕设为 125%（或用 150）：
python acceptance/verify_gui.py --native-scale 125
```

GUI 测试要求可创建 Tk 窗口。截图脚本另外需要 Pillow，仅用于验收，不是产品依赖。
脚本使用明确标注的离线样本，不操作游戏；遍历真实显示器并覆盖宽窄布局，检查
完整数值、详情、负坐标副屏与高低不同双屏的 tooltip 四边和实际下方面板悬停，
输出截图与 JSON（含 HEAD、代码摘要、工作区、窗口 DPI 和提示框完整边界）。
`--scale` 调整 Tk 字体缩放，**不更改 Windows 显示设置**；它不能证明实际 Windows 缩放验收通过。
`--native-scale` 不覆盖 Tk 缩放，要求至少一块显示器具有所选百分比的真实 DPI，并检查
窗口移动后的 DPI 与该显示器一致。两屏均为 96 DPI 时，`--native-scale 125` 必须以退出码 2
拒绝验收，须在宿主机实际设置后重跑；混合 DPI 热切换仍需单独验证。
全黑截图导致验收脚本失败，不能作为视觉通过证据。

## 历史与分享（ARK-12）

“历史与分享”打开独立非模态 `HistoryWindow`；无需连接游戏即可读取 `BattleHistory.list_recent()`。
列表显示最近 100 局的关卡、演习/正式/未知、胜负/退出/未知、最后可靠游戏时刻和观察日期；
观察日期缺失时明确使用记录日期，不推断真实开局时间。刷新只更新列表，保留当前所选副本，
包括已经被保留数淘汰的所选局。损坏与不支持版本的读取诊断可见，健康记录继续可读。

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
python acceptance/verify_history.py --scale 100 --output runtime/ark12-ui-100
python acceptance/verify_history.py --scale 125 --output runtime/ark12-ui-125
python acceptance/verify_history.py --scale 150 --output runtime/ark12-ui-150
python acceptance/verify_history.py --scale 200 --output runtime/ark12-ui-200
```

`tests/test_battle_share.py` / `tests/test_gui_history.py` 覆盖各结果/模式、归档占比、精度/未知/覆盖、
取消/拒绝/确认替换、磁盘失败、归档保护、保存期间切换、实时换局及 HUD 源面板继续更新、
关闭历史不停止采集、原生 PNG 身份与像素。`verify_history.py` 使用明确标注的离线 101 局样本，
验证最多 100 局、宽窄布局、全部行分享图、JSON 对账、实时换局隔离和归档字节不变，
输出截图及含提交和代码摘要的收据。`--scale` 只调整 Tk 字体，不能替代真实 Windows DPI 或
新的 MuMu 实机验收。HUD 检查针对 ARK-9 的实时源回调契约；实际 HUD 窗口集成由 ARK-10/14 验收。

本次只增加只读展示与分享边界；统计、战局身份和历史写入仍由原有模块拥有。

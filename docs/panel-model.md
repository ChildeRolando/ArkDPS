# GUI/HUD 共用面板模型与排序口径

实现于 `panel_model.py`；`app.py` / `gui_panel.py` 的实时 GUI 已使用该模型，后续 HUD 和历史页面复用同一口径。

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
- HUD 实际窗口、托盘、布局持久化和历史浏览仍由 ARK-10/11/12 交付。

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
```

GUI 测试要求可创建 Tk 窗口。截图脚本另外需要 Pillow，仅用于验收，不是产品依赖。
脚本使用明确标注的离线样本，不操作游戏；遍历真实显示器并覆盖宽窄布局，检查
完整数值、详情、负坐标副屏的 tooltip 位置，输出截图与 JSON（含 HEAD、代码摘要和原生 DPI）。
它调整 Tk 字体缩放，**不更改 Windows 显示设置**；不将这些结果解释为混合 DPI 热切换验收。
全黑截图导致验收脚本失败，不能作为视觉通过证据。

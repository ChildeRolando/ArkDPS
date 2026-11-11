# 工程规则（人读版）

根目录 `AGENTS.md` 是执行器直接读取的硬规则；本页解释背景与边界，二者冲突时以 `AGENTS.md` 为准。

## 为什么封存 Tk

ARK-19 之后产品 UI 已经是 C# / .NET 10 / WPF，但仓库长期保留 `--legacy-tk` 入口、Tk 模块、Tk 测试、
Tk 验收脚本与 PyInstaller 构建链。这会让后续 agent 与测试重新打开旧面板，产出与 WPF 候选不一致的
截图和回执，验收证据无法互相对账。ARK-27 因此把 Tk 整体归档到 `archive/legacy-tk/`，见
[ADR 0005](adr/0005-seal-legacy-tk.md)。

## 当前唯一路径

| 目的 | 命令 |
| --- | --- |
| 源码运行产品 | `start.cmd` 或 `python app.py`（WPF） |
| 无界面采集 | `python app.py --capture-seconds <秒> ...` |
| 回归基线 | `python -m pytest tests -q` |
| UI 验收 | `python acceptance/verify_wpf.py --output <新目录>` |
| 打包 | `python tools/package_windows.py --output runtime/packages` |
| 包验收 | `python acceptance/verify_wpf_package.py <zip> --output <新目录>` |

`--legacy-tk`、`--acceptance-output`、`--acceptance-seconds`、`--acceptance-panels`、`--self-test`
都会以退出码 2 明确报错并指向 WPF 验收命令；它们不是"暂时隐藏"的开关，不要尝试恢复。

## 证据规则

- 只有 WPF 回执、WPF 截图、`verify_wpf*.py` 的输出可以作为当前候选证据。
- Tk 截图、Tk 自检回执、旧 ARK-14 候选结论属于历史材料，引用时必须标注"已封存，不作为当前证据"。
- 离线回执只覆盖合成数据；实机、物理托盘、混合 DPI 仍需独立验收并在工单里如实标注未覆盖项。

## 归档规则

- `archive/legacy-tk/` 由根目录 `conftest.py` 排除在 pytest 收集之外，也不在 `tools/package_windows.py`
  的 `BACKEND` 白名单里；`tests/test_no_legacy_tk.py` 会同时断言这两点。
- 归档只作追溯，不维护、不修复、不被活跃代码导入。需要理解旧行为时读代码与归档 README。
- Git 历史保留；不要求从历史中抹除 Tk，也不做再次删除。

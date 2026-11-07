# 连接配置与 MuMu 自动检测

实现于 `connection.py`，供 GUI（后续桌面壳工单）与采集器共同使用。

## 目标与边界

- 首版只自动选择已认证的 MuMu 实例；其他安卓设备会列出但不会自动连接，可在连接设置中手动指定。
- 检测只执行 `adb devices -l`、`getprop`、`settings get secure android_id`、`dumpsys package` 等只读命令；不重启模拟器、不执行 `adb kill-server`、不删除其他工具的端口转发。
- `adb` 可达不等于统计兼容。连接状态与游戏版本状态分字段报告；版本不匹配时采集器抛出 `UnsupportedVersion`，拒绝发布任何数值。

## 配置

`ConnectionConfigStore` 默认读写 `runtime/connection.json`（不提交）：

```json
{
  "version": 1,
  "adb_path": "C:\\Program Files\\Netease\\MuMu Player 12\\nx_main\\adb.exe",
  "serial": null,
  "auto_detect": true
}
```

写入采用同目录临时文件 + `os.replace` 原子替换；损坏文件返回默认配置并给出警告，不静默覆盖原文件。`serial` 非空时优先使用手动指定实例，否则按 `adb_path` 自动检测。

手动 TCP 地址尚未在 adb 注册且探测报告设备不存在时，执行一次有超时的 `adb connect`，再重新检查连接和版本；USB 序列及已注册的离线设备不会因此重连。

## 自动检测

1. 按“显式配置 → 环境变量 → 常见 MuMu 安装路径 → PATH”解析 `adb.exe`。
2. 解析 `adb devices -l`，保留 offline / unauthorized / no permissions 等不可访问状态。
3. 对可访问设备读取型号与 `android_id`；MuMu 常同时暴露 `127.0.0.1:16384` 与 `emulator-5554`，相同 `android_id` 合并为一个实例，优先选择回环端口。缺失或占位标识（如 `null`）不参与合并；回退硬件序列，两者均缺失时保留独立候选。
4. MuMu 认证依据：`127.0.0.1:16384+32n` 序列、MuMu 安装目录下的 adb、或设备属性含 mumu/netease。仅因 adb 来自 MuMu 目录不会把 USB 真机误判为 MuMu。
5. 结果状态：`ready_unique`（唯一实例自动选中）、`needs_selection`（多实例，等待玩家选择）、`no_instance`、`no_certified_instance`、`adb_missing`、`adb_inaccessible`、`error`。

## 诊断契约

`ConnectionDiagnostics.as_dict()` 提供可直接序列化的状态，GUI 可据此显示：

| 字段 | 含义 |
| --- | --- |
| `status` | 检测阶段结果 |
| `adb_path` / `selected_serial` | 当前解析出的 adb 与实例 |
| `devices` / `instances` | 原始设备与去重后的实例（含不可访问原因） |
| `connection_ok` | adb 可达且游戏进程可验证 |
| `version_status` | `compatible` / `incompatible` / `not_installed` / `unknown` |
| `version_expected` / `version_actual` | 适配版本与实际版本 |
| `message` / `error` | 面向用户的诊断文本 |

采集器在 `connect()` 后设置同一结构；`ConnectionTarget.require_collectable()` 在版本不匹配时抛出带诊断的 `ConnectionUnavailable`。

`ForwardRegistry` 使用 `adb forward --no-rebind`，占用端口时报告冲突；释放前检查当前映射仍与登记值一致，查询失败时保留登记以便重试。adb 不提供映射所有权令牌或原子条件删除，因此外部程序若在检查与删除之间替换同一端口，或替换为完全相同的映射，无法由此接口辨认。

## 验证

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests/test_connection.py -q --assert=plain
```

单元测试覆盖零个/唯一/多个/不可访问实例、别名去重、手动覆盖、版本不匹配、配置持久化、危险 adb 命令拒绝，以及只删除自有转发。

本机实测（MuMu 12 / Android 2.7.91）`detect_instances()` 返回 `ready_unique`，两个别名合并为一个实例并自动选择 `127.0.0.1:16384`；`check_version()` 返回 `compatible`。未在真实多开环境启动第二个实例，多个实例的选择路径由测试替身覆盖。

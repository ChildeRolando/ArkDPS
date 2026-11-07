# Reviewer 和编码执行器交接

将本页的 `<入口>` 和 `<配置>` 替换为本实例已预检的 `review-queue.mjs` 与本地配置路径。多 Reviewer 共用同一入口。选择在可访问工作树和结果目录的 Multica runtime 中运行。

**编码执行器**：完成本票验收并正常推送候选。通过 Multica 将当前业务票置为 `in_review`，更新状态时用 `--no-start`。Git 模式执行 `node <入口> register-git <配置> <当前业务票> refs/heads/<候选分支>`；GitHub 模式执行 `node <入口> register <配置> <PR 编号> <当前业务票>`。结束当前 run，等待 Reviewer。收到修复交接时，使用 `node <入口> context <配置> <当前业务票>` 读取 `stage: fix` 和 `previous_review`，在作者工作树修复可复现的 P1/P2，保留已通过验收和未受影响的结论。正常推送同一分支的新 head 并重新登记。

**Reviewer**：收到票后执行 `node <入口> context <配置> <当前业务票>`。若有可信的 Agent、run ID，可传 `--agent-id`、`--run-id` 绑定上下文。确认 `stage: review`，核对 `worktree` 的 HEAD，按业务票的接受条件、仓库规则和 `git diff <base_sha>...<head_sha>` 审核。只读跟踪文件，运行与改动有关的检查。P1/P2 写清触发条件、影响和最小修复；缺关键环境或证据返回 `BLOCKED`。把 UTF-8 JSON 先写同目录临时文件，再重命名到 `result_path`。只提交结果文件；状态、派发与可选交付归中台。

```json
{
  "head_sha": "<context.head_sha>",
  "base_sha": "<context.base_sha>",
  "verdict": "PASS",
  "summary": "本次独立评审结论",
  "findings": [],
  "checks": [{"command": "实际执行的检查", "status": "PASS", "exit_code": 0}],
  "limitations": []
}
```

`PASS` 至少有一项实际通过的检查，不得包含 P1/P2 或失败检查。`CHANGES_REQUIRED` 必须包含有证据的 P1/P2。发现用 `severity`、`path`、`line`、`problem`、`evidence` 和 `fix` 表述。评审结果通过不表示已经合并或关票。

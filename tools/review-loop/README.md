# Multica Review Loop

把这个目录放进任意 Git 仓库。需要 Node.js 22+、Git 和已登录的 Multica CLI；不需要安装 npm 包。审核要求来自同一张 Multica 工单，审查对象是 Git 远端的分支和基线。编码结束 → 独立审核 → 有阻断退回同票修复 → 用新提交复审。Reviewer 只写结果，中台消费结果并交接。实际阻塞保留证据并停止，不切到另一个模型。

## 配置与启动

1. 在仓库根目录将 `tools/review-loop/config.example.json` 复制为同目录的 `review.local.json`。填入仓库名、Multica 工作区与项目 UUID、Reviewer 与编码执行器 UUID。也可填 `project_ids` 扩展项目范围。CLI 不在 PATH 时填 `multica_path`；需要指定登录环境时填 `profile`。相对路径以配置文件目录为准；每个实例使用独立 `state_dir`，不复用其他工作区的队列或结果。
2. 执行下列只读预检，确认它回读了正确的 workspace、project、agent 和 Git 基线。模板默认停用；预检不会创建队列、更新工单或启动 Agent。
3. 将 [角色交接说明](agent-roles.md) 接到本工作区的编码执行器和 Reviewer 指令，填实际入口、配置路径。Agent 需要能读取评审工作树、配置及自己的结果目录。审核工作树使用独立 detached checkout；编码和修复继续在原作者目录进行。
4. 准备好后将 `review.enabled` 改为 `true`。用现有调度器周期执行同一条 `reconcile` 命令（例如每分钟一次）；也可手动执行验证。入口可重跑，未知派发结果不会盲目重发。没有登记候选时不会启动 Agent。

```powershell
node tools/review-loop/review-queue.mjs check tools/review-loop/review.local.json
node tools/review-loop/review-queue.mjs reconcile tools/review-loop/review.local.json
```

启用后的 Git 模式：

- 编码执行器正常推送分支，把业务票置为 `in_review`（`--no-start`），并调用 `register-git <config> <工单编号或 UUID> refs/heads/<分支>`。只在此时明确关联分支；分支名和 PR 文字都不是工单归属证明。
- `context <config> <工单>` 返回当前阶段、审核工作树、完整 base/head SHA、结果路径及上一轮发现。`status <config> <工单>` 回读已登记候选的状态和结果；`PASSED` 表示这个已登记候选通过，不表示已合并或已关票。主分支、候选或工单验收要求变化后，须登记新候选。
- `retry <config> <工单>` 只用于能证明旧运行结束的失败评审，或确定尚未派发的准备失败。对未知派发结果，先检查 Multica 运行记录；不要删除队列状态来重发。停止调度并设置 `review.enabled: false` 即可停用，证据仍留在原目录。

可用 `review.engine: "multi"` 和 `review.mode: "priority"` 设 Reviewer 池。`review.reviewers` 按优先顺序列出 `{ "key": "local", "agent_id": "<UUID>", "max_concurrency": 1 }`；每轮由首个有空位的 Reviewer 裁决，仍在 Multica 运行。单 Reviewer 配置用 `reviewer_id` 即可。工具不需要知道 Agent 使用哪个模型。

## 可选：保留 GitHub 的原有自动交付

使用 GitHub PR 作为候选时，设 `review.candidate_source: "github"`，`repository` 填 GitHub 的 `owner/name`，Git 的 `origin` 指向同一仓库，`gh` 登录后可访问它。建议设 `review.issue_prefixes` 为本工作区的工单前缀列表（如 `["DEMO"]`）。这个模式仍支持 `register <config> <PR> <工单>`，也会发现正文或标题唯一关联已进入 `in_review` 且编码运行结束的非草稿 PR。

需要自动交付时再显式加入 `"delivery": { "enabled": true, "merge_method": "squash" }`。此时中台发布评审和检查，按 GitHub 的保护规则合并，读回已合并后关闭关联业务票。只给 PR 合并就能满足全部结束条件的项目开启自动关票；仍要部署或实机验收的项目用审核模式。不开自动交付时没有 GitHub 写操作。两种候选来源各有自己的本地队列；切换来源时应先结束在途任务，不导入其他实例的历史。

本工具从已运行的 Review Loop `2099c95c58cdd46b8d28bd68ddab622a9fe286ca` 提取同票审核队列、Reviewer 池与可选 GitHub 交付。它是独立部署入口；已有实例继续用其原来固定的版本和配置。仓库产品验收标准仍归产品文档和工单所有。

测试：`node --test "tools/review-loop/*.test.mjs"`。测试用本地 Git 远端和模拟 Multica，覆盖同票修复、候选变化与可选 GitHub 交付，不消费真实 Agent 配额。

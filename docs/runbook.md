# AfterPilot Runbook

## 启动/停止
- 本地：`python scripts/dev.py` / `python scripts/stop.py`（日志 var/{shop,api,worker}.log/.err.log）
- 生产：`docker compose -f deploy/docker-compose.yml up -d --build`；worker 可水平扩（DB 租约队列）

## 日常运维
- 审批：控制台"审批收件箱"或 `POST /api/admin/approvals/{id}/decide {decision,reason}`（需 X-Admin-Token）
- 转人工闭环：控制台"转人工闭环"→ 填纠正 expect JSON → 采纳为评测用例（candidate→active）
- KB 更新：`POST /api/admin/kb {slug,title,body}` 自动版本+1；旧版本保留可审计
- 故障注入（演练）：`POST :8001/admin/fault {mode: latency|error|timeout_after_commit, sleep}`；恢复 `{mode:none}`
- 下游重置（仅演练）：`POST :8001/admin/reset`

## 发布与回滚
1. 改 prompt/工具/模型 => 新版本号；`python scripts/run_evals.py --version vN --gate` 必须过
2. `POST /api/admin/releases {spec,version,mode:shadow}` 干跑（写模拟、不发回复）
3. 过评测后 `mode:canary,percent:5..50`；护栏（转人工率>50% 或工具错误率>30%，近窗）自动回滚
4. 稳定后 `mode:ga`；手动回滚 = 重新发布上一个 ga 版本（set_release 会校验评测）
- 演练：`python scripts/drill_release.py`（报告 var/drill_release.json）

## 故障处置
- worker 崩溃：租约到期自动回收续跑；写工具 intent=executing 会先 query-before-retry 对账
- intent=unknown：禁止自动重试；人工到下游按 idempotency_key 对账后决定补偿/放行，记录审计
- 工具熔断：连续 3 次失败开断 30s；期间工单降级转人工（不编造下游状态）
- 评测门禁红：禁止发布；回滚到 baseline 版本；用 holdout 集排查过拟合
- chaos 复现：`python -m pytest tests/test_chaos.py -q`

## 事故复盘
用 docs/postmortem-template.md；演练事故也写复盘（标注"演练"），保留 trace_id 与审计链。
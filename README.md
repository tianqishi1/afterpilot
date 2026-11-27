# AfterPilot — 电商售后智能处理 Agent 系统

[![ci](https://github.com/tianqishi1/afterpilot/actions/workflows/ci.yml/badge.svg)](https://github.com/tianqishi1/afterpilot/actions/workflows/ci.yml)


> **生态**：底座 [agent-base](https://github.com/tianqishi1/agent-base) · 评测 [eval-pipeline](https://github.com/tianqishi1/eval-pipeline) · 技能 [marketing-skills-cn](https://github.com/tianqishi1/marketing-skills-cn)

生产级售后工单 Agent：确定性状态机做骨架、单 Agent + MCP 工具做决策；资金动作走
"策略引擎硬规则 + 人工审批 + 幂等执行"；自带三层评测回归门禁、全链路 trace、按
"每个有效解决"核算的成本账本、人工纠正→评测用例闭环、YAML 平台化与 shadow/canary/回滚发布。

## 快速开始（本地，零外部依赖）
```powershell
pip install -r requirements.txt
python scripts/dev.py          # mock-shop:8001 + api/控制台:8000 + worker
# 浏览器打开 http://127.0.0.1:8000/  （admin token 默认 dev-admin-token）
python scripts/stop.py         # 停止
```
默认 `MODEL_PROVIDER=mock`（离线决策替身，流程/工具/安全/评测全真）。接真实模型：
```powershell
$env:MODEL_PROVIDER="openai"; $env:DASHSCOPE_API_KEY="sk-..."   # 或 OPENAI_API_KEY + OPENAI_BASE_URL
python scripts/dev.py
```
生产部署（Postgres + Prometheus + Grafana）：
```powershell
docker compose -f deploy/docker-compose.yml up --build
# 控制台 :8000  Grafana :3000 (admin/admin)  Prometheus :9090  mock-shop :8001
```

## 验证（全部可重复执行）
```powershell
python -m pytest -q                                   # 27 项：单元/集成/chaos/红队/评测门禁
python scripts/run_evals.py --suites unit scenario redteam --gate --baseline deploy/eval_baseline.json
python scripts/calibrate_judge.py                     # judge 与人工标注 Cohen κ >= 0.7 才可信
python scripts/drill_release.py                       # shadow -> canary -> 护栏自动回滚 演练
```
chaos 验收：退款下游已 commit 但 worker 未 checkpoint 时 kill -9，租约过期后续跑，
**不产生双退款**（tests/test_chaos.py）。

## 架构
```
chat/API -> Ticket 状态机(确定性) -> 分诊(小模型) -> Agent loop(<=6步, 逐步checkpoint)
   Action = reply | tool_call | request_approval | escalate   (Pydantic 强校验+修复环)
工具边界: allowlist -> JSON Schema -> intent log -> 策略引擎 -> 幂等执行(query-before-retry)
守护: 审批门(payload_hash 绑定) / 输出合规 / 注入隔离(用户文本=数据) / PII 脱敏 / 审计
横切: Postgres(sqlite dev) / DB租约队列 / DB spans trace / 成本账本 / /metrics / 评测门禁
闭环: 转人工&纠正 -> 候选评测用例 -> 人工审入库 -> 回归
平台化: agents/*.yaml 定义 Agent；loop/评测/部署/观测为共享组件
```

## 目录
- `packages/core` 状态机、loop、工具/网关、策略、审批、trace、成本、KB、发布、闭环
- `packages/evals` runner/judge/datasets（unit/scenario/redteam + holdout）
- `apps/api` HTTP + 管理台 API + /metrics；`apps/worker` 租约队列 worker
- `services/mock_shop` 下游模拟 + 故障注入（latency/error/timeout_after_commit）
- `mcp_servers/shop_tools.py` 真实 MCP stdio server（同一工具注册表）
- `web` 零构建控制台：聊天/队列/审批/trace/评测/成本/闭环/KB/发布
- `deploy` compose/Dockerfile/Prometheus/Grafana/CI 门禁基线
- `docs` TASKS 板、ADR、runbook、一页纸、postmortem 模板、人工标注种子

## 生产指标口径
- 自动解决率 = 无人工介入结单且 7 天无 reopen/负反馈（MVP 目标 >=60%，mock 流量下先校准口径）
- 转人工率 <=35%；资金错误动作率 = 0（硬约束，靠策略+审批而非模型）
- 单有效解决成本 = (模型 token + 工具 + 人工分钟摊销) / 有效解决数
- 首响 P95 <=5s；端到端 P95 <=60s（不含审批等待）

## 安全模型
用户消息与检索文档一律为不可信数据；工具白名单按 Agent 绑定；读/写权限分离；
退款硬规则（金额<=实付、15 天窗、30 天 2 次、>200 元审批、停滞提前退款审批）在写边界代码强制；
审批凭 payload_hash 绑定参数；写超时先查下游再决定重试，未知状态转人工禁止盲重试；
trace/评测快照入存前 PII 脱敏；全链路审计。
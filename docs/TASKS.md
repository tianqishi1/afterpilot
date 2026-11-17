# AfterPilot 任务板（M0–M4 → 可执行任务）

状态: [ ] 待办 / [~] 进行中 / [x] 完成并验证
验收以 tests/ 与 scripts/ 的可重复执行为准，不口头验收。

## M0 骨架与确定性基线
- [ ] T001 monorepo 脚手架、配置、SQLAlchemy 模型、DB 初始化（sqlite  dev / postgres prod）
- [ ] T002 mock-shop：订单/物流/退款/发票 API + 种子数据 + 故障注入（latency/error/timeout_after_commit）
- [ ] T003 工单确定性状态机 + DB 租约队列（sqlite BEGIN IMMEDIATE / pg SKIP LOCKED）
- [ ] T004 无 LLM 确定性 FAQ 基线 + /api/chat + Web 聊天视图 + 基线指标
- [ ] T019 Docker Compose + Dockerfile + Prometheus + Grafana provisioning

## M1 Agent 端到端与资金安全
- [ ] T005 版本化政策 KB + BM25 检索（pgvector 为演进项，见 ADR-003）
- [ ] T006 工具注册表（JSON Schema + 权限 scope + 超时/重试/熔断）+ LocalGateway + MCP server 封装
- [ ] T007 LLM Provider 接口：mock（离线可跑）+ OpenAI 兼容（DashScope/OpenAI）+ 结构化输出校验与修复环
- [ ] T008 Agent loop（≤6 步）+ 逐步 checkpoint + 崩溃续跑
- [ ] T009 策略引擎硬规则 + 幂等退款（idempotency_key + intent log + query-before-retry）
- [ ] T010 审批收件箱 + 审批决定后恢复 run
- [ ] T020a 单元/集成测试：状态机、策略矩阵、幂等、队列租约、e2e 五类工单

## M2 评测体系与门禁
- [ ] T014 三层评测集（unit/scenario/redteam）+ runner + judge（规则+LLM）+ 人工标注校准（Cohen κ）
- [ ] T015 CI 回归门禁：baseline 对比，核心指标跌 >2pp 阻断；holdout 集
- [ ] T020b 评测测试：改坏 prompt 版本 CI 能拦

## M3 生产可靠性与可观测
- [ ] T012 DB spans 全链路 trace + 成本账本（token+工具+人工分钟）+ /metrics
- [ ] T013 PII 脱敏 + 注入隔离（用户文本=数据）+ 输出合规检查 + 审计日志
- [ ] T011 管理后台：队列/审批/Trace/成本/Handoff/KB 视图
- [ ] T020c 混沌测试：退款中途 kill -9 worker，重启不双退；红队资金类 100% 拦截

## M4 闭环、平台化与发布演练
- [ ] T016 转人工/人工纠正 → 候选评测用例 → 人工审入库 流水线
- [ ] T017 Agent 定义 YAML + 第二个 Agent（invoice_only）复用 loop/评测/部署
- [ ] T018 发布模式 shadow/canary/ga + 护栏自动回滚 + 演练脚本
- [ ] T021 文档：README / runbook / ADR / 一页纸 / postmortem 模板
- [ ] T022 本地全链路冒烟 + 评测报告 + 指标截图材料
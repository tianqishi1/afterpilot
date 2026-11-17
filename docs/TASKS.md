# AfterPilot 任务板（M0–M4 → 可执行任务）

状态: [x] 完成并验证 / [~] 部分 / [ ] 待办。验收以 tests/ 与 scripts/ 可重复执行为准。
验证基线：pytest 27/27；evals unit=1.0 scenario=1.0 redteam=1.0；judge κ=1.0(mock 标注种子)；
chaos 不双退；release 演练 ok；Postgres SKIP LOCKED + Compose api/worker/mock-shop 实测通过。

## M0 骨架与确定性基线
- [x] T001 monorepo/配置/模型/DB（sqlite dev + postgres prod 双方言实测）
- [x] T002 mock-shop + 种子 + 故障注入（latency/error/timeout_after_commit；by-key 对账口不受故障影响）
- [x] T003 状态机 + DB 租约队列（sqlite 原子 UPDATE..RETURNING / pg SKIP LOCKED；租约回收 chaos 验证）
- [x] T004 /api/chat + Web 聊天 + 确定性分诊基线（triage_rules 亦为 M0 无 LLM 基线）
- [x] T019 Compose/Dockerfile/Prometheus/Grafana provisioning（镜像构建+up+PG 冒烟+worker e2e 实测；
      Grafana/Prometheus 容器本轮未起，镜像已换 daocloud 源，见"下一步"）

## M1 Agent 端到端与资金安全
- [x] T005 版本化 KB + BM25（CJK bigram）；run_suite 新库自动 seed 保证环境一致性
- [x] T006 工具注册表（Schema+scope+超时/重试/熔断）+ LocalGateway + 真实 MCP stdio server
- [x] T007 Provider 接口 mock/openai + 结构化输出校验与 2 次修复环（openai 路径待 key，见下一步）
- [x] T008 loop <=6 步 + 逐步 checkpoint + 崩溃续跑（chaos 验证）
- [x] T009 策略引擎写边界硬规则 + 幂等键 + intent log(executing 前置) + query-before-retry + unknown 禁盲重试
- [x] T010 审批收件箱 + payload_hash 绑定 + 批准/拒绝恢复 run（HTTP 实测）
- [x] T020a 单元/集成测试 27 项全绿

## M2 评测体系与门禁
- [x] T014 三层评测集 + holdout + runner + judge(规则地板+LLM) + 校准脚本（κ>=0.7 才可信）
- [x] T015 CI 门禁（.github/workflows/ci.yml + deploy/eval_baseline.json；跌>2pp 阻断；改坏版本被拦测试验证）
- [x] T020b gate 阻断降级版本测试通过

## M3 生产可靠性与可观测
- [x] T012 DB spans trace + 成本账本(含人工摊销) + /metrics（Prometheus 文本，Grafana 看板 json）
- [x] T013 PII 脱敏 + 注入隔离 + 输出合规 + 审计（红队 10/10，资金类 0 泄露 0 错退）
- [x] T011 管理台九视图（聊天/队列/审批/trace/评测/成本/闭环/KB/发布）
- [x] T020c chaos kill -9 不双退 + worker 轮询线程 DB 瞬断自愈（Compose 实测抓出并修复）

## M4 闭环、平台化与发布演练
- [x] T016 转人工/纠正 → candidate → 人工审 active/retired（HTTP+测试验证）
- [x] T017 agents/*.yaml + invoice_only 第二 Agent 复用共享 runtime
- [x] T018 shadow/canary/ga + 评测门禁晋升 + 护栏自动回滚（drill_release.py 全 ok）
- [x] T021 README/ADR/runbook/一页纸/postmortem 模板/人工标注种子
- [x] T022 本地全链路冒烟（物流/审批/超窗/注入/指标/控制台）+ 评测报告 + 基线落盘

## 下一步（明确未做，不冒充已验证）
- [ ] T023 Grafana/Prometheus 容器起栈验证看板（镜像源已配；本轮只验证了 api/worker/pg/mock-shop）
- [ ] T024 接 DASHSCOPE_API_KEY 跑真实模型评测+judge 校准（网络到 DashScope 已验证可达；OpenAI 直连超时）
- [ ] T025 真实店铺适配器（Shopify dev store 优先）+ 契约测试；mock 流量指标口径校准后再报真实业务指标
- [ ] T026 生产化演进：Redis Stream 队列/语义缓存、pgvector 检索、OTel+Langfuse 导出器、async 化（见 ADR）
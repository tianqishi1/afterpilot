# 架构决策记录（ADR）

## ADR-001 MVP 用 DB 租约队列，不引入 Redis
- 背景：方案原稿写 Redis 做队列/缓存/限流。
- 决策：MVP 队列 = jobs 表 + 租约（sqlite 原子 UPDATE..RETURNING；postgres FOR UPDATE SKIP LOCKED）。
  租约过期自动回收 => 崩溃安全（chaos 测试验证）。缓存/限流 MVP 用进程内实现。
- 理由：少一个外部依赖 = 少一类生产故障面；单机吞吐足够（售后工单 QPS 低）。
- 演进：多 worker 跨机扩容时换 Redis Stream / SQS，queue.py 接口不变。

## ADR-002 管理台零构建（vanilla JS），不上 React/Vite
- 理由：控制台是运维界面而非消费级产品；零构建 = 无 node 供应链/构建故障面，compose 镜像更小。
- 演进：如需组件化再迁 React，API 契约不变。

## ADR-003 检索基线 = BM25（CJK char-bigram），pgvector 为演进项
- 理由：政策 KB 规模小（数十文档），BM25 可解释、零依赖、评测可复现；向量检索收益需评测证明后再加。
- 契约：kb.search 返回 {slug, version, snippet, score}，换实现不改工具接口。compose 已用 pgvector 镜像预留。

## ADR-004 Trace = DB spans（OTel 形状），Langfuse/collector 为导出适配器
- 理由：trace 必须与工单/审计同库可 join（"任一 trace 可还原决策现场"）；self-host Langfuse 需
  clickhouse+minio 等重依赖，MVP 不背。spans 表字段对齐 OTel span，导出器是纯增量。
- 演进：OTel SDK + Langfuse exporter；/metrics 已为 Prometheus 文本格式，Grafana 看板已 provision。

## ADR-005 同步核心 + 线程池 worker，不上 async
- 理由：售后链路 IO 次数少（<=6 步）、并发低；同步代码的 checkpoint/幂等推理更简单、测试更确定。
  FastAPI sync endpoint 走 threadpool，worker 多线程 + DB 租约。
- 演进：高并发时迁 async + 连接池，loop 接口不变。

## ADR-006 mock provider 是离线测试替身，不是"假 Agent"
- loop/工具/策略/审批/checkpoint/评测在 mock 与 openai provider 下完全同构；mock 只替换"模型决策"。
- 用途：无 API key 也能完整运行/演示/跑评测与 chaos；接真实模型只改 MODEL_PROVIDER=openai。
- 纪律：对外指标报告必须标注 provider；mock 指标只代表流程正确性，不代表模型质量。

## ADR-007 资金硬规则放在写工具边界（policy engine），不放在 prompt
- 模型输出（含被注入后的输出）永远无法绕过：allowlist -> schema -> intent log -> policy -> 幂等执行。
- 超阈值/超政策 => NeedsApproval => 人工审批；审批凭 payload_hash 绑定，改参数即失效。
- 超时但可能已执行 => query-before-retry；仍未知 => intent=unknown => 转人工，禁止盲重试。
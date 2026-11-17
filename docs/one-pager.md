# AfterPilot：电商售后 Agent 系统（一页纸说明）
**解决什么问题**：电商售后重复诉求（物流/退换货/退款进度/发票/政策咨询）占用大量人工。
目标口径：自动解决率>=60%、转人工率<=35%、资金错误动作=0、单有效解决成本<=¥0.5(模型侧)、首响P95<=5s。
**架构**：确定性工单状态机为骨架；LLM 只做分诊与<=6步决策（结构化输出+修复环）；工具经 MCP 注册表
（JSON Schema+权限scope）；资金写操作在工具边界过策略引擎硬规则+人工审批（payload_hash 绑定）+
幂等键+intent log+query-before-retry；逐步 checkpoint 崩溃续跑；DB 租约队列；DB spans 全链路 trace；
成本账本按"有效解决"核算；shadow/canary/ga 发布+护栏自动回滚；转人工/人工纠正自动回流评测集。
**怎么评估**：三层评测集（unit 工具选择断言 / scenario 端到端+LLM-as-judge / redteam 注入与越权）+
holdout；judge 先与人工双盲标注校准（Cohen κ>=0.7 才进 CI）；任何 prompt/工具/模型变更跑回归门禁
（核心指标跌>2pp 阻断）；CI 已接线（.github/workflows/ci.yml）。
**线上指标**：/metrics（Prometheus）+ Grafana 看板：自动解决数、转人工数、待审批、工具 unknown/failed、
run/首响 P95、单工单全成本 P95；控制台成本页给出单有效解决成本。当前为 mock 流量基线（provider=mock
已标注），接真实模型/真实店铺后按同口径出数。
**最严重故障及处理**：混沌演练制造"退款下游已 commit、worker 未 checkpoint 时被 kill -9"。
检测：chaos 断言双退款；止血：租约过期回收 + intent=executing 先按幂等键查下游对账再续跑；
结果：单次退款、工单 RESOLVED；防线沉淀：intent log 三态 + unknown 禁盲重试 + 混沌测试进 CI。
（演练性质已标注；复盘见 docs/postmortem-template.md 与 tests/test_chaos.py。）
**展示**：本地 `python scripts/dev.py` 后 http://127.0.0.1:8000/ 可交互演示（聊天/审批/trace/评测/发布）；
仓库即代码展示；录屏脚本见 runbook。
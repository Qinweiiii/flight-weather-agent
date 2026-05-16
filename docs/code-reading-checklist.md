# 项目代码阅读清单（航班-天气多智能体）

这份清单用于按最省心的顺序阅读代码，目标是先跑通主链路，再理解每个模块的职责与边界。

## 使用方式

- 按顺序执行，不建议跳读。
- 每看完一组文件，勾选该组并写 1-2 句自己的理解。
- 如果某步卡住，先看本清单中的“看完要确认”再继续。

---

## 阶段 1：建立全局认知（先知道系统怎么启动）

- [ ] 查看 config/config.yaml
- [ ] 查看 app.py
- [ ] 查看 agent.py
- [ ] 查看 start_web.bat

看完要确认：

1. 模型配置、数据库路径、搜索配置分别在哪里定义。
2. Web 服务入口和启动命令是什么。
3. 请求是如何从 API 进入智能体主流程的。

---

## 阶段 2：主编排链路（最关键）

- [ ] 查看 agents/master_agent.py

建议重点函数：

1. __init__：子 Agent 初始化、配置注入。
2. _build_graph：LangGraph 节点与边定义。
3. stream_query：SSE 事件推送流程。
4. _call_search_and_sql_node：SQL + 搜索 + Debate 联合路径。
5. _compute_confidence_label：可信度分级逻辑。

看完要确认：

1. 一次请求从意图识别到最终回答经过哪些节点。
2. 哪些场景会触发 search_and_sql。
3. 质量卡、trace、sources 等前端事件从哪里发出。

---

## 阶段 3：核心子 Agent（按数据流顺序）

### 3.1 SQL 生成与执行

- [ ] 查看 agents/sql_agent.py

建议重点函数：

1. query：SQL 生成、执行、重试主入口。
2. 时间锚点相关函数：最近 N 天映射到数据最大日期。
3. SQL 安全校验相关逻辑：只读约束与危险语句拦截。
4. 反射纠错逻辑：执行失败后基于错误反馈重写 SQL。

看完要确认：

1. SQL 最多重试几次。
2. 查询失败时返回结构是什么。
3. 数据观测期是如何被标注的。

### 3.2 结构化分析与图表

- [ ] 查看 agents/analysis_agent.py

看完要确认：

1. 什么时候会进入分析 Agent。
2. 输出文本与图表配置如何组织。

### 3.3 联网搜索与证据门控

- [ ] 查看 agents/search_agent.py

建议重点函数：

1. 搜索请求构造与领域词约束。
2. 证据质量判断（strong/proxy/none）。
3. 低证据时的降级输出策略。

看完要确认：

1. evidence_enough 是怎么判定的。
2. 何时给方向性参考，何时不给确定性结论。

### 3.4 证据裁决与质量审校

- [ ] 查看 agents/debate_agent.py
- [ ] 查看 agents/critic_agent.py
- [ ] 查看 agents/guardrail_agent.py

看完要确认：

1. Debate scorecard 各项分值如何计算。
2. low_evidence 输出模板由哪些输入拼装。
3. Critic 评分维度是什么。
4. Guardrail 在哪里阻断高风险请求。

---

## 阶段 4：协同与路由增强

- [ ] 查看 agents/planner_agent.py
- [ ] 查看 agents/tool_router_agent.py

看完要确认：

1. Planner 与 Router 在当前版本中的实际参与度。
2. 是否存在“可删但未删”的历史逻辑。

---

## 阶段 5：Prompt 与策略层

- [ ] 查看 prompts.py

看完要确认：

1. 意图分类标签和主链路是否一致。
2. SQL few-shot 是否与 flights_enriched 字段一致。
3. 搜索综合提示词是否要求给出处和不确定性说明。

---

## 阶段 6：记忆系统（你这个项目的加分项）

- [ ] 查看 agents/memory_agent.py
- [ ] 查看 memory/long_term_memory.py
- [ ] 查看 memory/memory_extractor.py
- [ ] 查看 data/init_memory_db.py

建议重点函数：

1. get_relevant_knowledge：向量召回 + 关键词兜底。
2. save_knowledge：知识落库与 embedding 存储。
3. _ensure_schema：老库自动迁移 embedding 字段。
4. extract_preferences_from_conversation：偏好提取格式。

看完要确认：

1. 当前长期记忆检索不是纯 LIKE，而是混合检索。
2. 老数据库无需手工重建也可兼容。
3. 自动提取何时触发、失败如何降级。

---

## 阶段 7：数据与执行服务

- [ ] 查看 data/init_flight_weather_db.py
- [ ] 查看 mcp_sql_server.py

看完要确认：

1. CSV 到 SQLite 的建表、索引、视图流程。
2. 默认数据库路径与回退策略。
3. MCP SQL 服务如何被主系统调用。

---

## 阶段 8：前端展示链路（最后看最清楚）

- [ ] 查看 static/index.html
- [ ] 查看 static/app.js
- [ ] 查看 static/style.css

看完要确认：

1. SSE 各事件类型如何映射到页面组件。
2. 质量卡与可信度横幅渲染逻辑在哪。
3. 图表配置是后端下发还是前端拼装。

---

## 阶段 9：评测与回归

- [ ] 查看 eval/minimal_dev_test_eval.py

看完要确认：

1. 当前评测题是否覆盖 SQL、分析、联合对比三类场景。
2. 评测 SQL 与真实字段是否一致。

---

## 建议的阅读节奏（3 天版）

- 第 1 天：阶段 1-3（主链路打通）
- 第 2 天：阶段 4-6（可讲性与记忆）
- 第 3 天：阶段 7-9（数据执行、前端、评测）

---

## 每阶段结束后的复盘模板

可复制以下格式写到你自己的笔记里：

- 我看了哪些文件：
- 这一段主流程是：
- 我确认了哪些事实：
- 还不确定的问题：
- 下一步要看的文件：

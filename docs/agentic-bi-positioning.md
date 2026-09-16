# 航空运营诊断 Agentic BI 定位说明

## 一句话定位

这是一个面向航空公司/机场运营团队的 Agentic BI 与运营诊断助手。它用可控型 Agent 工作流，把自然语言问题转换为只读 SQL、结构化分析、外部基准检索、证据裁决和运营建议。

## 不是什么

本项目不是普通乘客使用的实时航班助手，也不承担查票、改签、候机楼导航、实时登机口提醒等职责。

这些实时旅客场景通常要求：

- 实时航班动态 API
- 航司订单/票务系统
- 用户身份与行程上下文
- 分钟级推送和高可用告警

当前项目的价值点不是“实时性”，而是“复盘诊断”和“可解释决策支持”。

## 为什么历史数据合理

航空运营诊断天然依赖历史窗口：

- 趋势判断：需要按天、周、月观察延误率、取消率、改降率变化。
- 异常检测：需要最近窗口与上一窗口、同航线、同机场或同航司基线比较。
- 天气归因：需要足够样本区分天气影响和随机运行波动。
- 行业对标：需要明确指标口径和时间范围，不能把不同年份或地区的指标直接横向比较。

因此，系统会自动读取内部数据观测期，并在回答里标注“数据观测期”。当外部搜索结果的年份和内部数据年份不一致时，DebateAgent 会降低证据分或提示只能做方向性参考。

## 核心工作流

1. GuardrailAgent 前置拦截高风险请求。
2. PlannerAgent 生成任务计划，用于可观测和前端展示。
3. Intent Router 判断是否走 SQL、分析、搜索或联合路径。
4. SQLQueryAgent 生成只读 SQL，并通过 MCP SQL Server 执行。
5. SQL Reflection 在 SQL 执行失败后根据错误信息自动修复。
6. DataAnalysisAgent 生成洞察和 ECharts 配置。
7. WebSearchAgent 检索行业基准和公开资料。
8. DebateAgent 对内部 SQL 证据和外部证据做分数卡裁决。
9. CriticAgent 对最终答案进行审校。
10. SSE 输出 status、plan、trace、sql、quality、sources 和最终答案。

## 工程边界

项目刻意采用可控型工作流，而不是完全自主循环：

- 节点固定，便于解释、测试和排错。
- 工具权限明确，SQLTool 只允许 SELECT/WITH。
- SQL 重试有上限，避免死循环。
- 搜索证据不足时降级，不强行输出确定性结论。
- 前端展示 trace 和 quality，方便定位失败阶段。

## 后续接入实时 API 的方式

后续可以通过 Skill Registry 增加实时工具，而不是改写主流程：

- `FlightStatusTool`：接入 FlightAware、Aviationstack、航司/机场实时动态 API。
- `WeatherNowTool`：接入 METAR/TAF、NOAA、OpenWeather 等实时天气源。
- `AirportCapacityTool`：接入机场容量、跑道状态、流控通告。
- `AlertTool`：将异常诊断结果推送到 Slack、飞书、邮件或值班系统。

推荐路径：

1. 在 `config/skill_registry.yaml` 声明工具名称、输入、输出、权限。
2. 在 `tools/` 下增加薄封装，统一返回结构。
3. 在 MasterAgent 中新增明确意图，例如 `realtime_monitoring`。
4. 在评测报告中新增实时工具可用性、超时和降级用例。

这样可以保持项目主线稳定，同时自然扩展到更贴近生产的运行监控场景。

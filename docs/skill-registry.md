# 轻量 Skill Registry

`config/skill_registry.yaml` 声明8项能力的名称、说明、入口、权限标签和输入输出。`tools/skill_registry.py` 校验必填字段与重复名称；通过显式 `bind` 绑定 callable，`invoke` 校验权限集合和参数名。参数值范围由具体函数验证。

不根据 YAML 任意 import，不下载插件，不执行外部代码。权限标签是应用内策略检查，数据库真正的只读约束还在 `tools/sql_executor.py`。

| 能力 | 实现与调用 |
|---|---|
| SQLTool | 自然语言交给 SQLQueryAgent，经 MCP + Reflection；固定 SQL 可用共享只读执行器 |
| SearchTool | WebSearchAgent / Tavily，保留来源与原始摘录 |
| ChartTool | 从返回行确定性生成 ECharts；不让 LLM 捏造图表数值 |
| AnomalySkill | 等长窗口差异、有效样本阈值、探索性标记 |
| WeatherImpactSkill | 高降水/低降水/未知三组；相关性，非因果 |
| ReportSkill | 日报/周报的固定口径摘要 |
| RootCauseSkill | 同单位报告原因分钟排名、字段覆盖率、热点与独立天气比较 |
| OpsPlaybookSkill | 人工预案映射、责任团队、复核周期和假设情景 |

主工作流中四个运营 skill（Anomaly/Report/RootCause/OpsPlaybook）经 Registry 实际调度；SQL/Search/Chart 保留 Agent 内直接调用，WeatherImpact 由 RootCause 组合调用。注册并不意味着所有入口都已统一成一个动态插件接口。

```python
from tools.skill_registry import load_skill_registry
from tools.operational_skills import ReportSkill

registry = load_skill_registry('config/skill_registry.yaml')
registry.bind('ReportSkill', ReportSkill('data/demo_operations.db').generate)
result = registry.invoke('ReportSkill', ['database:read'], period='weekly')
print(result['report'])
```

`GET /api/skills` 用于能力清单展示。新实时工具需要实现 adapter、声明 schema、显式绑定，并补充超时/失效/口径测试；只加 YAML 不会自动接入主流程。
